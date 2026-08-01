from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    repo_id: str
    revision: str
    ref_name: str

    @property
    def cache_name(self) -> str:
        return "models--" + self.repo_id.replace("/", "--")


MODEL_SPECS = (
    ModelSpec(
        repo_id="docling-project/docling-layout-heron",
        revision="8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        ref_name="main",
    ),
    ModelSpec(
        repo_id="docling-project/docling-models",
        revision="fc0f2d45e2218ea24bce5045f58a389aed16dc23",
        ref_name="v2.3.0",
    ),
    ModelSpec(
        repo_id="docling-project/CodeFormulaV2",
        revision="ecedbe111d15c2dc60bfd4a823cbe80127b58af4",
        ref_name="main",
    ),
)


def prepare_models(
    hf_home: Path,
    *,
    downloader: Callable[..., str] | None = None,
) -> None:
    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download

    hub = hf_home.expanduser().resolve() / "hub"
    hub.mkdir(parents=True, exist_ok=True)

    for spec in MODEL_SPECS:
        snapshot = Path(
            downloader(
                repo_id=spec.repo_id,
                revision=spec.revision,
                cache_dir=hub,
            )
        )
        if not snapshot.is_dir():
            raise RuntimeError(f"模型快照下载失败：{spec.repo_id}@{spec.revision}")

        expected_snapshot = hub / spec.cache_name / "snapshots" / spec.revision
        if not expected_snapshot.is_dir():
            raise RuntimeError(f"模型缓存结构不完整：{expected_snapshot}")

        ref_file = hub / spec.cache_name / "refs" / spec.ref_name
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(f"{spec.revision}\n", encoding="ascii")
        print(f"已准备 {spec.repo_id}@{spec.revision}")


def main() -> None:
    parser = argparse.ArgumentParser(description="准备 macOS Release 使用的固定 Docling 模型")
    parser.add_argument("--hf-home", type=Path, required=True)
    args = parser.parse_args()
    prepare_models(args.hf_home)


if __name__ == "__main__":
    main()
