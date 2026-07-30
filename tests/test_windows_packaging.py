from __future__ import annotations

import importlib.util
import struct
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREPARE_SCRIPT = PROJECT_ROOT / "scripts" / "prepare_windows_assets.py"
SPEC_FILE = PROJECT_ROOT / "scripts" / "windows_app.spec"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_windows_app.ps1"
SMOKE_SCRIPT = PROJECT_ROOT / "scripts" / "smoke_test_windows.ps1"
INSTALLER_FILE = PROJECT_ROOT / "desktop" / "windows" / "installer.iss"
MANIFEST_FILE = PROJECT_ROOT / "desktop" / "windows" / "app.manifest"
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "windows-release.yml"
README_FILE = PROJECT_ROOT / "README.md"


def _load_prepare_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "prepare_windows_assets",
        PREPARE_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_windows_packaging_files_exist() -> None:
    expected = (
        PREPARE_SCRIPT,
        SPEC_FILE,
        BUILD_SCRIPT,
        SMOKE_SCRIPT,
        INSTALLER_FILE,
        MANIFEST_FILE,
        WORKFLOW_FILE,
    )
    assert all(path.is_file() for path in expected)
    for windows_script in (BUILD_SCRIPT, SMOKE_SCRIPT, INSTALLER_FILE):
        assert windows_script.read_bytes().startswith(b"\xef\xbb\xbf")


def test_asset_preparation_generates_valid_multisize_icon_and_version_info(
    tmp_path: Path,
) -> None:
    module = _load_prepare_module()

    result = module.prepare_assets(tmp_path / "windows-build", "0.9.0")

    assert set(result) == {"icon", "version_info", "version"}
    icon = Path(result["icon"])
    version_info = Path(result["version_info"])
    icon_bytes = icon.read_bytes()
    reserved, icon_type, image_count = struct.unpack("<HHH", icon_bytes[:6])
    assert (reserved, icon_type) == (0, 1)
    assert image_count == len(module.ICON_SIZES)
    for entry_index in range(image_count):
        entry_offset = 6 + entry_index * 16
        image_size, image_offset = struct.unpack(
            "<II",
            icon_bytes[entry_offset + 8 : entry_offset + 16],
        )
        image = icon_bytes[image_offset : image_offset + image_size]
        assert image.startswith(b"\x89PNG\r\n\x1a\n")

    version_text = version_info.read_text(encoding="utf-8")
    assert "filevers=(0, 9, 0, 0)" in version_text
    assert "StringStruct('ProductVersion', '0.9.0')" in version_text
    assert "StringStruct('OriginalFilename', 'PDF Markdown Studio.exe')" in version_text


@pytest.mark.parametrize("version", ["", "v0.9.0", "0.9.beta", "../../payload"])
def test_asset_preparation_rejects_invalid_versions(tmp_path: Path, version: str) -> None:
    module = _load_prepare_module()

    with pytest.raises(ValueError, match="invalid application version"):
        module.prepare_assets(tmp_path, version)


def test_pyinstaller_spec_is_windowed_onedir_and_uses_authoritative_entrypoint() -> None:
    spec_text = _read(SPEC_FILE)

    assert 'ENTRY_SCRIPT = PROJECT_ROOT / "scripts" / "windows_app_entry.py"' in spec_text
    assert 'name="PDF Markdown Studio"' in spec_text
    assert "console=False" in spec_text
    assert "COLLECT(" in spec_text
    assert "PDFMD_WINDOWS_EDITION" in spec_text
    assert 'EDITION == "full"' in spec_text
    assert '"docling"' in spec_text
    assert '"torch"' in spec_text
    assert '(str(FRONTEND_DIST), "pdfmd/web")' in spec_text
    assert "windows_launcher.py" not in spec_text
    assert "huggingface/hub" not in spec_text.lower()
    assert "snapshots" not in spec_text.lower()


def test_windows_manifest_is_non_elevated_dpi_aware_and_long_path_aware() -> None:
    root = ET.parse(MANIFEST_FILE).getroot()
    nodes = list(root.iter())

    execution_level = next(node for node in nodes if node.tag.endswith("requestedExecutionLevel"))
    long_path = next(node for node in nodes if node.tag.endswith("longPathAware"))
    dpi_awareness = next(node for node in nodes if node.tag.endswith("dpiAwareness"))
    supported_os = next(node for node in nodes if node.tag.endswith("supportedOS"))

    assert execution_level.attrib == {"level": "asInvoker", "uiAccess": "false"}
    assert long_path.text is not None and long_path.text.strip() == "true"
    assert dpi_awareness.text is not None and "PerMonitorV2" in dpi_awareness.text
    assert supported_os.attrib["Id"] == "{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"


def test_build_script_enforces_full_release_contract() -> None:
    script = _read(BUILD_SCRIPT)

    assert '[string]$Edition = "full"' in script
    assert '$pythonVersion -ne "3.12"' in script
    assert '"primary,desktop-build,windows-desktop"' in script
    assert "download.pytorch.org/whl/cpu" in script
    assert "LinkId=2124703" in script
    assert "Get-AuthenticodeSignature" in script
    assert "scripts\\smoke_test_windows.ps1" in script
    assert "Assert-ReleaseAssetSize" in script
    assert "$item.Length -ge $maxReleaseAssetBytes" in script
    assert "Full 版含 Docling 运行时，但不含 Hugging Face 离线模型" in script
    assert "PDF-Markdown-Studio-$version-Windows-x64-Portable.zip" in script
    assert "PDF-Markdown-Studio-$version-Windows-x64-Setup.exe" in script
    assert "PDF-Markdown-Studio-$version-Windows-x64-SHA256SUMS.txt" in script


def test_packaged_smoke_test_runs_real_http_conversion_and_validates_outputs() -> None:
    script = _read(SMOKE_SCRIPT)

    assert '"PDF Markdown Studio.exe"' in script
    assert "--smoke-test" in script
    assert "--smoke-output" in script
    assert "_internal\\docling" in script
    assert "Subsystem=$subsystem" in script
    assert "$subsystem -ne 2" in script
    assert 'Filter "*.md"' in script
    assert 'Filter "*-markdown.zip"' in script
    assert "$zipHeader[0] -ne 0x50" in script
    assert '$env:HF_HUB_OFFLINE = "1"' in script


def test_inno_installer_is_per_user_and_bootstraps_webview2() -> None:
    script = _read(INSTALLER_FILE)

    assert "PrivilegesRequired=lowest" in script
    assert r"DefaultDirName={localappdata}\Programs\PDF Markdown Studio" in script
    assert "MinVersion=10.0.17763" in script
    assert "ArchitecturesAllowed=x64compatible" in script
    assert (
        "OutputBaseFilename=PDF-Markdown-Studio-{#MyAppVersion}-Windows-x64-Setup"
        in script
    )
    assert r'Parameters: "/silent /install"' in script
    assert r'Filename: "{app}\{#WebView2Bootstrapper}"' in script
    assert r"AppMutex={#MyAppMutex}" in script
    assert r"%LOCALAPPDATA%\PDF Markdown Studio" in script
    assert r'Name: "{app}"' in script
    assert r'Name: "{localappdata}\PDF Markdown Studio"' not in script


def test_windows_workflow_can_publish_exact_release_assets() -> None:
    workflow = _read(WORKFLOW_FILE)

    assert "workflow_dispatch:" in workflow
    assert "create_release:" in workflow
    assert "runs-on: windows-2022" in workflow
    assert 'python-version: "3.12"' in workflow
    assert 'node-version: "22"' in workflow
    assert "npm --prefix frontend run build -- --mode desktop" in workflow
    assert workflow.index("npm --prefix frontend run build") < workflow.index(
        'python -m pip install -e ".[dev]"'
    )
    assert "build_windows_app.ps1 -Edition full" in workflow
    assert "gh release create" in workflow
    assert "gh release upload" in workflow
    assert "inputs.create_release" in workflow
    assert ".Length -ge 2GB" in workflow
    version_prefix = "PDF-Markdown-Studio-${{ steps.metadata.outputs.version }}"
    assert f"{version_prefix}-Windows-x64-Portable.zip" in workflow
    assert f"{version_prefix}-Windows-x64-Setup.exe" in workflow
    assert f"{version_prefix}-Windows-x64-SHA256SUMS.txt" in workflow


def test_readme_download_names_match_windows_release_outputs() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    readme = _read(README_FILE)

    assert f"PDF-Markdown-Studio-{version}-Windows-x64-Portable.zip" in readme
    assert f"PDF-Markdown-Studio-{version}-Windows-x64-Setup.exe" in readme
