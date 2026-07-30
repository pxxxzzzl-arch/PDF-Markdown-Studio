from __future__ import annotations

import argparse
import ctypes
import html
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import uvicorn

from pdfmd.api import create_app
from pdfmd.config import Settings
from pdfmd.desktop_server import _bind_loopback_socket

APP_NAME = "PDF Markdown Studio"
APP_MUTEX_NAME = r"Local\PDFMarkdownStudio"
ERROR_ALREADY_EXISTS = 183
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_INSTALLER_NAMES = (
    "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
    "MicrosoftEdgeWebview2Setup.exe",
)
FRONTEND_ASSET_PATTERN = re.compile(rb"""(?:src|href)=["'](/static/[^"'?#]+)""")
WINDOWS_ENVIRONMENT_KEYS = (
    "PDFMD_DATA_DIR",
    "PDFMD_DESKTOP",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "DOCLING_CACHE_DIR",
    "TORCH_HOME",
    "XDG_CACHE_HOME",
    "PADDLE_HOME",
    "DO_NOT_TRACK",
    "HF_HUB_DISABLE_TELEMETRY",
    "TOKENIZERS_PARALLELISM",
)
_LOCAL_HTTP_OPENER = build_opener(ProxyHandler({}))


class WindowsAppError(RuntimeError):
    """A recoverable desktop bootstrap failure that can be shown to the user."""


@dataclass(frozen=True, slots=True)
class WindowsAppPaths:
    root: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    webview_dir: Path

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        home: Path | None = None,
    ) -> WindowsAppPaths:
        values = environ if environ is not None else os.environ
        override = values.get("PDFMD_WINDOWS_HOME")
        if override:
            root = Path(override).expanduser()
        else:
            local_appdata = values.get("LOCALAPPDATA")
            if local_appdata:
                root = Path(local_appdata).expanduser() / APP_NAME
            else:
                fallback_home = home if home is not None else Path.home()
                root = fallback_home.expanduser() / "AppData" / "Local" / APP_NAME
        root = root.resolve()
        return cls(
            root=root,
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            webview_dir=root / "webview",
        )

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "desktop.log"

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.cache_dir,
            self.logs_dir,
            self.webview_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def configure_windows_environment(
    paths: WindowsAppPaths,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    values = environ if environ is not None else os.environ
    values["PDFMD_DATA_DIR"] = str(paths.data_dir)
    values["PDFMD_DESKTOP"] = "1"
    values.setdefault("HF_HOME", str(paths.cache_dir / "huggingface"))
    values.setdefault("HF_HUB_CACHE", str(paths.cache_dir / "huggingface" / "hub"))
    values.setdefault("HUGGINGFACE_HUB_CACHE", str(paths.cache_dir / "huggingface" / "hub"))
    values.setdefault("TRANSFORMERS_CACHE", str(paths.cache_dir / "huggingface" / "transformers"))
    values.setdefault("DOCLING_CACHE_DIR", str(paths.cache_dir / "docling"))
    values.setdefault("TORCH_HOME", str(paths.cache_dir / "torch"))
    values.setdefault("XDG_CACHE_HOME", str(paths.cache_dir))
    values.setdefault("PADDLE_HOME", str(paths.cache_dir / "paddle"))
    values.setdefault("DO_NOT_TRACK", "1")
    values.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    values.setdefault("TOKENIZERS_PARALLELISM", "false")


@dataclass(slots=True)
class FileLoggingState:
    handler: RotatingFileHandler
    original_stdout: TextIO | None
    original_stderr: TextIO | None
    fallback_stream: TextIO | None

    def close(self) -> None:
        root_logger = logging.getLogger()
        root_logger.removeHandler(self.handler)
        self.handler.close()
        if self.fallback_stream is not None:
            if sys.stdout is self.fallback_stream:
                sys.stdout = self.original_stdout
            if sys.stderr is self.fallback_stream:
                sys.stderr = self.original_stderr
            self.fallback_stream.close()


def configure_file_logging(log_file: Path) -> FileLoggingState:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    fallback_stream: TextIO | None = None
    if sys.stdout is None or sys.stderr is None:
        fallback_stream = log_file.with_name("console.log").open(
            "a",
            encoding="utf-8",
            buffering=1,
        )
        if sys.stdout is None:
            sys.stdout = fallback_stream
        if sys.stderr is None:
            sys.stderr = fallback_stream

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_pdfmd_windows_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler._pdfmd_windows_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    return FileLoggingState(
        handler=handler,
        original_stdout=original_stdout,
        original_stderr=original_stderr,
        fallback_stream=fallback_stream,
    )


class EmbeddedUvicornServer:
    """Run the loopback-only FastAPI app in the desktop process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.listener: socket.socket | None = None
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.failure: BaseException | None = None
        self.base_url: str | None = None

    def start(self, timeout: float = 20.0) -> str:
        if self.thread is not None:
            raise WindowsAppError("内置服务已经启动")

        self.listener = _bind_loopback_socket()
        port = int(self.listener.getsockname()[1])
        self.base_url = f"http://127.0.0.1:{port}"
        try:
            app = create_app(self.settings)
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="info",
                log_config=None,
                access_log=False,
            )
            self.server = uvicorn.Server(config)
            self.thread = threading.Thread(
                target=self._serve,
                name="pdfmd-windows-server",
                daemon=True,
            )
            self.thread.start()
            self._wait_until_ready(timeout)
        except BaseException:
            self.stop()
            raise
        return self.base_url

    def _serve(self) -> None:
        assert self.server is not None
        assert self.listener is not None
        try:
            self.server.run(sockets=[self.listener])
        except BaseException as exc:
            self.failure = exc
            logging.getLogger(__name__).exception("内置服务异常退出")
        finally:
            self.listener.close()

    def _wait_until_ready(self, timeout: float) -> None:
        assert self.base_url is not None
        deadline = time.monotonic() + timeout
        health_url = f"{self.base_url}/api/health"
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            if self.failure is not None:
                raise WindowsAppError(f"内置服务启动失败：{self.failure}") from self.failure
            if self.thread is not None and not self.thread.is_alive():
                raise WindowsAppError("内置服务在启动过程中意外退出")
            try:
                with _LOCAL_HTTP_OPENER.open(health_url, timeout=0.5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("status") == "ok":
                    return
            except (OSError, ValueError, URLError) as exc:
                last_error = exc
            time.sleep(0.05)
        raise WindowsAppError(f"内置服务启动超时：{last_error or '健康检查无响应'}")

    def stop(self, timeout: float | None = None) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout)
            if timeout is not None and self.thread.is_alive() and self.server is not None:
                self.server.force_exit = True
                self.thread.join(min(timeout, 2.0))
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass


@dataclass(frozen=True, slots=True)
class SingleInstanceLock:
    handle: int | None
    already_running: bool = False

    @classmethod
    def acquire(cls, name: str = APP_MUTEX_NAME) -> SingleInstanceLock:
        if os.name != "nt":
            return cls(handle=None)

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        handle = create_mutex(None, False, name)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(error, "无法创建 Windows 单实例互斥锁")
        return cls(
            handle=int(handle),
            already_running=ctypes.get_last_error() == ERROR_ALREADY_EXISTS,
        )

    def close(self) -> None:
        if self.handle is None or os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CloseHandle(ctypes.c_void_p(self.handle))


def _registry_has_webview2(winreg_module: ModuleType | Any) -> bool:
    key_paths = (
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}",
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}",
    )
    roots = (winreg_module.HKEY_CURRENT_USER, winreg_module.HKEY_LOCAL_MACHINE)
    access_modes = {
        int(getattr(winreg_module, "KEY_READ", 0)),
        int(getattr(winreg_module, "KEY_READ", 0))
        | int(getattr(winreg_module, "KEY_WOW64_32KEY", 0)),
        int(getattr(winreg_module, "KEY_READ", 0))
        | int(getattr(winreg_module, "KEY_WOW64_64KEY", 0)),
    }
    for root in roots:
        for key_path in key_paths:
            for access in access_modes:
                try:
                    with winreg_module.OpenKey(root, key_path, 0, access) as key:
                        version = str(winreg_module.QueryValueEx(key, "pv")[0]).strip()
                except OSError:
                    continue
                if version and version != "0.0.0.0":
                    return True
    return False


def has_webview2_runtime() -> bool:
    if os.name != "nt":
        return True
    try:
        import winreg
    except ImportError:
        return False
    return _registry_has_webview2(winreg)


def find_bundled_webview2_installer(
    environ: Mapping[str, str] | None = None,
    *,
    search_roots: list[Path] | None = None,
) -> Path | None:
    values = environ if environ is not None else os.environ
    explicit = values.get("PDFMD_WEBVIEW2_BOOTSTRAPPER")
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() else None

    if search_roots is None:
        roots = [Path(sys.executable).resolve().parent]
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            roots.append(Path(bundle_root).resolve())
        roots.append(Path(__file__).resolve().parents[2])
    else:
        roots = [root.expanduser().resolve() for root in search_roots]

    for root in roots:
        for relative_dir in (Path(), Path("webview2"), Path("runtime")):
            for filename in WEBVIEW2_INSTALLER_NAMES:
                candidate = root / relative_dir / filename
                if candidate.is_file():
                    return candidate
    return None


def ensure_webview2_runtime(
    *,
    detector: Callable[[], bool] = has_webview2_runtime,
    installer: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    verification_timeout: float = 10.0,
) -> bool:
    logger = logging.getLogger(__name__)
    try:
        if detector():
            return True
    except Exception:
        logger.exception("检测 WebView2 Runtime 时发生异常")

    installer = installer or find_bundled_webview2_installer()
    if installer is None:
        return False
    logger.info("正在安装 WebView2 Runtime：%s", installer)
    try:
        completed = runner(
            [str(installer), "/silent", "/install"],
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("WebView2 Runtime 安装程序无法执行")
        return False

    deadline = time.monotonic() + max(0.0, verification_timeout)
    while True:
        try:
            if detector():
                return True
        except Exception:
            logger.exception("复检 WebView2 Runtime 时发生异常")
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)

    logger.error(
        "WebView2 Runtime 安装后仍不可用，安装程序退出码 %s",
        completed.returncode,
    )
    return False


def _load_webview() -> ModuleType:
    try:
        import webview
    except ImportError as exc:
        raise WindowsAppError("桌面组件 pywebview 未安装") from exc
    return webview


def configure_webview(webview_module: ModuleType | Any) -> None:
    webview_module.settings["ALLOW_DOWNLOADS"] = True
    webview_module.settings["ALLOW_FILE_URLS"] = False
    webview_module.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True


def startup_failure_page(message: str, log_file: Path) -> str:
    safe_message = html.escape(message)
    safe_log_file = html.escape(str(log_file))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_NAME} 启动失败</title>
  <style>
    :root {{ color-scheme: light; font-family: "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f3f0e8; color: #20231f; }}
    main {{ max-width: 720px; margin: 12vh auto; padding: 44px; background: #fffdf8;
            border: 1px solid #d7d1c4; border-radius: 18px; }}
    h1 {{ margin-top: 0; font-size: 28px; }}
    p {{ line-height: 1.7; }}
    code {{ display: block; padding: 12px; overflow-wrap: anywhere; background: #eee9df; }}
  </style>
</head>
<body>
  <main>
    <h1>应用启动失败</h1>
    <p>{safe_message}</p>
    <p>请关闭应用后重试；诊断日志保存在：</p>
    <code>{safe_log_file}</code>
  </main>
</body>
</html>"""


def _show_native_error(message: str) -> None:
    logging.getLogger(__name__).error(message)
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            return
        except (AttributeError, OSError):
            pass
    if sys.stderr is not None:
        print(f"{APP_NAME}: {message}", file=sys.stderr)


def _monitor_webview_load(
    window: Any,
    failures: list[str],
    *,
    timeout: float,
    close_after_load: bool,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window.events.loaded.wait(timeout=0.1):
            if close_after_load:
                try:
                    has_root = window.evaluate_js(
                        "document.getElementById('root') !== null"
                    )
                    title = window.evaluate_js("document.title")
                    if has_root is not True or APP_NAME not in str(title):
                        raise WindowsAppError("WebView2 未加载有效的应用页面")
                except Exception as exc:
                    failures.append(f"WebView2 页面验证失败：{exc}")
                finally:
                    window.destroy()
            return
        if window.events.closed.is_set():
            return

    message = f"WebView2 页面在 {timeout:g} 秒内未完成加载"
    failures.append(message)
    _show_native_error(message)
    try:
        window.destroy()
    except Exception:
        logging.getLogger(__name__).exception("关闭无响应的 WebView2 窗口失败")


def _start_webview(
    webview_module: ModuleType | Any,
    paths: WindowsAppPaths,
    *,
    url: str | None = None,
    failure_message: str | None = None,
    hidden: bool = False,
    close_after_load: bool = False,
    load_timeout: float = 45.0,
) -> None:
    configure_webview(webview_module)
    window_options: dict[str, Any] = {
        "width": 1180,
        "height": 780,
        "min_size": (960, 680),
        "background_color": "#f3f0e8",
        "hidden": hidden,
    }
    if failure_message is None:
        window = webview_module.create_window(APP_NAME, url=url, **window_options)
    else:
        window = webview_module.create_window(
            f"{APP_NAME} - 启动失败",
            html=startup_failure_page(failure_message, paths.log_file),
            **window_options,
        )
    if window is None:
        raise WindowsAppError("WebView2 窗口初始化被取消")

    failures: list[str] = []

    def monitor() -> None:
        _monitor_webview_load(
            window,
            failures,
            timeout=load_timeout,
            close_after_load=close_after_load,
        )

    webview_module.start(
        func=monitor,
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(paths.webview_dir),
    )
    if failures:
        raise WindowsAppError(failures[0])


def launch_desktop(
    paths: WindowsAppPaths,
    *,
    webview_module: ModuleType | Any | None = None,
    server_factory: Callable[[Settings], EmbeddedUvicornServer] = EmbeddedUvicornServer,
    runtime_check: Callable[[], bool] = ensure_webview2_runtime,
    instance_lock: SingleInstanceLock | None = None,
) -> int:
    lock: SingleInstanceLock | None = None
    server: EmbeddedUvicornServer | None = None
    try:
        try:
            lock = instance_lock if instance_lock is not None else SingleInstanceLock.acquire()
        except Exception as exc:
            logging.getLogger(__name__).exception("创建单实例锁失败")
            _show_native_error(f"无法建立应用单实例锁：{exc}")
            return 2

        if lock.already_running:
            _show_native_error("PDF Markdown Studio 已经在运行。")
            return 0
        try:
            runtime_available = runtime_check()
        except Exception as exc:
            logging.getLogger(__name__).exception("检查 WebView2 Runtime 失败")
            _show_native_error(f"检查 Microsoft Edge WebView2 Runtime 失败：{exc}")
            return 2
        if not runtime_available:
            _show_native_error(
                "未检测到 Microsoft Edge WebView2 Runtime，且随包安装程序未能完成安装。"
            )
            return 2
        try:
            webview_module = webview_module or _load_webview()
        except WindowsAppError as exc:
            _show_native_error(str(exc))
            return 2

        server = server_factory(Settings.from_env())
        try:
            base_url = server.start()
        except BaseException as exc:
            logging.getLogger(__name__).exception("内置服务启动失败")
            try:
                _start_webview(webview_module, paths, failure_message=str(exc))
            except BaseException:
                _show_native_error(f"内置服务启动失败：{exc}")
            return 1

        try:
            _start_webview(webview_module, paths, url=base_url)
        except BaseException as exc:
            logging.getLogger(__name__).exception("桌面窗口启动失败")
            _show_native_error(f"桌面窗口启动失败：{exc}")
            return 1
        return 0
    finally:
        if server is not None:
            server.stop()
        if lock is not None:
            lock.close()


@dataclass(frozen=True, slots=True)
class SmokeTestResult:
    job_id: str
    markdown_path: Path
    archive_path: Path


def _multipart_upload_body(pdf_path: Path) -> tuple[bytes, str]:
    boundary = f"----PDFMarkdownStudio{uuid.uuid4().hex}"
    safe_filename = pdf_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    options = json.dumps(
        {
            "primary_engine": "native",
            "fallback_engine": "native",
            "ocr_mode": "never",
            "extract_images": False,
            "enable_quality_fallback": False,
        },
        ensure_ascii=False,
    )
    body = bytearray()
    for name, value in (("options_json", options),):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
    )
    body.extend(pdf_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), boundary


def _http_bytes(request: Request, timeout: float) -> tuple[bytes, Mapping[str, str]]:
    try:
        with _LOCAL_HTTP_OPENER.open(request, timeout=timeout) as response:
            return response.read(), response.headers
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WindowsAppError(f"HTTP {exc.code}: {detail}") from exc
    except (OSError, URLError) as exc:
        raise WindowsAppError(f"无法访问内置服务：{exc}") from exc


def _loopback_origin(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise WindowsAppError("内置服务地址无效") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise WindowsAppError("冒烟测试只允许访问 127.0.0.1 内置服务")
    return f"http://127.0.0.1:{port}"


def verify_frontend_assets(base_url: str, *, timeout: float) -> None:
    origin = _loopback_origin(base_url)
    index, _ = _http_bytes(Request(f"{origin}/", method="GET"), timeout)
    if b'id="root"' not in index or APP_NAME.encode() not in index:
        raise WindowsAppError("打包后的前端首页缺失或内容无效")
    match = FRONTEND_ASSET_PATTERN.search(index)
    if match is None:
        raise WindowsAppError("打包后的前端首页没有静态资源引用")
    asset_path = match.group(1).decode("ascii")
    asset, _ = _http_bytes(Request(f"{origin}{asset_path}", method="GET"), timeout)
    if len(asset) < 32:
        raise WindowsAppError(f"打包后的前端静态资源无效：{asset_path}")


def run_http_smoke_test(
    pdf_path: Path,
    base_url: str,
    output_dir: Path,
    *,
    timeout: float = 120.0,
) -> SmokeTestResult:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise WindowsAppError(f"冒烟测试 PDF 不存在：{pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise WindowsAppError("冒烟测试输入必须是 PDF 文件")
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise WindowsAppError(f"冒烟测试输出目录已存在：{output_dir}")

    origin = _loopback_origin(base_url)
    verify_frontend_assets(origin, timeout=min(timeout, 30.0))
    body, boundary = _multipart_upload_body(pdf_path)
    upload = Request(
        f"{origin}/api/jobs",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": origin,
        },
    )
    payload, _ = _http_bytes(upload, min(timeout, 60.0))
    try:
        job = json.loads(payload.decode("utf-8"))
        job_id = str(job["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WindowsAppError("内置服务返回了无效的任务信息") from exc

    deadline = time.monotonic() + timeout
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status_request = Request(f"{origin}/api/jobs/{job_id}", method="GET")
        request_timeout = min(5.0, max(0.1, deadline - time.monotonic()))
        status_bytes, _ = _http_bytes(status_request, request_timeout)
        try:
            status = json.loads(status_bytes.decode("utf-8"))
        except ValueError as exc:
            raise WindowsAppError("内置服务返回了无效的任务状态") from exc
        if status.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.05)
    else:
        raise WindowsAppError(f"转换在 {timeout:g} 秒内未完成")

    if status.get("status") != "completed":
        failure_detail = status.get("error") or status.get("stage") or "未知错误"
        raise WindowsAppError(f"转换失败：{failure_detail}")

    markdown, _ = _http_bytes(
        Request(f"{origin}/api/jobs/{job_id}/markdown", method="GET"),
        min(timeout, 60.0),
    )
    archive, _ = _http_bytes(
        Request(f"{origin}/api/jobs/{job_id}/archive", method="GET"),
        min(timeout, 60.0),
    )
    if not markdown or not archive.startswith(b"PK"):
        raise WindowsAppError("下载结果校验失败")

    output_dir.mkdir(parents=True, exist_ok=False)
    markdown_path = output_dir / f"{pdf_path.stem}.md"
    archive_path = output_dir / f"{pdf_path.stem}-markdown.zip"
    markdown_path.write_bytes(markdown)
    archive_path.write_bytes(archive)
    return SmokeTestResult(
        job_id=job_id,
        markdown_path=markdown_path,
        archive_path=archive_path,
    )


def _default_smoke_output(pdf_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return pdf_path.expanduser().resolve().parent / f"{pdf_path.stem}-pdfmd-smoke-{stamp}"


def _environment_snapshot() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in WINDOWS_ENVIRONMENT_KEYS}


def _restore_environment(snapshot: Mapping[str, str | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _run_smoke_mode(
    paths: WindowsAppPaths,
    pdf_path: Path,
    output_dir: Path | None,
    timeout: float,
) -> int:
    lock: SingleInstanceLock | None = None
    server: EmbeddedUvicornServer | None = None
    try:
        lock = SingleInstanceLock.acquire()
        if lock.already_running:
            raise WindowsAppError("PDF Markdown Studio 已经在运行，无法执行冒烟测试")
        webview_module: ModuleType | Any | None = None
        if os.name == "nt":
            if not ensure_webview2_runtime():
                raise WindowsAppError("WebView2 Runtime 不可用")
            webview_module = _load_webview()

        server = EmbeddedUvicornServer(Settings.from_env())
        base_url = server.start()
        result = run_http_smoke_test(
            pdf_path,
            base_url,
            output_dir or _default_smoke_output(pdf_path),
            timeout=timeout,
        )
        if webview_module is not None:
            _start_webview(
                webview_module,
                paths,
                url=base_url,
                hidden=True,
                close_after_load=True,
                load_timeout=min(timeout, 60.0),
            )
    except Exception as exc:
        logging.getLogger(__name__).exception("Windows 冒烟测试失败")
        if sys.stderr is not None:
            print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.stop(timeout=min(max(timeout, 1.0), 30.0))
        if lock is not None:
            lock.close()
    if sys.stdout is not None:
        print(f"SMOKE TEST PASSED: {result.job_id}")
        print(f"Markdown: {result.markdown_path}")
        print(f"Archive: {result.archive_path}")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"启动 {APP_NAME} Windows 桌面版")
    parser.add_argument(
        "--smoke-test",
        type=Path,
        metavar="PDF",
        help="不打开窗口，通过 HTTP 使用 native 引擎转换指定 PDF 并下载 Markdown/ZIP",
    )
    parser.add_argument(
        "--smoke-output",
        type=Path,
        help="冒烟测试结果目录（必须尚不存在）",
    )
    parser.add_argument(
        "--smoke-timeout",
        type=float,
        default=120.0,
        help="冒烟转换超时秒数（默认：120）",
    )
    args = parser.parse_args(argv)
    if args.smoke_output is not None and args.smoke_test is None:
        parser.error("--smoke-output 只能与 --smoke-test 一起使用")
    if args.smoke_timeout <= 0:
        parser.error("--smoke-timeout 必须大于 0")

    snapshot = _environment_snapshot()
    logging_state: FileLoggingState | None = None
    try:
        paths = WindowsAppPaths.from_environment()
        paths.ensure_directories()
        configure_windows_environment(paths)
        logging_state = configure_file_logging(paths.log_file)
        logging.getLogger(__name__).info("%s 正在启动", APP_NAME)

        if args.smoke_test is not None:
            return _run_smoke_mode(
                paths,
                args.smoke_test,
                args.smoke_output,
                args.smoke_timeout,
            )
        return launch_desktop(paths)
    except Exception as exc:
        logging.getLogger(__name__).exception("Windows 桌面应用初始化失败")
        _show_native_error(f"应用初始化失败：{exc}")
        return 1
    finally:
        if logging_state is not None:
            logging_state.close()
        _restore_environment(snapshot)


if __name__ == "__main__":
    raise SystemExit(run())
