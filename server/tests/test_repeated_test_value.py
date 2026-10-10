"""Repeated boundary input is expanded once, identically for both browser modes."""

import asyncio
from uuid import uuid4

import pytest

from app.test_browser_agent import BrowserSession
from app.test_executor import MissingInput, _functional, _test_text, _value
from app.test_management import _valid_step
from test_browser_agent import neutral_service, ref  # noqa: F401 - pytest fixtures


def test_repeat_validation_and_existing_credentials():
    repeated = {"text": "测", "repeat": 1000}
    assert _test_text(repeated) == "测" * 1000
    assert _test_text("x" * 4001) == "x" * 4001
    assert _value({"testUsername": "qa", "testPassword": "private"}, "{{username}}/{{password}}") == "qa/private"
    for invalid in (None, {"text": "", "repeat": 1}, {"text": 1, "repeat": 1},
                    {"text": "测", "repeat": True}, {"text": "测", "repeat": 1.0},
                    {"text": "测", "repeat": 0}, {"text": "测", "repeat": -1},
                    {"text": "测", "repeat": 4001}, {"text": "测试", "repeat": 2001},
                    {"text": "测", "repeat": 1, "extra": "unexpected"}, {"text": "测"}):
        with pytest.raises(MissingInput):
            _test_text(invalid)
    for action in ("fill", "select_option", "assert_value", "assert_text"):
        step = {"action": action, "locator": {"by": "label", "value": "字段"}, "value": repeated}
        _valid_step(step, "functional", {})
        with pytest.raises(ValueError):
            _valid_step({**step, "value": "x" * 4001}, "functional", {})
    for action in ("press", "click", "assert_enabled", "open"):
        with pytest.raises(ValueError):
            _valid_step({"action": action, "locator": {"by": "label", "value": "字段"}, "value": repeated}, "functional", {})


@pytest.mark.parametrize("mode", ["steps", "agent"])
def test_browser_modes_fill_and_assert_exactly_1000_characters(neutral_service, tmp_path, mode):
    url, _ = neutral_service
    task = {"frontendUrl": url, "backendUrl": "", "dataScope": "中性临时页面的输入框，无业务数据"}
    repeated = {"text": "测", "repeat": 1000}
    case = {"id": str(uuid4()), "kind": "functional", "steps": [
        {"action": "open", "url": "/"},
        {"action": "fill", "locator": {"by": "label", "value": "用户名"}, "value": repeated},
        {"action": "assert_value", "locator": {"by": "label", "value": "用户名"}, "value": repeated},
    ]}
    for step in case["steps"]:
        _valid_step(step, "functional", task)
    if mode == "steps":
        result = _functional(task, case, tmp_path, lambda: False, lambda _: None)
    else:
        async def scenario():
            session = BrowserSession(task, case, tmp_path, lambda: False, lambda _: None)
            try:
                await session.start()
                snapshot = await session.observe()
                assert "error" not in await session.act("fill", ref=ref(snapshot, "用户名"), value=repeated)
                assert await session.page.get_by_label("用户名").input_value() == "测" * 1000
                await session.assert_original(2)
                return await session.finish()
            finally:
                await session.close()
        result = asyncio.run(scenario())
    assert result["status"] == "passed", result
    assert result["steps"][-1]["action"] == "assert_value"
    assert all((tmp_path / step["screenshot"]).is_file() for step in result["steps"])
