from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
from pathlib import Path

APP_NAME = "PDF Markdown Studio"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.-]+)?$")


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum)
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum & 0xFFFFFFFF)
    )


def _inside_rounded_rect(
    x: float,
    y: float,
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
) -> bool:
    if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
        return left <= x <= right and top <= y <= bottom
    corner_x = left + radius if x < left + radius else right - radius
    corner_y = top + radius if y < top + radius else bottom - radius
    return (x - corner_x) ** 2 + (y - corner_y) ** 2 <= radius**2


def _icon_pixel(size: int, x: int, y: int) -> tuple[int, int, int, int]:
    scale = size / 256
    px = (x + 0.5) / scale
    py = (y + 0.5) / scale
    if not _inside_rounded_rect(px, py, 14, 14, 242, 242, 48):
        return (0, 0, 0, 0)

    background = (31, 35, 31, 255)
    if 69 <= px <= 187 and 46 <= py <= 210:
        if px > 153 and py < 80 and py < 233 - px:
            return background
        if px >= 153 and py <= 80:
            return (213, 208, 195, 255)
        if _inside_rounded_rect(px, py, 69, 46, 187, 210, 7):
            if 88 <= px <= 168 and 104 <= py <= 113:
                return (185, 71, 39, 255)
            if 88 <= px <= 168 and 130 <= py <= 138:
                return (71, 76, 70, 255)
            if 88 <= px <= 151 and 155 <= py <= 163:
                return (71, 76, 70, 255)
            return (249, 247, 240, 255)
    return background


def build_png(size: int) -> bytes:
    if size <= 0 or size > 256:
        raise ValueError("icon size must be between 1 and 256")
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(_icon_pixel(size, x, y))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )


def build_ico(destination: Path) -> None:
    images = [(size, build_png(size)) for size in ICON_SIZES]
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, image in images:
        encoded_size = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        payload.extend(image)
        offset += len(image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + payload)


def _numeric_version(version: str) -> tuple[int, int, int, int]:
    release = version.split("-", 1)[0].split("+", 1)[0]
    values = [int(value) for value in release.split(".")]
    padded = (values + [0, 0, 0, 0])[:4]
    return padded[0], padded[1], padded[2], padded[3]


def build_version_info(destination: Path, version: str) -> None:
    numeric = _numeric_version(version)
    dotted = ".".join(str(value) for value in numeric)
    content = f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric},
    prodvers={numeric},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'PDF Markdown Studio'),
          StringStruct('FileDescription', 'PDF Markdown Studio'),
          StringStruct('FileVersion', '{dotted}'),
          StringStruct('InternalName', 'PDF Markdown Studio'),
          StringStruct('LegalCopyright', 'Released under the MIT License'),
          StringStruct('OriginalFilename', 'PDF Markdown Studio.exe'),
          StringStruct('ProductName', 'PDF Markdown Studio'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def prepare_assets(build_root: Path, version: str) -> dict[str, str]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid application version: {version}")
    generated = build_root.expanduser().resolve() / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    icon = generated / f"{APP_NAME}.ico"
    version_info = generated / "windows_version_info.txt"
    manifest = generated / "assets.json"

    build_ico(icon)
    build_version_info(version_info, version)
    result = {
        "icon": str(icon),
        "version_info": str(version_info),
        "version": version,
    }
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="准备 Windows 桌面版图标和版本资源")
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    result = prepare_assets(args.build_root, args.version)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
