from __future__ import annotations

import argparse
import os

import uvicorn

from pdfmd.api import create_app

app = create_app()


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="启动 PDF Markdown Studio 本地服务")
    parser.add_argument("--host", default=os.getenv("PDFMD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PDFMD_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="开发模式下自动重载")
    args = parser.parse_args(argv)
    uvicorn.run("pdfmd.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    run()
