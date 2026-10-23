from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# processors are loaded by name from a team's settings, so they are listed here rather than found by import
processors = [f"processors.{p.stem}" for p in Path("processors").glob("*.py") if p.stem != "__init__"]

a = Analysis(
    ["report_app.py"],
    datas=[("setup", "setup"), ("processors", "processors")] + collect_data_files("docx"),
    hiddenimports=["app", *processors],
)
pyz = PYZ(a.pure)
# a folder rather than one self-unpacking file, so it starts straight from disk
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="report-bot-app", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="report-bot-app")
