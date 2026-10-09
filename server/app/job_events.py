"""Ordered job events. Replaying a stream does not run the job again."""

from __future__ import annotations

from uuid import UUID

from app.models import format_timestamp


def append(
    connection,
    job_id: UUID,
    event_type: str,
    status: str,
    stage_name: str = "",
    text: str = "",
) -> int:
    row = connection.execute(
        "select last_sequence from jobs where id = %s for update",
        (job_id,),
    ).fetchone()
    sequence = int(row["last_sequence"]) + 1
    connection.execute(
        """
        insert into job_events (
          job_id, sequence, event_type, status, stage_name, text_body
        )
        values (%s, %s, %s, %s, %s, %s)
        """,
        (job_id, sequence, event_type, status, stage_name, text),
    )
    connection.execute(
        """
        update jobs
        set last_sequence = %s, updated_at = now()
        where id = %s
        """,
        (sequence, job_id),
    )
    return sequence


def since(connection, job_id: UUID, after: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        select job_id, sequence, event_type, status, stage_name, text_body, created_at
        from job_events
        where job_id = %s and sequence > %s
        order by sequence
        """,
        (job_id, after),
    ).fetchall()
    return [
        {
            "jobId": str(row["job_id"]),
            "sequence": row["sequence"],
            "type": row["event_type"],
            "status": row["status"],
            "stageName": row["stage_name"],
            "text": row["text_body"],
            "createdAt": format_timestamp(row["created_at"]),
        }
        for row in rows
    ]
