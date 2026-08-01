from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "macos-release.yml"
MODEL_SCRIPT = PROJECT_ROOT / "scripts" / "prepare_macos_models.py"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_macos_app.sh"
BUILDING_DOC_FILE = PROJECT_ROOT / "docs" / "building.md"
PROJECT_VERSION = tomllib.loads(
    (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_model_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prepare_macos_models", MODEL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_macos_release_files_exist() -> None:
    assert all(path.is_file() for path in (WORKFLOW_FILE, MODEL_SCRIPT, BUILD_SCRIPT))


def test_model_preparation_downloads_and_pins_expected_refs(tmp_path: Path) -> None:
    module = _load_model_module()
    downloads: list[tuple[str, str]] = []

    def fake_download(*, repo_id: str, revision: str, cache_dir: Path) -> str:
        downloads.append((repo_id, revision))
        cache_name = "models--" + repo_id.replace("/", "--")
        snapshot = Path(cache_dir) / cache_name / "snapshots" / revision
        snapshot.mkdir(parents=True)
        (snapshot / "model.bin").write_bytes(b"model")
        return str(snapshot)

    module.prepare_models(tmp_path / "huggingface", downloader=fake_download)

    assert downloads == [
        (spec.repo_id, spec.revision) for spec in module.MODEL_SPECS
    ]
    for spec in module.MODEL_SPECS:
        ref_file = (
            tmp_path
            / "huggingface"
            / "hub"
            / spec.cache_name
            / "refs"
            / spec.ref_name
        )
        assert ref_file.read_text(encoding="ascii").strip() == spec.revision


def test_macos_workflow_builds_and_publishes_verified_arm64_assets() -> None:
    workflow = _read(WORKFLOW_FILE)

    assert "workflow_dispatch:" in workflow
    assert "create_release:" in workflow
    assert "runs-on: macos-14" in workflow
    assert 'architecture: arm64' in workflow
    assert 'python-version: "3.12"' in workflow
    assert 'node-version: "22"' in workflow
    assert "[desktop-release]" in workflow
    assert "[macos-release]" in workflow
    assert "group: desktop-release-${{ github.ref }}" in workflow
    assert "npm --prefix frontend ci" in workflow
    assert "npm --prefix frontend run build -- --mode desktop" in workflow
    assert '"docling==2.114.0"' in workflow
    assert "python -m pytest -q --timeout=90" in workflow
    assert "python -m ruff check src tests scripts" in workflow
    assert "prepare_macos_models.py" in workflow
    assert "PDFMD_DESKTOP_EDITION: full" in workflow
    assert 'PDFMD_BUNDLE_MODELS: "1"' in workflow
    assert 'PDFMD_DESKTOP_REUSE_SOURCE_ENV: "1"' in workflow
    assert "PDFMD_MODEL_CACHE_SOURCE: ${{ runner.temp }}/pdfmd-huggingface" in workflow
    assert "make macos-app" in workflow
    assert "does not match project version" in workflow
    assert "2147483648" in workflow
    assert "/usr/bin/lipo -archs" in workflow
    assert "/usr/bin/codesign --verify --deep --strict" in workflow
    assert "/api/health" in workflow
    assert "shasum -a 256" in workflow
    assert "gh release create" in workflow
    assert "gh release upload" in workflow
    assert "--clobber" in workflow

    job_environment = workflow.split("    env:\n", 1)[1].split("\n\n    steps:", 1)[0]
    assert "runner.temp" not in job_environment

    version_prefix = "PDF-Markdown-Studio-${{ steps.metadata.outputs.version }}"
    assert f"{version_prefix}-macOS-arm64.zip" in workflow
    assert f"{version_prefix}-macOS-arm64-SHA256SUMS.txt" in workflow


def test_macos_build_and_docs_match_release_asset_contract() -> None:
    build_script = _read(BUILD_SCRIPT)
    building = _read(BUILDING_DOC_FILE)
    zip_name = f"PDF-Markdown-Studio-{PROJECT_VERSION}-macOS-arm64.zip"
    sums_name = f"PDF-Markdown-Studio-{PROJECT_VERSION}-macOS-arm64-SHA256SUMS.txt"

    zip_contract = (
        'ZIP_PATH="$PROJECT_DIR/dist/PDF-Markdown-Studio-$VERSION-macOS-$ARCH.zip"'
    )
    assert zip_contract in build_script
    assert "/usr/bin/codesign --verify --deep --strict" in build_script
    assert "/usr/bin/ditto -c -k --sequesterRsrc --keepParent" in build_script
    assert zip_name in building
    assert sums_name in building
