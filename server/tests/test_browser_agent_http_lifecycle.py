"""Repeated browser runs must not reuse HTTP sockets from a closed event loop."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import app.test_browser_agent as module
from app.config import clear_settings_cache
from test_browser_agent import local_skill, neutral_service


def test_repeated_agent_runs_use_owned_keepalive_model_connections(neutral_service, local_skill, tmp_path, monkeypatch):
    class ModelHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        ports = []

        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            type(self).ports.append(self.client_address[1])
            last = request["messages"][-1]
            if last["role"] == "tool":
                call = next(call for message in reversed(request["messages"])
                            for call in message.get("tool_calls", []) if call["id"] == last["tool_call_id"])
                previous = call["function"]["name"]
            else:
                previous = ""
            name, args = {"read_file": ("browser_observe", {}), "browser_observe": ("finish_case", {})}.get(
                previous, ("read_file", {"file_path": "/skills/browser/SKILL.md"}))
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": str(uuid4()), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
            body = json.dumps({"id": str(uuid4()), "object": "chat.completion", "created": 1, "model": "offline-test",
                               "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("AI_MODEL", "offline-test")
    monkeypatch.setenv("AI_API_KEY", "offline-only")
    clear_settings_cache()
    url, _ = neutral_service
    task = {"frontendUrl": url, "backendUrl": "", "dataScope": "Only read the neutral login page."}
    case = {"id": str(uuid4()), "kind": "functional", "title": "Login form is available", "steps": [
        {"action": "assert_enabled", "locator": {"by": "role", "role": "button", "value": "登录"}}]}
    try:
        for index in range(2):
            folder = tmp_path / f"run-{index}"
            result = module.functional_agent(task, case, folder, lambda: False, lambda _: None)
            assert result["status"] == "passed", result
            assert [step["action"] for step in result["steps"]] == ["open", "assert_enabled"]
            assert all((folder / step["screenshot"]).is_file() for step in result["steps"])
        # Real HTTP/1.1 sockets are retained within a run and replaced across run loops.
        assert len(ModelHandler.ports) == 6
        assert len(set(ModelHandler.ports[:3])) == len(set(ModelHandler.ports[3:])) == 1
        assert ModelHandler.ports[0] != ModelHandler.ports[3]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        clear_settings_cache()
