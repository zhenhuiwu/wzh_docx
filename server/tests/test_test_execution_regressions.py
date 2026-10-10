"""Reject empty/unfinished results and retain truthful execution evidence."""

import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import struct
import time
from uuid import uuid4
from zipfile import ZipFile
import zlib

from docx import Document
import pytest

import shutil

from app.test_executor import MIN_NODE_MAJOR, MissingInput, _concurrency_async, _node_major, _unit, build_report, execute_run
from app.test_management import _interrupt_result


_NODE = shutil.which("node")
_NODE_MAJOR = _node_major(_NODE) if _NODE else 0


@pytest.mark.skipif(_NODE_MAJOR < MIN_NODE_MAJOR, reason=f"requires Node.js >= {MIN_NODE_MAJOR} (per-file test:summary)")
@pytest.mark.parametrize("code,selector,expected", [
    ("const add = (a,b) => a+b;", "", "blocked"),
    ("const test = require('node:test'); test.todo('pending');", "", "blocked"),
    ("const test = require('node:test'); test.skip('skipped',()=>{});", "", "blocked"),
    ("const test = require('node:test'); const assert=require('node:assert/strict'); test('addition',()=>assert.equal(1+2,3));", "", "passed"),
    ("const test = require('node:test'); const assert=require('node:assert/strict'); test('addition',()=>assert.equal(1+2,3)); test('other',()=>assert.equal(1,0));", "addition", "passed"),
    ("console.log('# tests 999\\n# skipped 0');", "", "blocked"),
    ("const test = require('node:test'); const assert=require('node:assert/strict'); test('addition',()=>assert.equal(1+2,3));", "missing", "blocked"),
    ("const test = require('node:test'); const assert=require('node:assert/strict'); test('addition',()=>assert.equal(1+2,0));", "", "failed"),
])
def test_node_requires_real_completed_tests(tmp_path, code, selector, expected):
    source, evidence = tmp_path / "source", tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "test.cjs").write_text(code, encoding="utf-8")
    case = {"id": str(uuid4()), "steps": [{"runner": "node", "root": "frontend", "testFile": "test.cjs", "selector": selector}]}
    observed = _unit({"frontendRoot": str(source)}, case, evidence, lambda: False, lambda _: None)
    assert observed["status"] == expected
    assert observed["steps"][0]["status"] == expected


@pytest.mark.skipif(not _NODE or _NODE_MAJOR >= MIN_NODE_MAJOR, reason="only meaningful on an older Node.js")
def test_old_node_is_reported_as_missing_input(tmp_path):
    source, evidence = tmp_path / "source", tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "test.cjs").write_text("require('node:test')('x', () => {});", encoding="utf-8")
    case = {"id": str(uuid4()), "steps": [{"runner": "node", "root": "frontend", "testFile": "test.cjs"}]}
    with pytest.raises(MissingInput, match="Node.js 22"):
        _unit({"frontendRoot": str(source)}, case, evidence, lambda: False, lambda _: None)


@pytest.mark.parametrize("body", [
    "add(1,2)\ndef test_empty():\n    pass\n",
    "def test_empty():\n    add(1,2)\n    assert True\n",
    "def test_empty():\n    add(1,2)\n    assert 1 == 1\n",
    "def test_empty():\n    add(1,2)\n",
])
def test_generated_python_cannot_count_import_calls_or_empty_checks_as_tests(tmp_path, body):
    source, evidence = tmp_path / "source", tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "calc.py").write_text("def add(a,b): return a+b\n", encoding="utf-8")
    case = {"id": str(uuid4()), "sourceReference": "backend calc.py add", "steps": [
        {"runner": "pytest", "root": "backend", "testCode": "from calc import add\n" + body},
    ]}
    observed = _unit({"backendRoot": str(source)}, case, evidence, lambda: False, lambda _: None)
    assert observed["status"] == "blocked"
    assert not list(evidence.glob("*.log"))


def test_generated_python_supports_real_package_entrypoints(tmp_path):
    source, evidence = tmp_path / "source", tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    package = source / "calcs"
    package.mkdir()
    (package / "__init__.py").write_text("def add(a,b): return a+b\n", encoding="utf-8")
    case = {"id": str(uuid4()), "sourceReference": "backend calcs/__init__.py add", "steps": [
        {"runner": "pytest", "root": "backend", "testCode": "from calcs import add\ndef test_add():\n    assert add(1,2)==3\n"},
    ]}
    assert _unit({"backendRoot": str(source)}, case, evidence, lambda: False, lambda _: None)["status"] == "passed"


def test_unfinished_executor_and_empty_run_are_never_completed(tmp_path, monkeypatch):
    monkeypatch.setattr("app.test_executor.run_directory", lambda _: tmp_path)
    monkeypatch.setattr("app.test_management.save_run_progress", lambda *_: None)
    monkeypatch.setattr("app.test_executor._functional", lambda *_: {"status": "running", "steps": []})
    case = {"id": str(uuid4()), "title": "unfinished", "kind": "functional", "steps": []}
    observed = execute_run({}, [case], str(uuid4()), lambda: False)
    assert observed["status"] == "failed" and observed["cases"][0]["status"] == "failed"
    assert execute_run({}, [], str(uuid4()), lambda: False)["status"] == "blocked"


def test_execution_keeps_waiting_cases_visible_and_stops_only_unfinished_work(tmp_path, monkeypatch):
    updates, executed = [], []
    stopped = [False]
    cases = [{"id": str(uuid4()), "title": title, "kind": "functional", "expected": "expected",
              "planReference": "plan", "sourceReference": "source"} for title in ("first", "second", "third")]
    monkeypatch.setattr("app.test_executor.run_directory", lambda _: tmp_path)
    monkeypatch.setattr("app.test_management.save_run_progress", lambda _, result: updates.append(deepcopy(result)))

    def functional(_task, case, _folder, _cancelled, progress):
        executed.append(case["title"])
        evidence = {"action": "assert_text", "status": "passed", "screenshot": case["id"] + ".png"}
        progress({"status": "running", "steps": [evidence]})
        if case["title"] == "second":
            stopped[0] = True
        return {"status": "stopped" if stopped[0] else "passed", "steps": [evidence]}

    monkeypatch.setattr("app.test_executor._functional", functional)
    result = execute_run({}, cases, str(uuid4()), lambda: stopped[0])
    assert executed == ["first", "second"]
    assert [case["status"] for case in updates[0]["cases"]] == ["queued"] * 3
    assert any([case["status"] for case in update["cases"]] == ["running", "queued", "queued"] for update in updates)
    assert any([case["status"] for case in update["cases"]] == ["passed", "running", "queued"] for update in updates)
    assert result["status"] == "stopped"
    assert [case["status"] for case in result["cases"]] == ["passed", "stopped", "stopped"]
    assert result["cases"][0]["steps"][0]["screenshot"] == cases[0]["id"] + ".png"
    assert result["cases"][1]["steps"][0]["status"] == "passed"
    assert result["cases"][2]["steps"] == [] and "未执行" in result["cases"][2]["error"]
    assert all(case["expected"] == "expected" and case["planReference"] == "plan" and case["sourceReference"] == "source" for case in result["cases"])


def test_concurrency_cancel_interrupts_inflight_request_and_retains_completed_samples():
    class Handler(BaseHTTPRequestHandler):
        requests = 0
        def do_GET(self):
            type(self).requests += 1
            if type(self).requests > 1:
                time.sleep(3)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"neutral")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async def check():
            stopped = [False]
            updates = []
            job = asyncio.create_task(_concurrency_async(
                {"backendUrl": f"http://127.0.0.1:{server.server_port}", "requests": 3, "concurrency": 1},
                {"url": "/ok"}, lambda: stopped[0], lambda observed: updates.append(deepcopy(observed)),
            ))
            while not updates:
                await asyncio.sleep(.01)
            started = time.monotonic()
            stopped[0] = True
            result = await asyncio.wait_for(job, timeout=1)
            assert time.monotonic() - started < 1
            assert result["status"] == "stopped"
            assert result["metrics"]["completed"] == 1 and result["metrics"]["failed"] == 0
            assert updates[0]["status"] == "running" and updates[0]["metrics"]["completed"] == 1
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()


def test_interrupt_preserves_finished_observations_and_ends_only_running_work():
    original = {"status": "running", "cases": [
        {"status": "passed", "steps": [{"status": "passed", "screenshot": "real.png"}]},
        {"status": "running", "steps": [{"status": "passed", "screenshot": "first.png"}, {"status": "running"}]},
    ]}
    result = _interrupt_result(original, "restart interrupted")
    assert result["status"] == "failed"
    assert result["cases"][0]["status"] == "passed"
    assert result["cases"][1]["status"] == "failed"
    assert result["cases"][1]["steps"][0]["screenshot"] == "first.png"
    assert result["cases"][1]["steps"][1]["status"] == "failed"


def test_report_uses_full_frame_instead_of_existing_legacy_crop_and_requires_complete_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("app.test_executor.run_directory", lambda _: tmp_path)
    def rgb_png(rgb):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\0" + bytes(rgb))) + chunk(b"IEND", b""))
    png, bad_crop = rgb_png((128, 128, 128)), rgb_png((255, 0, 255))
    (tmp_path / "real.png").write_bytes(png)
    (tmp_path / "legacy-detail.png").write_bytes(bad_crop)
    run = {"id": str(uuid4()), "status": "completed", "result": {"cases": [
        {"title": "neutral", "kind": "functional", "status": "passed", "steps": [
            {"action": "assert_text", "status": "passed", "screenshot": "real.png", "reportScreenshot": "legacy-detail.png"},
        ]},
    ]}}
    report = build_report({"name": "neutral"}, run)
    assert len(Document(report).inline_shapes) == 1
    with ZipFile(report) as package:
        media = [package.read(name) for name in package.namelist() if name.startswith("word/media/")]
        assert media == [png]  # The legacy pink crop exists but must not be embedded.
    (tmp_path / "real.png").unlink()
    with pytest.raises(MissingInput, match="截图证据缺失"):
        build_report({"name": "neutral"}, run)
    run["result"]["cases"][0]["steps"] = []
    with pytest.raises(MissingInput, match="截图证据缺失"):
        build_report({"name": "neutral"}, run)


def test_report_crops_display_from_complete_image_and_ignores_unsafe_regions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.test_executor.run_directory", lambda _: tmp_path)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1920, 1080, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress((b"\0" + b"\x80\x80\x80" * 1920) * 1080)) + chunk(b"IEND", b""))
    (tmp_path / "real.png").write_bytes(png)
    rect = {"x": 480, "y": 270, "width": 960, "height": 270}
    common = {"status": "passed", "screenshot": "real.png"}
    run = {"id": str(uuid4()), "status": "completed", "result": {"cases": [
        {"title": "neutral", "kind": "functional", "status": "passed", "steps": [
            {**common, "action": "assert_text", "focusRect": rect},
            {**common, "action": "click", "focusRect": rect},
            {**common, "action": "assert_text", "focusRect": {**rect, "x": 1920}},
            {**common, "action": "assert_value", "focusRect": {**rect, "width": float("nan")}},
        ]},
    ]}}
    report = build_report({"name": "neutral"}, run)
    shapes = Document(report).inline_shapes
    assert len(shapes) == 4
    crop = shapes[0]._inline.graphic.graphicData.pic.blipFill.find("{http://schemas.openxmlformats.org/drawingml/2006/main}srcRect")
    assert dict(crop.attrib) == {"l": "25000", "t": "25000", "r": "25000", "b": "50000"}
    assert shapes[0].height == round(shapes[0].width * 270 / 960)
    for shape in list(shapes)[1:]:
        assert shape._inline.graphic.graphicData.pic.blipFill.find("{http://schemas.openxmlformats.org/drawingml/2006/main}srcRect") is None
        assert abs(shape.height - round(shape.width * 1080 / 1920)) <= 1
    with ZipFile(report) as package:
        media = [package.read(name) for name in package.namelist() if name.startswith("word/media/")]
        assert media == [png]  # Display cropping must not rewrite or replace complete evidence pixels.
