"""Verify the configured effort reaches the actual Chat Completions request."""

import json

import httpx
from langchain_openai import ChatOpenAI
from pydantic import ValidationError
import pytest

from app.config import ai_reasoning_kwargs, get_settings


@pytest.mark.parametrize("configured,expected", [
    (None, ""), ("", ""), ("   ", ""), ("none", "none"),
    ("minimal", "minimal"), ("low", "low"), ("medium", "medium"),
    ("high", "high"), ("xhigh", "xhigh"), ("max", "max"),
    ("  HiGh  ", "high"), ("ultra", None),
])
def test_reasoning_effort_is_sent_as_configured(monkeypatch, tmp_path, configured, expected):
    monkeypatch.chdir(tmp_path)  # Do not load the configured provider or its key.
    if configured is None:
        monkeypatch.delenv("AI_REASONING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("AI_REASONING_EFFORT", configured)
    if expected is None:
        with pytest.raises(ValidationError, match="ai_reasoning_effort|AI_REASONING_EFFORT"):
            ai_reasoning_kwargs()
        return

    assert get_settings().ai_reasoning_effort == expected
    requests = []

    def respond(request):
        assert request.url.path == "/v1/chat/completions"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "chatcmpl-offline", "object": "chat.completion", "created": 0,
            "model": "offline-test", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(model="offline-test", api_key="offline-test",
                           base_url="https://offline.invalid/v1", http_client=client,
                           use_responses_api=False, max_retries=0, **ai_reasoning_kwargs())
        assert model.invoke("Check request configuration.").content == "ok"
    assert len(requests) == 1
    if expected:
        assert requests[0]["reasoning_effort"] == expected
    else:
        assert "reasoning_effort" not in requests[0]
