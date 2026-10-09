"""Observe real browser state; AI answers never substitute for test assertions."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import struct
from threading import Thread
from uuid import uuid4
import zlib

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import app.test_browser_agent as module
from app.test_executor import MissingInput, _functional


_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>中性浏览器测试</title>
<body><main id="app">
<label for="username" style="position:absolute;left:40px;top:20px">用户名</label>
<input id="username" style="position:absolute;left:40px;top:45px;width:280px;height:40px">
<label for="password" style="position:absolute;left:40px;top:95px">密码</label>
<input id="password" type="password" style="position:absolute;left:40px;top:120px;width:280px;height:44px">
<button id="login" style="position:absolute;left:40px;top:190px">登录</button>
</main><script>
const app = document.getElementById('app');
document.getElementById('login').onclick = () => {
  if (document.getElementById('username').value !== 'qa' ||
      document.getElementById('password').value !== 'qa-password-private') {
    app.insertAdjacentHTML('beforeend', '<p role="alert">账号错误</p>');
    return;
  }
  app.innerHTML = '<label for="project">项目</label><select id="project">' +
    '<option>请选择</option><option>中性项目</option></select><p role="status" data-testid="status">未保存</p>';
  document.getElementById('project').onchange = () => {
    if (document.getElementById('project').value === '中性项目' && !document.getElementById('save')) {
      app.insertAdjacentHTML('beforeend', '<button id="save">保存</button>');
      document.getElementById('save').onclick = () => {
        document.querySelector('[data-testid="status"]').textContent = '已保存';
      };
    }
  };
};
</script></body></html>"""


@pytest.fixture
def neutral_service():
    class Handler(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            Handler.hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_PAGE.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def browser_case(expected="已保存"):
    return {
        "id": str(uuid4()), "kind": "functional", "title": "动态页面登录后保存项目",
        "expected": expected,
        "steps": [{"action": "open", "url": "/"},
                  {"action": "assert_text", "locator": {"by": "testid", "value": "status"},
                   "value": expected, "description": "保存结果与计划中的预期一致"}],
    }


def browser_task(url):
    return {"frontendUrl": url, "backendUrl": "", "executionMode": "agent",
            "testUsername": "qa", "testPassword": "qa-password-private",
            "dataScope": "本地中性页面中的临时登录表单与项目选择，无持久化业务数据"}


def test_fixed_steps_retains_real_screenshots_and_password_mask(neutral_service, tmp_path):
    url, _ = neutral_service
    task = browser_task(url)
    task["executionMode"] = "steps"
    case = {"id": str(uuid4()), "kind": "functional", "steps": [
        {"action": "open", "url": "/"},
        {"action": "fill", "locator": {"by": "label", "value": "用户名"}, "value": "{{username}}"},
        {"action": "fill", "locator": {"by": "label", "value": "密码"}, "value": "{{password}}"},
        {"action": "click", "locator": {"by": "role", "role": "button", "value": "登录"}},
        {"action": "select_option", "locator": {"by": "label", "value": "项目"}, "value": "中性项目"},
        {"action": "click", "locator": {"by": "role", "role": "button", "value": "保存"}},
        {"action": "assert_text", "locator": {"by": "role", "role": "status", "value": ""}, "value": "已保存"},
    ]}
    updates = []
    result = _functional(task, case, tmp_path, lambda: False, lambda observed: updates.append(deepcopy(observed)))
    assert result["status"] == "passed" and len(result["steps"]) == 7
    assert all((tmp_path / step["screenshot"]).is_file() for step in result["steps"])
    assert png_pixel(tmp_path / result["steps"][2]["screenshot"], 80, 140)[:3] == (128, 128, 128)
    assert all("reportScreenshot" not in step for step in result["steps"])
    assert [bool(step.get("focusRect")) for step in result["steps"]] == [False, True, True, False, True, False, True]
    assert len(list(tmp_path.glob("*.png"))) == len(result["steps"])
    assert "qa-password-private" not in json.dumps(updates, ensure_ascii=False)


@pytest.mark.parametrize("mode", ["steps", "agent"])
def test_open_waits_for_delayed_spa_first_screen_before_single_masked_capture(tmp_path, mode):
    page = """<!doctype html><meta charset="utf-8"><body><main id="app"></main><script>
        setTimeout(() => { document.getElementById('app').innerHTML = `
            <section style="position:absolute;left:40px;top:40px;width:480px;height:260px;background:rgb(17,91,149);color:white">
                <h1 data-testid="ready">延迟首屏已显示</h1></section>
            <input type="password" value="qa-password-private" style="position:absolute;left:600px;top:120px;width:280px;height:44px">`;
        }, 500);
        </script></body>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        task = browser_task(f"http://127.0.0.1:{server.server_port}")
        case = {"id": str(uuid4()), "steps": [{"action": "open", "url": "/"},
                {"action": "assert_text", "locator": {"by": "testid", "value": "ready"}, "value": "延迟首屏已显示"}]}
        if mode == "steps":
            result = _functional(task, case, tmp_path, lambda: False, lambda _: None)
        else:
            async def scenario():
                session = module.BrowserSession(task, case, tmp_path, lambda: False, lambda _: None)
                try:
                    await session.start()
                    return await session.finish()
                finally:
                    await session.close()
            result = asyncio.run(scenario())
        assert result["status"] == "passed", result
        first_frame = tmp_path / result["steps"][0]["screenshot"]
        assert png_pixel(first_frame, 100, 180)[:3] == (17, 91, 149)
        assert png_pixel(first_frame, 640, 140)[:3] == (128, 128, 128)
        assert len(list(tmp_path.glob("*.png"))) == len(result["steps"]) == 2
        assert "focusRect" not in result["steps"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("behavior,expected", [("websocket", "failed"), ("popup", "failed")])
def test_fixed_steps_blocks_outside_websocket_and_popup_requests(tmp_path, behavior, expected):
    class Outside(BaseHTTPRequestHandler):
        hits = 0
        def do_GET(self):
            type(self).hits += 1
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_):
            pass
    outside = ThreadingHTTPServer(("127.0.0.1", 0), Outside)
    outside.daemon_threads = True
    outside_thread = Thread(target=outside.serve_forever, daemon=True)
    outside_thread.start()
    external = f"127.0.0.1:{outside.server_port}"
    markup = (f'<p data-testid="state">ready</p><script>new WebSocket("ws://{external}/ws")</script>' if behavior == "websocket"
              else f'<p data-testid="state">ready</p><button onclick="window.open(\'http://{external}/popup\')">popup</button>')
    class Frontend(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(markup.encode())
        def log_message(self, *_):
            pass
    frontend = ThreadingHTTPServer(("127.0.0.1", 0), Frontend)
    frontend.daemon_threads = True
    frontend_thread = Thread(target=frontend.serve_forever, daemon=True)
    frontend_thread.start()
    try:
        steps = [{"action": "open", "url": "/"}]
        if behavior == "popup":
            steps.append({"action": "click", "locator": {"by": "role", "role": "button", "value": "popup"}})
        steps.append({"action": "assert_text", "locator": {"by": "testid", "value": "state"}, "value": "ready"})
        result = _functional({"frontendUrl": f"http://127.0.0.1:{frontend.server_port}", "dataScope": "neutral fixture only"},
                             {"id": str(uuid4()), "steps": steps}, tmp_path, lambda: False, lambda _: None)
        assert result["status"] == expected
        assert Outside.hits == 0
        assert "范围" in result["error"] or "新窗口" in result["error"]
    finally:
        frontend.shutdown()
        frontend.server_close()
        outside.shutdown()
        outside.server_close()


def ref(snapshot, name):
    matches = [item["ref"] for item in snapshot["refs"] if item.get("name") == name]
    assert len(matches) == 1, snapshot
    return matches[0]


def png_pixel(path: Path, x: int, y: int):
    """Decode a Chromium PNG pixel without adding an image dependency to the server."""
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    cursor, compressed, header = 8, bytearray(), None
    while cursor < len(raw):
        length = struct.unpack(">I", raw[cursor:cursor + 4])[0]
        kind = raw[cursor + 4:cursor + 8]
        payload = raw[cursor + 8:cursor + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        cursor += length + 12
    width, height, depth, color, _, _, interlace = header
    assert (width, height, depth, interlace) == (1920, 1080, 8, 0)
    channels = {2: 3, 6: 4}[color]
    stride = width * channels
    rows = zlib.decompress(compressed)
    previous = bytearray(stride)
    for row_index in range(y + 1):
        offset = row_index * (stride + 1)
        filter_kind = rows[offset]
        row = bytearray(rows[offset + 1:offset + 1 + stride])
        for i in range(stride):
            a, b = row[i - channels] if i >= channels else 0, previous[i]
            c = previous[i - channels] if i >= channels else 0
            if filter_kind == 1:
                row[i] = (row[i] + a) & 255
            elif filter_kind == 2:
                row[i] = (row[i] + b) & 255
            elif filter_kind == 3:
                row[i] = (row[i] + ((a + b) // 2)) & 255
            elif filter_kind == 4:
                prediction = a + b - c
                da, db, dc = abs(prediction - a), abs(prediction - b), abs(prediction - c)
                predictor = a if da <= db and da <= dc else b if db <= dc else c
                row[i] = (row[i] + predictor) & 255
            else:
                assert filter_kind == 0
        previous = row
    return tuple(previous[x * channels:x * channels + 3])


class FeedbackModel(FakeMessagesListChatModel):
    """Choose only refs received from the preceding real browser observation."""

    stage: int = 0
    snapshots: list[dict] = Field(default_factory=list)
    observed_inputs: list = Field(default_factory=list)
    tool_grants: list[list[str]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        names = [item.name if hasattr(item, "name") else item["function"]["name"] for item in tools]
        return self.bind(test_tools=names)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed_inputs.append(messages)
        self.tool_grants.append(kwargs.get("test_tools", []))
        last = messages[-1]
        if isinstance(last, ToolMessage) and last.name == "browser_observe":
            self.snapshots.append(json.loads(last.content))
        if isinstance(last, ToolMessage) and last.name == "browser_act":
            assert "error" not in json.loads(last.content), last.content
        actions = {
            0: ("read_file", {"file_path": "/skills/browser/SKILL.md"}),
            2: ("browser_assert", {"index": 1}),
            3: ("browser_act", {"action": "fill", "ref": lambda: ref(self.snapshots[-1], "用户名"), "value": "{{username}}"}),
            5: ("browser_act", {"action": "fill", "ref": lambda: ref(self.snapshots[-1], "密码"), "value": "{{password}}"}),
            7: ("browser_act", {"action": "click", "ref": lambda: ref(self.snapshots[-1], "登录")}),
            9: ("browser_act", {"action": "select_option", "ref": lambda: ref(self.snapshots[-1], "项目"), "value": "中性项目"}),
            11: ("browser_act", {"action": "click", "ref": lambda: ref(self.snapshots[-1], "保存")}),
            13: ("finish_case", {}),
        }
        if self.stage > 13:
            answer = AIMessage(content="实际核对已完成。")
        else:
            name, arguments = actions.get(self.stage, ("browser_observe", {}))
            arguments = {key: value() if callable(value) else value for key, value in arguments.items()}
            answer = AIMessage(content="", tool_calls=[{"name": name, "args": arguments,
                                                        "id": str(uuid4()), "type": "tool_call"}])
        self.stage += 1
        return ChatResult(generations=[ChatGeneration(message=answer)])


@pytest.fixture
def local_skill(tmp_path, monkeypatch):
    # Testing the browser flow must not require any project document or business record.
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: browser-test\ndescription: Observe and operate the test browser.\n---\n"
                     "Observe before each action and verify immutable expected results.\n", encoding="utf-8")
    monkeypatch.setattr(module, "SKILL_FILE", skill)


def test_agent_uses_live_dynamic_controls_and_keeps_password_out_of_evidence(neutral_service, tmp_path, local_skill):
    url, _ = neutral_service
    task, case, model = browser_task(url), browser_case(), FeedbackModel(responses=[])
    case["steps"].insert(1, {"action": "assert_enabled", "locator": {"by": "role", "role": "button", "value": "登录"}})
    recorded = []
    result = asyncio.run(module._functional_agent_async(task, case, tmp_path, lambda: False,
                                                       lambda state: recorded.append(deepcopy(state)), model=model))
    assert result["status"] == "passed", result
    assert [step["action"] for step in result["steps"]] == [
        "open", "assert_enabled", "fill", "fill", "click", "select_option", "click", "assert_text"]
    # Login has disappeared by the last page; its earlier fixed assertion stays verified.
    assert result["steps"][1]["assertionIndex"] == 1
    assert set(item["name"] for item in model.snapshots[0]["refs"]) == {"用户名", "密码", "登录"}
    assert set(item["name"] for item in model.snapshots[3]["refs"]) == {"项目"}
    assert set(item["name"] for item in model.snapshots[4]["refs"]) == {"项目", "保存"}
    assert "已保存" in model.snapshots[-1]["text"]
    assert all((tmp_path / step["screenshot"]).is_file() for step in result["steps"])
    # Login removes its button. Keep one complete frame per operation rather
    # than a second live crop of the page after the transition.
    assert all("reportScreenshot" not in step for step in result["steps"])
    assert [bool(step.get("focusRect")) for step in result["steps"]] == [False, True, True, True, False, True, False, True]
    assert len(list(tmp_path.glob("*.png"))) == len(result["steps"])
    assert all(struct.unpack(">II", (tmp_path / step["screenshot"]).read_bytes()[16:24]) == (1920, 1080)
               for step in result["steps"])
    assert png_pixel(tmp_path / result["steps"][3]["screenshot"], 80, 140) == (128, 128, 128)
    assert "qa-password-private" not in json.dumps(recorded + model.snapshots, ensure_ascii=False)
    assert "qa-password-private" not in str(model.observed_inputs)
    assert all(not set(names) & {"execute", "task", "write_file", "edit_file"} for names in model.tool_grants)


def test_focus_rect_is_discarded_when_url_changes_during_single_capture(tmp_path):
    class Focus:
        async def count(self):
            return 1
        async def is_visible(self):
            return True
        async def scroll_into_view_if_needed(self, **_):
            pass
        async def bounding_box(self, **_):
            return {"x": 40, "y": 45, "width": 280, "height": 40}

    class Page:
        url = "http://localhost:5173/login"
        def locator(self, _):
            return Focus()
        async def screenshot(self, **options):
            assert "clip" not in options and options["mask_color"] == "gray"
            self.url = "http://localhost:5173/projects"

    async def scenario():
        session = module.BrowserSession({}, {"id": str(uuid4()), "steps": []}, tmp_path, lambda: False, lambda _: None)
        session.page = Page()
        observation = {"action": "fill"}
        await session._screenshot(observation, Focus())
        assert "screenshot" in observation and "focusRect" not in observation
    asyncio.run(scenario())


async def complete_form(session):
    await session.act("fill", ref=ref(await session.observe(), "用户名"), value="{{username}}")
    await session.act("fill", ref=ref(await session.observe(), "密码"), value="{{password}}")
    await session.act("click", ref=ref(await session.observe(), "登录"))
    await session.act("select_option", ref=ref(await session.observe(), "项目"), value="中性项目")
    await session.act("click", ref=ref(await session.observe(), "保存"))


def test_stale_refs_are_rejected_and_original_wrong_assertion_still_fails(neutral_service, tmp_path):
    url, _ = neutral_service
    case = browser_case(expected="不存在的保存结果")

    async def scenario():
        session = module.BrowserSession(browser_task(url), case, tmp_path, lambda: False, lambda _: None)
        # An AI or caller cannot alter the assertion after the isolated session was created.
        case["steps"][-1]["value"] = "已保存"
        try:
            await session.start()
            first = await session.observe()
            await session.act("fill", ref=ref(first, "用户名"), value="{{username}}")
            stale = await session.act("click", ref=ref(first, "登录"))
            assert "过期" in stale["error"]
            assert len(session.observed["steps"]) == 2
            await complete_form(session)
            assert "已保存" in (await session.observe())["text"]
            result = await session.finish()
            assert result["status"] == "failed", result
            assert result["steps"][-1]["action"] == "assert_text"
            assert result["steps"][-1]["status"] == "failed"
            assert (tmp_path / result["steps"][-1]["screenshot"]).is_file()
        finally:
            await session.close()

    asyncio.run(scenario())


def test_outside_service_is_not_requested_and_cancel_stops_browser(neutral_service, tmp_path):
    url, handler = neutral_service
    outside = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    outside_thread = Thread(target=outside.serve_forever, daemon=True)
    outside_thread.start()

    async def scenario():
        session = module.BrowserSession(browser_task(url), browser_case(), tmp_path / "outside", lambda: False, lambda _: None)
        try:
            await session.start()
            before = handler.hits
            result = await session.act("open", url=f"http://127.0.0.1:{outside.server_port}/outside")
            assert result["status"] == "blocked"
            assert "服务" in result["step"]["actual"]
            assert handler.hits == before
        finally:
            await session.close()

        stop = [False]
        session = module.BrowserSession(browser_task(url), browser_case(), tmp_path / "cancel", lambda: stop[0], lambda _: None)
        try:
            await session.start()
            first = await session.observe()
            stop[0] = True
            with pytest.raises(MissingInput, match="停止"):
                await session.act("fill", ref=ref(first, "用户名"), value="{{username}}")
            assert session.observed["status"] == "stopped"
            assert len(session.observed["steps"]) == 1
        finally:
            await session.close()

    try:
        asyncio.run(scenario())
    finally:
        outside.shutdown()
        outside.server_close()
        outside_thread.join(timeout=2)


def test_cancel_interrupts_pending_model_call(neutral_service, tmp_path, local_skill):
    url, _ = neutral_service

    async def scenario():
        entered, interrupted = asyncio.Event(), asyncio.Event()

        class SlowModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

            async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                entered.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    interrupted.set()
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="尚未执行。"))])

        stopped = [False]
        runner = asyncio.create_task(module._functional_agent_async(
            browser_task(url), browser_case(), tmp_path, lambda: stopped[0], lambda _: None,
            model=SlowModel(responses=[])))
        await asyncio.wait_for(entered.wait(), timeout=5)
        stopped[0] = True
        result = await asyncio.wait_for(runner, timeout=3)
        assert result["status"] == "stopped"
        assert interrupted.is_set()
        assert [step["action"] for step in result["steps"]] == ["open"]

    asyncio.run(scenario())


@pytest.mark.parametrize("answer,reason", [
    (AIMessage(content="测试已通过。"), "没有完成"),
    (AIMessage(content="", tool_calls=[
        {"name": "browser_observe", "args": {}, "id": "observe", "type": "tool_call"},
        {"name": "browser_act", "args": {"action": "click", "ref": "guessed-ref"}, "id": "act", "type": "tool_call"},
    ]), "不能并行"),
])
def test_model_claim_or_parallel_calls_cannot_become_a_pass(neutral_service, tmp_path, local_skill, answer, reason):
    url, _ = neutral_service

    class StaticModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    result = asyncio.run(module._functional_agent_async(
        browser_task(url), browser_case(), tmp_path, lambda: False, lambda _: None,
        model=StaticModel(responses=[answer])))
    assert result["status"] == "blocked", result
    assert reason in result["error"]
    assert [step["action"] for step in result["steps"]] == ["open"]


@pytest.mark.parametrize("expected,status", [("已保存", "passed"), ("未保存", "failed")])
def test_anonymous_status_locates_by_role_and_checks_original_text(neutral_service, tmp_path, expected, status):
    from app.test_management import _valid_step

    url, _ = neutral_service
    task, case = browser_task(url), browser_case(expected)
    case["steps"][-1]["locator"] = {"by": "role", "role": "status", "value": ""}
    _valid_step(case["steps"][-1], "functional", task)

    async def scenario():
        session = module.BrowserSession(task, case, tmp_path, lambda: False, lambda _: None)
        try:
            await session.start()
            await complete_form(session)
            result = await session.finish()
            assert result["status"] == status, result
            assert result["steps"][-1]["status"] == status
            assert result["steps"][-1]["action"] == "assert_text"
        finally:
            await session.close()

    asyncio.run(scenario())


def test_anonymous_alert_uses_body_text_as_assertion_not_accessible_name(neutral_service, tmp_path):
    from app.test_management import _valid_step

    url, _ = neutral_service
    task, case = browser_task(url), browser_case("账号错误")
    case["steps"][-1]["locator"] = {"by": "role", "role": "alert", "value": ""}
    _valid_step(case["steps"][-1], "functional", task)

    async def scenario():
        session = module.BrowserSession(task, case, tmp_path, lambda: False, lambda _: None)
        try:
            await session.start()
            await session.act("fill", ref=ref(await session.observe(), "用户名"), value="{{username}}")
            await session.act("fill", ref=ref(await session.observe(), "密码"), value="incorrect-password")
            await session.act("click", ref=ref(await session.observe(), "登录"))
            result = await session.finish()
            assert result["status"] == "passed", result
            assert result["steps"][-1]["action"] == "assert_text"
        finally:
            await session.close()

    asyncio.run(scenario())


def test_empty_locator_value_is_only_valid_for_a_defined_role():
    from app.test_management import _valid_step

    for role in ("status", "alert"):
        _valid_step({"action": "assert_text", "locator": {"by": "role", "role": role, "value": ""},
                     "value": "待核对正文"}, "functional", {})
    for locator in ({"by": "text", "value": ""}, {"by": "label", "value": ""},
                    {"by": "testid", "value": ""}, {"by": "role", "value": ""},
                    {"by": "role", "role": "", "value": ""},
                    {"by": "role", "role": "status", "value": " "}):
        with pytest.raises(ValueError, match="定位"):
            _valid_step({"action": "assert_text", "locator": locator, "value": "待核对正文"}, "functional", {})

