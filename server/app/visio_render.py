"""Render checked graph data as editable native Visio shapes, in a bounded worker."""

from __future__ import annotations

import logging

import json
import math
import os
from pathlib import Path
import subprocess
import sys

logger = logging.getLogger(__name__)


class VisioRenderError(RuntimeError):
    pass


def _layers(graph: dict) -> list[list[dict]]:
    """Keep declaration order within shortest-path layers; cycles stay bounded."""
    nodes = graph["nodes"]
    incoming = {node["id"]: 0 for node in nodes}
    outgoing = {node["id"]: [] for node in nodes}
    for edge in graph["edges"]:
        incoming[edge["target"]] += 1
        outgoing[edge["source"]].append(edge["target"])
    roots = [node["id"] for node in nodes if incoming[node["id"]] == 0]
    queue = roots or [nodes[0]["id"]]
    depths = {key: 0 for key in queue}
    for key in queue:
        for target in outgoing[key]:
            if target not in depths:
                depths[target] = depths[key] + 1
                queue.append(target)
    for node in nodes:
        if node["id"] not in depths:
            depths[node["id"]] = 0
    layers: list[list[dict]] = []
    for depth in range(max(depths.values()) + 1):
        row = [node for node in nodes if depths[node["id"]] == depth]
        # ponytail: at most four nodes per row; layered native routing can be replaced if dense diagrams need specialist layout.
        layers.extend([row[index:index + 4] for index in range(0, len(row), 4)])
    return layers


def _native_render(graph: dict, directory: Path) -> dict:
    import pythoncom
    import win32com.client
    import win32process

    pythoncom.CoInitialize()
    application = None
    try:
        application = win32com.client.DispatchEx("Visio.Application")
        application.Visible = False
        application.AlertResponse = 7  # No modal confirmation; this instance only handles newly generated data.
        # Visio uses OpenEx's visOpenMacrosDisabled flag rather than Word's AutomationSecurity property.
        application.EventsEnabled = False
        pid = win32process.GetWindowThreadProcessId(application.WindowHandle32)[1]
        (directory / "visio.pid").write_text(str(pid), encoding="ascii")
        document = application.Documents.Add("")
        page = document.Pages.Item(1)
        page.Name = graph["name"][:30]
        layers = _layers(graph)
        heights = {node["id"]: max(0.75, math.ceil(len(node["label"]) / 11) * 0.2 + 0.28)
                   if node["type"] != "decision" else max(1.05, (math.ceil(len(node["label"]) / 8) * 0.2 + 0.18) / 0.65)
                   for node in graph["nodes"]}
        row_heights = [max(heights[node["id"]] for node in row) for row in layers]
        columns = max(len(row) for row in layers)
        page_width = max(4.1, columns * 2.8 + 0.8)
        page_height = max(3.2, sum(row_heights) + (len(layers) - 1) * 0.75 + 0.9)
        page.PageSheet.CellsU("PageWidth").ResultIU = page_width
        page.PageSheet.CellsU("PageHeight").ResultIU = page_height
        shapes = {}
        for row_index, row in enumerate(layers):
            for column, node in enumerate(row):
                x = page_width / 2 + (column - (len(row) - 1) / 2) * 2.8
                y = page_height - 0.45 - sum(row_heights[:row_index]) - row_index * 0.75 - row_heights[row_index] / 2
                width, height = 2.15, heights[node["id"]]
                if node["type"] == "decision":
                    width = 2.3
                    points = [x, y + height / 2, x + width / 2, y,
                              x, y - height / 2, x - width / 2, y, x, y + height / 2]
                    shape = page.DrawPolyline(tuple(points), 0)
                    # Keep the decision text inside the diamond's central rectangle.
                    shape.CellsU("TxtWidth").FormulaU = "Width*0.65"
                    shape.CellsU("TxtHeight").FormulaU = "Height*0.65"
                elif node["type"] in {"start", "end"}:
                    shape = page.DrawOval(x - width / 2, y - height / 2, x + width / 2, y + height / 2)
                else:
                    shape = page.DrawRectangle(x - width / 2, y - height / 2, x + width / 2, y + height / 2)
                shape.NameU = "Node_" + node["id"]
                shape.Text = node["label"]
                for cell, formula in {
                    "Char.Font": 'FONT("Microsoft YaHei UI")', "Char.AsianFont": 'FONT("Microsoft YaHei UI")', "Char.Size": "10 pt",
                    "Char.Color": "RGB(31,41,47)", "Para.HorzAlign": "1", "VerticalAlign": "1",
                    "LineColor": "RGB(54,86,97)", "LineWeight": "1 pt",
                    "FillForegnd": "RGB(245,248,249)" if node["type"] == "process" else "RGB(255,255,255)",
                    "FillPattern": "1", "LeftMargin": "0.06 in", "RightMargin": "0.06 in",
                }.items():
                    shape.CellsU(cell).FormulaU = formula
                shapes[node["id"]] = shape
        for index, edge in enumerate(graph["edges"]):
            connector = page.Drop(application.ConnectorToolDataObject, 0, 0)
            connector.NameU = f"Connector_{index}"
            connector.CellsU("BeginX").GlueTo(shapes[edge["source"]].CellsU("PinY"))
            connector.CellsU("EndX").GlueTo(shapes[edge["target"]].CellsU("PinY"))
            connector.Text = edge["label"]
            for cell, formula in {"EndArrow": "13", "LineColor": "RGB(54,86,97)", "LineWeight": "1 pt",
                                  "Char.Font": 'FONT("Microsoft YaHei UI")', "Char.AsianFont": 'FONT("Microsoft YaHei UI")', "Char.Size": "9 pt",
                                  "ShapeRouteStyle": "16"}.items():
                connector.CellsU(cell).FormulaU = formula
            connector.SendToBack()
        file = directory / "diagram.vsdx"
        document.SaveAs(str(file))
        settings = application.Settings
        original_resolution = settings.GetRasterExportResolution()
        try:
            settings.SetRasterExportResolution(3, 200.0, 200.0, 0)
            page.Export(str(directory / "diagram.png"))
        finally:
            settings.SetRasterExportResolution(*original_resolution)
        document.Close()
        # Reopen through Visio itself. A screenshot or an XML package alone cannot prove editable connectors.
        reopened = application.Documents.OpenEx(str(file), 2 | 8 | 64 | 128 | 256)
        try:
            reopened_page = reopened.Pages.Item(1)
            reopened_shapes = [reopened_page.Shapes.Item(index) for index in range(1, reopened_page.Shapes.Count + 1)]
            node_count = sum(not bool(shape.OneD) for shape in reopened_shapes)
            connectors = [shape for shape in reopened_shapes if bool(shape.OneD)]
            labels = [str(shape.Text).strip() for shape in reopened_shapes if not bool(shape.OneD)]
            if node_count != len(graph["nodes"]) or len(connectors) != len(graph["edges"]):
                raise ValueError("原生对象数量不一致")
            if sorted(labels) != sorted(node["label"] for node in graph["nodes"]) or any(shape.Connects.Count != 2 for shape in connectors):
                raise ValueError("原生对象或连接未保存")
            result = {"nodes": node_count, "connectors": len(connectors), "verifiedEditable": True,
                      "pageWidth": page_width, "pageHeight": page_height}
        finally:
            reopened.Close()
        (directory / "render-meta.json").write_text(json.dumps(result), encoding="utf-8")
        return result
    finally:
        if application is not None:
            try:
                application.Quit()
            except Exception:
                logger.warning("Visio did not quit cleanly", exc_info=True)
        pythoncom.CoUninitialize()
        (directory / "visio.pid").unlink(missing_ok=True)


def render_visio(graph: dict, directory: Path, *, timeout: int = 90) -> dict:
    if sys.platform != "win32":
        raise VisioRenderError("Visio 生成功能需要 Windows 和本机 Microsoft Visio。")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    job = directory / "graph.json"
    job.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    command = ([sys.executable, "--render-visio", str(job)] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "app.visio_render", str(job)])
    try:
        process = subprocess.run(command, capture_output=True, timeout=timeout, env=environment,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        pid_file = directory / "visio.pid"
        if pid_file.is_file() and pid_file.read_text(encoding="ascii").strip().isdigit():
            subprocess.run(["taskkill", "/PID", pid_file.read_text(encoding="ascii").strip(), "/T", "/F"],
                           capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        raise VisioRenderError("Visio 生成超过 90 秒，请缩小图表后重试。") from None
    if process.returncode or not (directory / "diagram.vsdx").is_file() or not (directory / "diagram.png").is_file():
        raise VisioRenderError("Visio 生成失败，请确认本机 Microsoft Visio 已安装并能正常创建文件。")
    return json.loads((directory / "render-meta.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    graph_path = Path(sys.argv[1]).resolve()
    _native_render(json.loads(graph_path.read_text(encoding="utf-8")), graph_path.parent)
