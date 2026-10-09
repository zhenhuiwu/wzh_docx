"""Real subprocess and HTTP observations, including failed and unexecuted cases."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from app.test_executor import MissingInput, _concurrency_async, _source, _target, _unit


def test_real_execution_and_boundaries(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calculations.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    folder = tmp_path / "evidence"
    folder.mkdir()
    task = {"backendRoot": str(source), "testPassword": "qa-secret"}
    case = {"id": str(uuid4()), "sourceReference": "backend calculations.py add", "steps": [{"runner": "pytest", "root": "backend", "testCode": "from calculations import add\ndef test_add():\n    assert add(2, 3) == 5\n"}]}
    passed = _unit(task, case, folder, lambda: False, lambda _: None)
    assert passed["status"] == "passed" and "1 passed" in passed["log"]
    case["steps"][0]["testCode"] = "from calculations import add\ndef test_add():\n    assert add(2, 3) == 0\n"
    failed = _unit(task, case, folder, lambda: False, lambda _: None)
    assert failed["status"] == "failed" and "1 failed" in failed["log"]
    case["steps"][0]["testCode"] = "from calculations import add\ndef test_skip():\n    import pytest\n    add(1,2)\n    pytest.skip('unavailable')\n"
    assert _unit(task, case, folder, lambda: False, lambda _: None)["status"] == "blocked"
    assert _unit(task, case, folder, lambda: True, lambda _: None)["status"] == "stopped"
    for operation in (lambda: _source(task, "backend", "../outside.py"), lambda: _target({"backendUrl": "http://localhost:8001"}, "http://example.org")):
        try:
            operation()
        except MissingInput:
            pass
        else:
            raise AssertionError("Boundary accepted an outside target")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/ok" else 503)
            self.end_headers()
            self.wfile.write(b"test")
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        task.update(backendUrl=f"http://127.0.0.1:{server.server_port}", concurrency=3, requests=9)
        good = asyncio.run(_concurrency_async(task, {"url": "/ok", "maxLatencyMs": 5000}, lambda: False))
        assert good["status"] == "passed" and good["metrics"]["completed"] == 9 and good["metrics"]["failed"] == 0
        bad = asyncio.run(_concurrency_async(task, {"url": "/bad"}, lambda: False))
        assert bad["status"] == "failed" and bad["metrics"]["failed"] == 9
    finally:
        server.shutdown()
        server.server_close()
