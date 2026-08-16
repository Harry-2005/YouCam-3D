"""Atomically rebuild a pipeline result ZIP after artifact-only repairs."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("pipeline", type=Path)
parser.add_argument("--model", type=Path, required=True)
args = parser.parse_args()

result = args.pipeline / "result.zip"
temporary = args.pipeline / "result-fixed.zip"
backup = args.pipeline / "result-pre-artifact-fix.zip"
if result.is_file() and not backup.exists():
    shutil.copy2(result, backup)
with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    archive.write(args.model, "model/model.glb")
    report = args.pipeline / "hunyuan" / "report.json"
    if report.is_file():
        archive.write(report, "model/report.json")
temporary.replace(result)
print(result)
