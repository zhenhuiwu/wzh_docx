import asyncio

import pytest

from app.documents import _keep_draft_alive
from app.config import Settings


def test_heartbeat_waits_without_restarting_or_cancelling_generation():
    async def run():
        release = asyncio.Event()
        calls = []

        async def source():
            calls.append("started")
            try:
                await release.wait()
                yield "done\n"
            finally:
                calls.append("closed")

        stream = _keep_draft_alive(source(), interval=0.001)
        assert await anext(stream) == "\n"
        assert await anext(stream) == "\n"
        assert calls == ["started"]
        release.set()
        assert await anext(stream) == "done\n"
        assert [chunk async for chunk in stream] == []
        assert calls == ["started", "closed"]

    asyncio.run(run())


def test_disconnect_closes_pending_model_stream():
    async def run():
        closed = asyncio.Event()

        async def source():
            try:
                await asyncio.Event().wait()
                yield "unreachable"
            finally:
                closed.set()

        stream = _keep_draft_alive(source(), interval=0.001)
        assert await anext(stream) == "\n"
        await stream.aclose()
        assert closed.is_set()

    asyncio.run(run())


def test_stream_error_is_not_hidden_by_heartbeat():
    async def run():
        async def source():
            raise ValueError("provider failed")
            yield

        with pytest.raises(ValueError, match="provider failed"):
            await anext(_keep_draft_alive(source()))

    asyncio.run(run())


def test_model_wait_config_is_positive_and_can_exceed_old_deadline():
    assert Settings(_env_file=None).ai_read_timeout_seconds == 600
    assert Settings(_env_file=None, AI_READ_TIMEOUT_SECONDS=1800).ai_read_timeout_seconds == 1800
    with pytest.raises(ValueError):
        Settings(_env_file=None, AI_READ_TIMEOUT_SECONDS=0)
