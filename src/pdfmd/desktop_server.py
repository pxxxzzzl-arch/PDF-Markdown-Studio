from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path

import uvicorn

from pdfmd.api import create_app
from pdfmd.config import Settings


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动 PDF Markdown Studio 桌面版内置服务")
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args(argv)

    port_file = args.port_file.expanduser().resolve()
    port_file.parent.mkdir(parents=True, exist_ok=True)
    listener = _bind_loopback_socket()
    port = listener.getsockname()[1]
    temporary = port_file.with_name(f".{port_file.name}.{os.getpid()}.tmp")

    try:
        temporary.write_text(str(port), encoding="ascii")
        temporary.replace(port_file)
        settings = Settings.from_env()
        app = create_app(settings)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
        )
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        temporary.unlink(missing_ok=True)
        port_file.unlink(missing_ok=True)
        listener.close()


def _bind_loopback_socket() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2048)
    listener.set_inheritable(False)
    return listener


if __name__ == "__main__":
    run()
