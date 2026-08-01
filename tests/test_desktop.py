from __future__ import annotations

import plistlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdfmd import desktop_server
from scripts.build_icns import ICON_MEMBERS, PNG_SIGNATURE, build_icns


class _FakeListener:
    def __init__(self, port: int = 49152) -> None:
        self.port = port
        self.closed = False

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", self.port)

    def close(self) -> None:
        self.closed = True


def test_desktop_server_publishes_port_and_runs_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    port_file = tmp_path / "runtime" / "server.port"
    listener = _FakeListener()
    settings = object()
    app = object()
    observed: dict[str, object] = {}

    monkeypatch.setattr(desktop_server, "_bind_loopback_socket", lambda: listener)
    monkeypatch.setattr(
        desktop_server.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )
    monkeypatch.setattr(
        desktop_server,
        "create_app",
        lambda actual_settings: app
        if actual_settings is settings
        else pytest.fail("create_app received unexpected settings"),
    )

    def fake_config(actual_app: object, **kwargs: object) -> SimpleNamespace:
        observed["config_app"] = actual_app
        observed["config_kwargs"] = kwargs
        return SimpleNamespace(app=actual_app, **kwargs)

    class FakeServer:
        def __init__(self, config: SimpleNamespace) -> None:
            observed["server_config"] = config

        def run(self, *, sockets: list[object]) -> None:
            observed["sockets"] = sockets
            observed["published_port"] = port_file.read_text(encoding="ascii")

    monkeypatch.setattr(desktop_server.uvicorn, "Config", fake_config)
    monkeypatch.setattr(desktop_server.uvicorn, "Server", FakeServer)

    desktop_server.run(["--port-file", str(port_file)])

    assert observed["config_app"] is app
    assert observed["config_kwargs"] == {
        "host": "127.0.0.1",
        "port": listener.port,
        "log_level": "info",
        "access_log": False,
    }
    assert observed["server_config"].app is app
    assert observed["sockets"] == [listener]
    assert observed["published_port"] == str(listener.port)
    assert listener.closed is True
    assert not port_file.exists()
    assert not list(port_file.parent.glob(f".{port_file.name}.*.tmp"))


def test_desktop_server_cleans_port_files_when_uvicorn_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    port_file = tmp_path / "server.port"
    listener = _FakeListener(port=53921)

    monkeypatch.setattr(desktop_server, "_bind_loopback_socket", lambda: listener)
    monkeypatch.setattr(
        desktop_server.Settings,
        "from_env",
        classmethod(lambda cls: object()),
    )
    monkeypatch.setattr(desktop_server, "create_app", lambda settings: object())
    monkeypatch.setattr(
        desktop_server.uvicorn,
        "Config",
        lambda app, **kwargs: SimpleNamespace(app=app, **kwargs),
    )

    class FailingServer:
        def __init__(self, config: SimpleNamespace) -> None:
            self.config = config

        def run(self, *, sockets: list[object]) -> None:
            assert sockets == [listener]
            assert port_file.read_text(encoding="ascii") == str(listener.port)
            raise RuntimeError("simulated startup failure")

    monkeypatch.setattr(desktop_server.uvicorn, "Server", FailingServer)

    with pytest.raises(RuntimeError, match="simulated startup failure"):
        desktop_server.run(["--port-file", str(port_file)])

    assert listener.closed is True
    assert not port_file.exists()
    assert not list(tmp_path.glob(f".{port_file.name}.*.tmp"))


def test_macos_info_plist_declares_expected_desktop_metadata() -> None:
    project_root = Path(__file__).resolve().parents[1]
    with (project_root / "desktop" / "macos" / "Info.plist").open("rb") as stream:
        metadata = plistlib.load(stream)

    assert metadata["CFBundleDisplayName"] == "PDF Markdown Studio"
    assert metadata["CFBundleExecutable"] == "PDF Markdown Studio"
    assert metadata["CFBundleIdentifier"] == "com.pdfmarkdownstudio.desktop"
    assert metadata["CFBundlePackageType"] == "APPL"
    assert metadata["CFBundleShortVersionString"] == "@VERSION@"
    assert metadata["CFBundleVersion"] == "@VERSION@"
    assert metadata["LSMinimumSystemVersion"] == "14.0"
    assert metadata["LSApplicationCategoryType"] == "public.app-category.productivity"
    assert metadata["LSMultipleInstancesProhibited"] is True
    assert metadata["NSHighResolutionCapable"] is True
    assert metadata["NSPrincipalClass"] == "NSApplication"
    assert metadata["NSAppTransportSecurity"]["NSAllowsLocalNetworking"] is True


def test_macos_wrapper_supports_native_file_panels_and_offline_models() -> None:
    project_root = Path(__file__).resolve().parents[1]
    wrapper = (
        project_root / "desktop" / "macos" / "PDFMarkdownStudioApp.m"
    ).read_text(encoding="utf-8")
    build_script = (project_root / "scripts" / "build_macos_app.sh").read_text(
        encoding="utf-8"
    )

    assert "WKUIDelegate" in wrapper
    assert "runOpenPanelWithParameters" in wrapper
    assert "parameters.allowsMultipleSelection" in wrapper
    assert "panel.allowedContentTypes = @[ UTTypePDF ]" in wrapper
    assert "WKDownloadDelegate" in wrapper
    assert "NSSavePanel" in wrapper
    assert 'environment[@"HF_HUB_OFFLINE"] = @"1"' in wrapper
    assert 'environment[@"HF_HUB_CACHE"] = bundledHubCache' in wrapper
    assert 'environment[@"HUGGINGFACE_HUB_CACHE"] = bundledHubCache' in wrapper
    assert 'environment[@"TRANSFORMERS_CACHE"] = bundledHubCache' in wrapper
    assert 'environment[@"TRANSFORMERS_OFFLINE"] = @"1"' in wrapper
    assert '[environment removeObjectForKey:@"DOCLING_ARTIFACTS_PATH"]' in wrapper
    assert 'URLByAppendingPathComponent:@"model-cache"' in wrapper
    assert "-framework UniformTypeIdentifiers" in build_script
    assert "models--docling-project--docling-layout-heron" in build_script
    assert "models--docling-project--docling-models" in build_script
    assert "models--docling-project--CodeFormulaV2" in build_script
    assert "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8" in build_script
    assert "fc0f2d45e2218ea24bce5045f58a389aed16dc23" in build_script
    assert "ecedbe111d15c2dc60bfd4a823cbe80127b58af4" in build_script
    assert '"$SOURCE_PYTHON" -m pip wheel' in build_script
    assert "--no-deps" in build_script
    assert "-m hatchling" not in build_script
    assert 'echo "  应用版本：$VERSION"' in build_script
    assert 'echo "  发行类型：$EDITION"' in build_script


def test_macos_icon_generator_is_pixel_stable_on_retina_displays() -> None:
    project_root = Path(__file__).resolve().parents[1]
    generator = (project_root / "desktop" / "macos" / "generate_icon.m").read_text(
        encoding="utf-8"
    )

    assert "const NSInteger pixelSize = 1024" in generator
    assert "pixelsWide:pixelSize" in generator
    assert "pixelsHigh:pixelSize" in generator
    assert "graphicsContextWithBitmapImageRep:bitmap" in generator
    assert "lockFocus" not in generator
    assert "TIFFRepresentation" not in generator


def test_icns_builder_emits_valid_container_and_checks_dimensions(tmp_path: Path) -> None:
    iconset = tmp_path / "AppIcon.iconset"
    iconset.mkdir()
    for _, filename, size in ICON_MEMBERS:
        payload = PNG_SIGNATURE + struct.pack(">I4sII", 13, b"IHDR", size, size)
        (iconset / filename).write_bytes(payload)

    destination = tmp_path / "AppIcon.icns"
    build_icns(iconset, destination)
    payload = destination.read_bytes()

    assert payload[:4] == b"icns"
    assert struct.unpack(">I", payload[4:8])[0] == len(payload)
    assert all(chunk_type.encode("ascii") in payload for chunk_type, _, _ in ICON_MEMBERS)
