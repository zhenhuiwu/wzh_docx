import asyncio
import json

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from app import document_agent as agent


def provider_error(status=None):
    request = httpx.Request("POST", "https://offline.invalid/v1")
    if status is None:
        return APIConnectionError(request=request)
    return APIStatusError("offline", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize("failure", [provider_error(), provider_error(502), httpx.RemoteProtocolError("incomplete stream")])
def test_retry_current_module_preserves_finished_chapters(monkeypatch, failure):
    calls, closed = [], []

    async def module(**kwargs):
        index = kwargs["heading_index"]
        calls.append(index)
        try:
            yield "部分正文。"
            if calls == [0, 1]:
                raise failure
            yield f"完成正文{index}。"
        finally:
            closed.append(index)

    monkeypatch.setattr(agent, "_stream_module_text", module)

    async def run():
        return [item async for item in agent.stream_section_text(
            sequential_document=True, whole_document=True, full_markdown="# 甲\n\n旧文甲。\n\n# 乙\n\n旧文乙。",
            include_progress=True)]

    events = asyncio.run(run())
    assert calls == closed == [0, 1, 1]
    result = json.loads([item for item in events if isinstance(item, str)][-1])
    assert result == {"edits": [{"headingIndex": 0, "content": "完成正文0。"}, {"headingIndex": 1, "content": "完成正文1。"}]}
    assert len([item for item in events if isinstance(item, dict) and item["stage"] == "retry"]) == 1


@pytest.mark.parametrize("status,attempts", [(400, 1), (401, 1), (429, 2), (503, 2)])
def test_retry_is_bounded_and_configuration_errors_fail_immediately(monkeypatch, status, attempts):
    calls = []

    async def module(**kwargs):
        calls.append(True)
        raise provider_error(status)
        yield

    monkeypatch.setattr(agent, "_stream_module_text", module)

    async def run():
        with pytest.raises(APIStatusError):
            async for _ in agent.stream_section_text():
                pass

    asyncio.run(run())
    assert len(calls) == attempts


def test_stop_during_retry_does_not_restart_module(monkeypatch):
    closed = []

    async def module(**kwargs):
        try:
            raise provider_error()
            yield
        finally:
            closed.append(True)

    monkeypatch.setattr(agent, "_stream_module_text", module)

    async def run():
        stream = agent.stream_section_text(include_progress=True)
        assert (await anext(stream))["stage"] == "retry"
        await stream.aclose()

    asyncio.run(run())
    assert closed == [True]
