from __future__ import annotations

import argparse
import struct
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_MEMBERS = (
    ("icp4", "icon_16x16.png", 16),
    ("icp5", "icon_32x32.png", 32),
    ("icp6", "icon_32x32@2x.png", 64),
    ("ic07", "icon_128x128.png", 128),
    ("ic08", "icon_256x256.png", 256),
    ("ic09", "icon_512x512.png", 512),
    ("ic10", "icon_512x512@2x.png", 1024),
    ("ic11", "icon_16x16@2x.png", 32),
    ("ic12", "icon_32x32@2x.png", 64),
    ("ic13", "icon_128x128@2x.png", 256),
    ("ic14", "icon_256x256@2x.png", 512),
)


def build_icns(iconset: Path, destination: Path) -> None:
    chunks: list[bytes] = []
    for chunk_type, filename, expected_size in ICON_MEMBERS:
        source = iconset / filename
        payload = source.read_bytes()
        width, height = _png_size(payload)
        if (width, height) != (expected_size, expected_size):
            raise ValueError(
                f"{filename} 尺寸应为 {expected_size}×{expected_size}，"
                f"实际为 {width}×{height}"
            )
        chunk_length = len(payload) + 8
        chunks.append(chunk_type.encode("ascii") + struct.pack(">I", chunk_length) + payload)

    body = b"".join(chunks)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def _png_size(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or not payload.startswith(PNG_SIGNATURE):
        raise ValueError("图标资源不是有效的 PNG")
    return struct.unpack(">II", payload[16:24])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a modern ICNS file from an iconset")
    parser.add_argument("iconset", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_icns(args.iconset, args.destination)


if __name__ == "__main__":
    main()
