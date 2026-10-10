"""Frozen workers must not relaunch the normal web application."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import test_executor, visio_render


@pytest.mark.skipif(sys.platform != "win32", reason="Visio worker dispatch is Windows-only (Microsoft Visio, CREATE_NO_WINDOW)")
def test_frozen_visio_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(visio_render.sys, "frozen", True, raising=False)
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        folder = Path(command[-1]).parent
        (folder / "diagram.vsdx").write_bytes(b"native-worker-output")
        (folder / "diagram.png").write_bytes(b"native-worker-output")
        (folder / "render-meta.json").write_text(json.dumps({"verifiedEditable": True}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visio_render.subprocess, "run", run)
    result = visio_render.render_visio({}, tmp_path / "visio")
    assert result["verifiedEditable"]
    assert commands[0] == [visio_render.sys.executable, "--render-visio", str(tmp_path / "visio" / "graph.json")]


def test_frozen_worker_requires_project_python(tmp_path, monkeypatch):
    monkeypatch.setattr(visio_render.sys, "frozen", True, raising=False)
    source, evidence = tmp_path / "source", tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "test_sample.py").write_text("def test_sample():\n    assert 1 == 1\n", encoding="utf-8")
    case = {"id": str(uuid4()), "steps": [{"runner": "pytest", "root": "backend", "testFile": "test_sample.py"}]}
    with pytest.raises(test_executor.MissingInput, match="目标项目的 .venv"):
        test_executor._unit({"backendRoot": str(source)}, case, evidence, lambda: False, lambda _: None)
    assert not list(evidence.iterdir())
