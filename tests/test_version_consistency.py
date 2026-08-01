from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from pdfmd import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent_across_runtime_frontend_and_docs() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    frontend = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text("utf-8"))
    frontend_lock = json.loads(
        (PROJECT_ROOT / "frontend" / "package-lock.json").read_text("utf-8")
    )
    version = project["project"]["version"]

    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version)
    assert version == __version__
    assert version == frontend["version"]
    assert version == frontend_lock["version"]
    assert version == frontend_lock["packages"][""]["version"]

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    building = (PROJECT_ROOT / "docs" / "building.md").read_text(encoding="utf-8")
    troubleshooting = (PROJECT_ROOT / "docs" / "troubleshooting.md").read_text(
        encoding="utf-8"
    )
    assert readme.startswith(f"# PDF Markdown Studio {version}\n")
    assert f"PDF-Markdown-Studio-{version}-Windows-x64-Setup.exe" in readme
    assert f"PDF-Markdown-Studio-{version}-Windows-x64-Portable.zip" in readme
    mac_zip = f"PDF-Markdown-Studio-{version}-macOS-arm64.zip"
    mac_sums = f"PDF-Markdown-Studio-{version}-macOS-arm64-SHA256SUMS.txt"
    assert mac_zip in readme
    assert mac_zip in building
    assert mac_zip in troubleshooting
    assert mac_sums in readme
    assert mac_sums in building
    assert mac_sums in troubleshooting
    assert f"PDF-Markdown-Studio-{version}-Windows-x64-SHA256SUMS.txt" in building
    assert f"PDF-Markdown-Studio-{version}-Windows-x64-Portable.zip" in troubleshooting
