"""Frozen workers must not relaunch the normal web application."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import test_executor, visio_render


def test_frozen_visio_dispatch_and_missing_project_python(tmp_path, monkeypatch):
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

    source, evidence = tmp_path / "source", tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "test_sample.py").write_text("def test_sample():\n    assert 1 == 1\n", encoding="utf-8")
    case = {"id": str(uuid4()), "steps": [{"runner": "pytest", "root": "backend", "testFile": "test_sample.py"}]}
    with pytest.raises(test_executor.MissingInput, match="目标项目的 .venv"):
        test_executor._unit({"backendRoot": str(source)}, case, evidence, lambda: False, lambda _: None)
    assert len(commands) == 1
    assert not list(evidence.iterdir())
