from __future__ import annotations

import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdfmd.windows_app import (
    EmbeddedUvicornServer,
    SingleInstanceLock,
    WindowsAppPaths,
    _loopback_origin,
    _start_webview,
    configure_file_logging,
    configure_windows_environment,
    ensure_webview2_runtime,
    find_bundled_webview2_installer,
    launch_desktop,
    run,
    startup_failure_page,
)


def test_file_logging_replaces_missing_windowed_streams(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_file = tmp_path / "logs" / "desktop.log"
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    state = configure_file_logging(log_file)

    assert sys.stdout is not None
    assert sys.stderr is not None
    sys.stdout.write("stdout fallback\n")
    sys.stderr.write("stderr fallback\n")
    state.close()

    assert sys.stdout is None
    assert sys.stderr is None
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    console_log = log_file.with_name("console.log").read_text(encoding="utf-8")
    assert "stdout fallback" in console_log
    assert "stderr fallback" in console_log


def test_windows_paths_and_environment_use_local_appdata(tmp_path: Path) -> None:
    local_appdata = tmp_path / "Local"
    paths = WindowsAppPaths.from_environment({"LOCALAPPDATA": str(local_appdata)})
    environ: dict[str, str] = {}

    paths.ensure_directories()
    configure_windows_environment(paths, environ)

    assert paths.root == local_appdata / "PDF Markdown Studio"
    assert paths.data_dir.is_dir()
    assert paths.cache_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.webview_dir.is_dir()
    assert environ["PDFMD_DATA_DIR"] == str(paths.data_dir)
    assert environ["HF_HOME"].startswith(str(paths.cache_dir))
    assert environ["DOCLING_CACHE_DIR"] == str(paths.cache_dir / "docling")
    assert environ["TORCH_HOME"] == str(paths.cache_dir / "torch")
    assert environ["XDG_CACHE_HOME"] == str(paths.cache_dir)
    assert environ["PADDLE_HOME"].startswith(str(paths.cache_dir))


def test_webview2_bootstrapper_is_found_and_checked_after_install(tmp_path: Path) -> None:
    installer = tmp_path / "webview2" / "MicrosoftEdgeWebview2Setup.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"bootstrapper")
    calls: list[list[str]] = []
    detections = iter((False, True))

    found = find_bundled_webview2_installer(search_roots=[tmp_path])
    installed = ensure_webview2_runtime(
        detector=lambda: next(detections),
        installer=found,
        runner=lambda command, **_: (
            calls.append(command) or subprocess.CompletedProcess(command, 0)
        ),
    )

    assert found == installer
    assert installed is True
    assert calls == [[str(installer), "/silent", "/install"]]


def test_webview2_runner_failure_is_reported_without_crashing(tmp_path: Path) -> None:
    installer = tmp_path / "MicrosoftEdgeWebview2Setup.exe"
    installer.write_bytes(b"bootstrapper")

    assert (
        ensure_webview2_runtime(
            detector=lambda: False,
            installer=installer,
            runner=lambda *_, **__: (_ for _ in ()).throw(PermissionError("blocked")),
            verification_timeout=0,
        )
        is False
    )


def test_launch_desktop_enables_downloads_and_stops_server(tmp_path: Path) -> None:
    paths = WindowsAppPaths.from_environment({"LOCALAPPDATA": str(tmp_path)})
    paths.ensure_directories()

    class FakeServer:
        def __init__(self, _settings):
            self.stopped = False

        def start(self) -> str:
            return "http://127.0.0.1:43123"

        def stop(self) -> None:
            self.stopped = True

    server: FakeServer | None = None

    def server_factory(settings):
        nonlocal server
        server = FakeServer(settings)
        return server

    webview = SimpleNamespace(settings={}, windows=[], starts=[])

    def create_window(title, **kwargs):
        webview.windows.append((title, kwargs))
        return SimpleNamespace(
            events=SimpleNamespace(
                loaded=_SetEvent(),
                closed=threading.Event(),
            ),
        )

    def start(**kwargs):
        webview.starts.append(kwargs)
        kwargs["func"]()

    webview.create_window = create_window
    webview.start = start

    result = launch_desktop(
        paths,
        webview_module=webview,
        server_factory=server_factory,
        runtime_check=lambda: True,
        instance_lock=SingleInstanceLock(handle=None),
    )

    assert result == 0
    assert webview.settings["ALLOW_DOWNLOADS"] is True
    assert webview.settings["ALLOW_FILE_URLS"] is False
    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert webview.windows[0][1]["url"] == "http://127.0.0.1:43123"
    assert webview.starts[0]["gui"] == "edgechromium"
    assert server is not None and server.stopped is True


def test_webview_load_timeout_closes_blank_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = WindowsAppPaths.from_environment({"LOCALAPPDATA": str(tmp_path)})
    paths.ensure_directories()
    reported_errors: list[str] = []
    monkeypatch.setattr(
        "pdfmd.windows_app._show_native_error",
        reported_errors.append,
    )
    window = SimpleNamespace(
        events=SimpleNamespace(
            loaded=threading.Event(),
            closed=threading.Event(),
        ),
        destroyed=False,
    )
    window.destroy = lambda: setattr(window, "destroyed", True)
    webview = SimpleNamespace(
        settings={},
        create_window=lambda *_, **__: window,
        start=lambda **kwargs: kwargs["func"](),
    )

    with pytest.raises(RuntimeError, match="未完成加载"):
        _start_webview(
            webview,
            paths,
            url="http://127.0.0.1:49152",
            load_timeout=0.01,
        )

    assert window.destroyed is True
    assert reported_errors == ["WebView2 页面在 0.01 秒内未完成加载"]


def test_loopback_origin_rejects_non_local_addresses() -> None:
    assert _loopback_origin("http://127.0.0.1:49152/") == "http://127.0.0.1:49152"
    with pytest.raises(RuntimeError, match="127.0.0.1"):
        _loopback_origin("http://example.com:49152")


def test_startup_failure_page_escapes_diagnostic_text(tmp_path: Path) -> None:
    page = startup_failure_page("<script>alert(1)</script>", tmp_path / "app.log")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert str(tmp_path / "app.log") in page


def test_smoke_flag_converts_over_http_and_downloads_results(
    sample_pdf: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    local_appdata = tmp_path / "LocalAppData"
    output_dir = tmp_path / "smoke-output"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("PDFMD_DATA_DIR", raising=False)
    monkeypatch.setattr("pdfmd.windows_app.ensure_webview2_runtime", lambda: True)
    monkeypatch.setattr("pdfmd.windows_app._load_webview", lambda: None)

    result = run(
        [
            "--smoke-test",
            str(sample_pdf),
            "--smoke-output",
            str(output_dir),
            "--smoke-timeout",
            "30",
        ]
    )

    captured = capsys.readouterr()
    markdown_path = output_dir / f"{sample_pdf.stem}.md"
    archive_path = output_dir / f"{sample_pdf.stem}-markdown.zip"
    assert result == 0, captured.err
    assert "SMOKE TEST PASSED" in captured.out
    assert markdown_path.is_file()
    assert 'source: "sample.pdf"' in markdown_path.read_text(encoding="utf-8")
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as archive:
        assert {
            "document.md",
            "document.json",
            "quality-report.json",
            "manifest.json",
        } <= set(archive.namelist())
    assert "PDFMD_DATA_DIR" not in os.environ


def test_embedded_server_stops_after_health_check(tmp_path: Path) -> None:
    from pdfmd.config import Settings

    server = EmbeddedUvicornServer(Settings(data_dir=tmp_path / "data"))
    base_url = server.start(timeout=10)
    thread = server.thread

    assert base_url.startswith("http://127.0.0.1:")
    assert thread is not None and thread.is_alive()
    server.stop(timeout=10)
    assert not thread.is_alive()


class _SetEvent(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self.set()
