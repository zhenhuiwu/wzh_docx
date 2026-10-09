"""Lease fencing and verification are not confused with pause or retry."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.identity import create_user
from app.worker import claim, enqueue, expire_lease, run_claimed, submit
import app.worker as worker

PASSWORD = "correct-horse-1"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("DATABASE_URL is required and has no default")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    from app.config import clear_settings_cache

    clear_settings_cache()
    migrate()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    clear_settings_cache()


def _admin(client: TestClient) -> tuple[str, str]:
    with connect() as connection:
        with connection.transaction():
            connection.execute("delete from job_steps")
            connection.execute("delete from job_events")
            connection.execute("delete from jobs")
            connection.execute(
                """
                delete from sessions
                where user_id in (select id from users where account_key = 'dev12-admin')
                """
            )
            connection.execute(
                """
                delete from user_roles
                where user_id in (select id from users where account_key = 'dev12-admin')
                """
            )
            connection.execute("delete from users where account_key = 'dev12-admin'")
    user_id = create_user(
        account="dev12-admin",
        name="任务管理员",
        password=PASSWORD,
        roles=["system_admin"],
    )
    csrf_response = client.get("/api/v1/auth/csrf")
    token = csrf_response.json()["data"]["csrfToken"]
    login = client.post(
        "/api/v1/auth/login",
        json={"account": "dev12-admin", "password": PASSWORD},
        headers={"X-CSRF-Token": token},
    )
    assert login.status_code == 200, login.text
    return str(user_id), login.json()["data"]["csrfToken"]


def _status(job_id) -> dict:
    with connect() as connection:
        row = connection.execute(
            """
            select status, fence, checkpoint, completed_steps, attempt, cancel_intent
            from jobs where id = %s
            """,
            (job_id,),
        ).fetchone()
    return dict(row)


def test_stale_lease_cannot_overwrite_checkpoint(client: TestClient) -> None:
    user_id, _csrf = _admin(client)
    from uuid import UUID

    job_id = enqueue(
        created_by=UUID(user_id),
        kind="check_consistency",
        target_kind="system",
        target_name="检查",
        step_plan="pure:one,pure:two",
    )
    first = claim("worker-a")
    assert first is not None
    assert submit(job_id, int(first["fence"]), "one", 1, "running") is True
    expire_lease(job_id)
    second = claim("worker-b")
    assert second is not None
    assert int(second["fence"]) != int(first["fence"])
    assert submit(job_id, int(first["fence"]), "stale", 0, "succeeded") is False
    assert run_claimed(second) == "succeeded"
    saved = _status(job_id)
    assert saved["status"] == "succeeded"
    assert saved["checkpoint"] == "done"
    assert saved["completed_steps"] == 2
    assert saved["attempt"] == 1
    with connect() as connection:
        steps = connection.execute(
            "select fence, checkpoint from job_steps where job_id = %s order by fence",
            (job_id,),
        ).fetchall()
    assert any(row["checkpoint"] == "one" for row in steps)
    stream = client.get(f"/api/v1/jobs/{job_id}/events?after=1")
    assert stream.status_code == 200
    assert "event: job" in stream.text
    again = client.get(f"/api/v1/jobs/{job_id}/events?after=1")
    assert _status(job_id)["attempt"] == 1
    assert again.text == stream.text


def test_verification_is_not_retried_or_cancelled_blindly(client: TestClient) -> None:
    user_id, csrf = _admin(client)
    from uuid import UUID

    job_id = enqueue(
        created_by=UUID(user_id),
        kind="export_document",
        target_kind="delivery",
        target_name="导出",
        step_plan="external:submit",
    )
    claimed = claim("worker-a")
    assert claimed is not None
    assert run_claimed(claimed) == "needs_verification"
    before = _status(job_id)
    listed = client.get(f"/api/v1/jobs/{job_id}")
    revision = listed.json()["data"]["revision"]
    resumed = client.post(
        f"/api/v1/jobs/{job_id}/resume",
        json={"expectedRevision": revision},
        headers={"X-CSRF-Token": csrf},
    )
    assert resumed.status_code == 409
    assert _status(job_id)["status"] == "needs_verification"
    assert _status(job_id)["attempt"] == before["attempt"]
    current = client.get(f"/api/v1/jobs/{job_id}").json()["data"]["revision"]
    cancelled = client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        json={"expectedRevision": current},
        headers={"X-CSRF-Token": csrf},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "needs_verification"
    assert bool(_status(job_id)["cancel_intent"]) is True
    verified = client.post(
        f"/api/v1/jobs/{job_id}/verify",
        json={
            "expectedRevision": cancelled.json()["data"]["revision"],
            "observedOutcome": "effect_applied",
            "evidenceIds": [],
            "reason": "外部结果已经生效",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert verified.status_code == 200
    assert verified.json()["data"]["status"] == "cancelled"


@pytest.mark.parametrize("job_count", [1, 8])
def test_concurrent_workers_claim_each_job_only_once(client, job_count):
    owner, _ = _admin(client)
    jobs = {enqueue(created_by=UUID(owner), kind="check_consistency", target_kind="system",
                    target_name="并发任务", step_plan="pure:one") for _ in range(job_count)}
    barrier = Barrier(8)

    def take(index):
        barrier.wait(timeout=5)
        return claim(f"worker-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [result for result in pool.map(take, range(8)) if result is not None]
    assert len(claimed) == job_count
    assert {result["id"] for result in claimed} == jobs
    assert all(result["fence"] == 1 and result["attempt"] == 1 for result in claimed)
    with connect() as connection:
        for job in jobs:
            row = connection.execute("SELECT lease_owner,lease_until FROM jobs WHERE id=%s", (job,)).fetchone()
            assert row["lease_owner"].startswith("worker-")
            assert 20 < (row["lease_until"] - datetime.now(timezone.utc)).total_seconds() <= 30
            events = connection.execute("SELECT sequence FROM job_events WHERE job_id=%s ORDER BY sequence", (job,)).fetchall()
            assert [event["sequence"] for event in events] == [1, 2]


def test_iso_utc_expired_lease_rejects_old_writes_and_recovers_checkpoint(client):
    owner, _ = _admin(client)
    job = enqueue(created_by=UUID(owner), kind="check_consistency", target_kind="system",
                  target_name="租约恢复", step_plan="pure:one,pure:two")
    first = claim("before-restart")
    assert first is not None and claim("too-early") is None
    assert submit(job, first["fence"], "one", 1, "running") is True
    with connect() as connection:
        connection.execute("UPDATE jobs SET lease_until=%s WHERE id=%s", (datetime.now(timezone.utc) - timedelta(seconds=2), job))
        connection.commit()
    assert submit(job, first["fence"], "expired", 0, "succeeded") is False
    assert run_claimed(first) == "stale"
    assert _status(job)["checkpoint"] == "one"
    recovered = claim("after-restart")
    assert recovered is not None and recovered["fence"] == first["fence"] + 1
    assert recovered["completed_steps"] == 1 and recovered["attempt"] == 1
    assert run_claimed(recovered) == "succeeded"
    with connect() as connection:
        finished = connection.execute("SELECT lease_until,lease_owner FROM jobs WHERE id=%s", (job,)).fetchone()
        assert finished["lease_until"] is None and finished["lease_owner"] is None


def test_stale_external_worker_cannot_report_verification_or_change_new_claim(client):
    owner, _ = _admin(client)
    job = enqueue(created_by=UUID(owner), kind="export_document", target_kind="delivery",
                  target_name="外部任务", step_plan="external:submit")
    first = claim("old-worker")
    assert first is not None
    expire_lease(job)
    second = claim("new-worker")
    assert second is not None
    assert run_claimed(first) == "stale"
    assert _status(job)["status"] == "running" and _status(job)["fence"] == second["fence"]
    assert run_claimed(second) == "needs_verification"
    assert claim("third-worker") is None


@pytest.mark.parametrize("action,requested,final", [("pause", "pause_requested", "paused"), ("cancel", "cancel_requested", "cancelled")])
def test_recovery_preserves_pending_control_after_worker_restart(client, action, requested, final):
    owner, csrf = _admin(client)
    job = enqueue(created_by=UUID(owner), kind="check_consistency", target_kind="system",
                  target_name="恢复控制", step_plan="pure:one")
    first = claim("crashed-worker")
    assert first is not None
    revision = client.get(f"/api/v1/jobs/{job}").json()["data"]["revision"]
    controlled = client.post(f"/api/v1/jobs/{job}/{action}", json={"expectedRevision": revision}, headers={"X-CSRF-Token": csrf})
    assert controlled.status_code == 200 and controlled.json()["data"]["status"] == requested
    expire_lease(job)
    recovered = claim("recovery-worker")
    assert recovered is not None and recovered["status"] == requested and recovered["attempt"] == 1
    assert run_claimed(recovered) == final
    assert _status(job)["status"] == final and _status(job)["completed_steps"] == 0
    assert submit(job, recovered["fence"], "resurrected", 1, "succeeded") is False
    if action == "pause":
        revision = client.get(f"/api/v1/jobs/{job}").json()["data"]["revision"]
        resumed = client.post(f"/api/v1/jobs/{job}/resume", json={"expectedRevision": revision}, headers={"X-CSRF-Token": csrf})
        assert resumed.status_code == 200 and resumed.json()["data"]["status"] == "queued"
        next_claim = claim("resumed-worker")
        assert next_claim is not None and next_claim["attempt"] == 2
        assert run_claimed(next_claim) == "succeeded"


@pytest.mark.parametrize("action,final", [("pause", "paused"), ("cancel", "cancelled")])
@pytest.mark.parametrize("step_plan,entry", [("pure:one", "submit"), ("external:send", "_mark")])
def test_control_arriving_during_a_step_cannot_be_overwritten(client, monkeypatch, action, final, step_plan, entry):
    owner, csrf = _admin(client)
    job = enqueue(created_by=UUID(owner), kind="check_consistency", target_kind="system",
                  target_name="执行中控制", step_plan=step_plan)
    claimed = claim("worker-a")
    assert claimed is not None
    original = getattr(worker, entry)
    injected = False

    def control_before_result(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            revision = client.get(f"/api/v1/jobs/{job}").json()["data"]["revision"]
            controlled = client.post(f"/api/v1/jobs/{job}/{action}", json={"expectedRevision": revision}, headers={"X-CSRF-Token": csrf})
            assert controlled.status_code == 200, controlled.text
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, entry, control_before_result)
    assert run_claimed(claimed) == final
    saved = _status(job)
    assert saved["status"] == final and saved["checkpoint"] == "" and saved["completed_steps"] == 0


def test_concurrent_control_requests_use_the_current_revision_only_once(client):
    owner, csrf = _admin(client)
    job = enqueue(created_by=UUID(owner), kind="check_consistency", target_kind="system",
                  target_name="控制竞争", step_plan="pure:one")
    claimed = claim("worker-a")
    assert claimed is not None
    revision = client.get(f"/api/v1/jobs/{job}").json()["data"]["revision"]
    barrier = Barrier(2)

    def control(action):
        barrier.wait(timeout=5)
        return client.post(f"/api/v1/jobs/{job}/{action}", json={"expectedRevision": revision}, headers={"X-CSRF-Token": csrf})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(control, ["pause", "cancel"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["error"]["code"] == "REVISION_CONFLICT"
    assert client.get(f"/api/v1/jobs/{job}").json()["data"]["revision"] == revision + 1
    final = "paused" if _status(job)["status"] == "pause_requested" else "cancelled"
    assert run_claimed(claimed) == final
