# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


root = Path(SPECPATH).parent
server = root / "server"
datas = [(str(root / "web" / "dist"), "web"),
         (str(root / "deploy" / ".build" / "tiktoken-cache"), "tiktoken-cache")]
for path in sorted((server / "migrations").glob("*.sql")):
    if path.stem.split("_", 1)[0].isdigit() and int(path.stem.split("_", 1)[0]) >= 100:
        datas.append((str(path), "migrations"))
for folder in (server / "workspaces" / "GJB438C-DocSkill" / "skills",
               server / "workspaces" / "browser-skills" / "agent-browser"):
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".docx"}:
            datas.append((str(path), str(path.parent.relative_to(server))))

# These packages load providers, serializers and Markdown extensions by name.
hiddenimports = [f"app.{path.stem}" for path in sorted((server / "app").glob("*.py"))
                 if path.stem != "__init__"]
for package in ("deepagents", "langchain", "langchain_core", "langchain_openai",
                "langgraph", "markdown.extensions", "tiktoken_ext"):
    hiddenimports += collect_submodules(package)
for package in ("deepagents", "langchain_core", "langgraph"):
    datas += collect_data_files(package)
for package in ("deepagents", "langchain-openai", "langgraph", "playwright", "tiktoken", "Markdown"):
    datas += copy_metadata(package, recursive=True)

a = Analysis([str(server / "desktop.py")], pathex=[str(server)],
             binaries=[], datas=datas, hiddenimports=sorted(set(hiddenimports)),
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="文档工作室",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="文档工作室")
