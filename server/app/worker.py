"""Single-database job worker. A stale lease cannot commit over a newer fence."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.db import connect
from app.job_events import append

_LEASE_SECONDS = 30


def enqueue(
    *,
    created_by: UUID,
    kind: str,
    target_kind: str,
    target_name: str,
    step_plan: str,
    project_id: UUID | None = None,
    target_id: UUID | None = None,
) -> UUID:
    job_id = uuid4()
    steps = _parse_plan(step_plan)
    with connect() as connection:
        with connection.transaction(immediate=True):
            connection.execute(
                """
                insert into jobs (
                  id, project_id, created_by, kind, status, target_kind, target_id,
                  target_name, total_steps, step_plan
                )
                values (%s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    project_id,
                    created_by,
                    kind,
                    target_kind,
                    target_id,
                    target_name,
                    len(steps),
                    step_plan,
                ),
            )
            append(connection, job_id, "status", "queued", "", "已排队")
    return job_id


def claim(worker_id: str) -> dict[str, object] | None:
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                """
                update jobs
                set fence = fence + 1,
                    lease_owner = %s,
                    lease_until = datetime('now', %s),
                    status = case when status = 'queued' then 'running' else status end,
                    attempt = case when status = 'queued' then attempt + 1 else attempt end,
                    started_at = coalesce(started_at, now()),
                    revision = revision + 1,
                    updated_at = now()
                where id = (
                  select id from jobs
                  where status in ('queued', 'running', 'pause_requested', 'cancel_requested')
                    and (lease_until is null or julianday(lease_until) <= julianday('now'))
                  order by created_at, id
                  limit 1
                )
                returning id, fence, checkpoint, completed_steps, total_steps, step_plan, attempt, status
                """,
                (worker_id, f"+{_LEASE_SECONDS} seconds"),
            ).fetchone()
            if row is None:
                return None
            append(connection, row["id"], "status", row["status"], row["checkpoint"], "已领取")
            return dict(row)


def expire_lease(job_id: UUID) -> None:
    with connect() as connection:
        with connection.transaction(immediate=True):
            connection.execute(
                "update jobs set lease_until = datetime('now', '-1 second') where id = %s",
                (job_id,),
            )


def submit(job_id: UUID, fence: int, checkpoint: str, completed: int, status: str) -> bool:
    """Commit a worker result only when the fence is still current."""

    with connect() as connection:
        with connection.transaction(immediate=True):
            current = connection.execute(
                """select fence, status,
                          lease_until is not null and julianday(lease_until) > julianday('now') as lease_valid
                   from jobs where id = %s""",
                (job_id,),
            ).fetchone()
            if current is None or int(current["fence"]) != fence:
                return False
            if current["status"] != "running" or not current["lease_valid"]:
                return False
            updated = connection.execute(
                """
                update jobs
                set checkpoint = %s,
                    completed_steps = %s,
                    status = %s,
                    lease_until = case when %s = 'running' then datetime('now', %s) else null end,
                    lease_owner = case when %s = 'running' then lease_owner else null end,
                    ended_at = case when %s in ('succeeded', 'failed', 'cancelled') then now() else ended_at end,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s and fence = %s and status = 'running'
                  and julianday(lease_until) > julianday('now')
                """,
                (checkpoint, completed, status, status, f"+{_LEASE_SECONDS} seconds", status, status, job_id, fence),
            )
            if updated.rowcount != 1:
                return False
            connection.execute(
                """
                insert into job_steps (job_id, step_index, name, status, fence, checkpoint)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (job_id, step_index, fence) do nothing
                """,
                (job_id, completed, checkpoint or "checkpoint", status, fence, checkpoint),
            )
            append(connection, job_id, "progress", status, checkpoint, checkpoint)
    return True


def run_claimed(claimed: dict[str, object]) -> str:
    """Advance one claim. Pure steps resume from the checkpoint. External steps wait."""

    job_id = claimed["id"]
    fence = int(claimed["fence"])
    steps = _parse_plan(str(claimed["step_plan"]))
    completed = int(claimed["completed_steps"])
    for index, (kind, name) in enumerate(steps):
        if index < completed:
            continue
        control = _control(job_id, fence)
        if control is not None:
            return control
        if kind == "external":
            if _mark(job_id, fence, "needs_verification", name, completed, "外部结果尚未核实"):
                return "needs_verification"
            return _control(job_id, fence) or "stale"
        completed = index + 1
        if not submit(job_id, fence, name, completed, "running"):
            return _control(job_id, fence) or "stale"
    if not submit(job_id, fence, "done", completed, "succeeded"):
        return _control(job_id, fence) or "stale"
    return "succeeded"


def _control(job_id: UUID, fence: int) -> str | None:
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                """select status, fence,
                          lease_until is not null and julianday(lease_until) > julianday('now') as lease_valid
                   from jobs where id = %s""",
                (job_id,),
            ).fetchone()
            if row is None or int(row["fence"]) != fence or not row["lease_valid"]:
                return "stale"
            if row["status"] == "pause_requested":
                connection.execute(
                    """
                    update jobs
                    set status = 'paused', lease_until = null, lease_owner = null,
                        revision = revision + 1, updated_at = now()
                    where id = %s and fence = %s
                    """,
                    (job_id, fence),
                )
                append(connection, job_id, "status", "paused", "", "已暂停")
                return "paused"
            if row["status"] == "cancel_requested":
                connection.execute(
                    """
                    update jobs
                    set status = 'cancelled', ended_at = now(), lease_until = null, lease_owner = null,
                        revision = revision + 1, updated_at = now()
                    where id = %s and fence = %s
                    """,
                    (job_id, fence),
                )
                append(connection, job_id, "status", "cancelled", "", "已取消")
                return "cancelled"
            if row["status"] != "running":
                return "stale"
    return None


def _mark(job_id: UUID, fence: int, status: str, stage: str, completed: int, reason: str) -> bool:
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                """select fence, status,
                          lease_until is not null and julianday(lease_until) > julianday('now') as lease_valid
                   from jobs where id = %s""",
                (job_id,),
            ).fetchone()
            if row is None or int(row["fence"]) != fence or row["status"] != "running" or not row["lease_valid"]:
                return False
            updated = connection.execute(
                """
                update jobs
                set status = %s, stage_name = %s, completed_steps = %s,
                    blocking_reason = %s, lease_until = null, lease_owner = null,
                    revision = revision + 1, updated_at = now()
                where id = %s and fence = %s and status = 'running'
                  and julianday(lease_until) > julianday('now')
                """,
                (status, stage, completed, reason, job_id, fence),
            )
            if updated.rowcount != 1:
                return False
            append(connection, job_id, "issue", status, stage, reason)
    return True


def _parse_plan(plan: str) -> list[tuple[str, str]]:
    steps: list[tuple[str, str]] = []
    for part in plan.split(","):
        kind, _, name = part.partition(":")
        steps.append((kind or "pure", name or "work"))
    return steps or [("pure", "work")]
