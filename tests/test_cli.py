from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdfmd import __version__
from pdfmd.cli import build_parser, main


def test_cli_native_conversion(sample_pdf: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "cli-result"
    exit_code = main(
        [
            str(sample_pdf),
            "--output",
            str(output),
            "--engine",
            "native",
            "--fallback-engine",
            "native",
            "--no-images",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "质量评分" in captured.out
    assert (output / "document.md").is_file()
    assert "<!-- page:" not in (output / "document.md").read_text(encoding="utf-8")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_filename"] == sample_pdf.name
    assert not captured.err


def test_cli_page_markers_are_opt_in(sample_pdf: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "cli-page-markers"
    exit_code = main(
        [
            str(sample_pdf),
            "--output",
            str(output),
            "--engine",
            "native",
            "--fallback-engine",
            "native",
            "--no-images",
            "--page-markers",
        ]
    )
    capsys.readouterr()
    assert exit_code == 0
    assert "<!-- page: 1 -->" in (output / "document.md").read_text(encoding="utf-8")


def test_cli_no_page_markers_remains_compatible(sample_pdf: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "cli-no-page-markers"
    exit_code = main(
        [
            str(sample_pdf),
            "--output",
            str(output),
            "--engine",
            "native",
            "--fallback-engine",
            "native",
            "--no-images",
            "--no-page-markers",
        ]
    )
    capsys.readouterr()
    assert exit_code == 0
    assert "<!-- page:" not in (output / "document.md").read_text(encoding="utf-8")


def test_cli_reports_validation_error_without_traceback(tmp_path: Path, capsys) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_text("not pdf", encoding="utf-8")
    exit_code = main([str(fake), "--output", str(tmp_path / "result"), "--engine", "native"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "错误：" in captured.err
    assert "Traceback" not in captured.err


def test_cli_engine_status_and_version(capsys) -> None:
    assert main(["--engine-status"]) == 0
    statuses = json.loads(capsys.readouterr().out)
    assert {item["name"] for item in statuses} == {"docling", "native", "paddleocr"}

    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_rejects_out_of_range_quality_score(sample_pdf: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([str(sample_pdf), "--minimum-quality-score", "101"])
    assert exc_info.value.code == 2


def test_cli_code_enrichment_is_explicitly_opt_in() -> None:
    assert build_parser().parse_args(["sample.pdf"]).code_enrichment is False
    assert build_parser().parse_args(["sample.pdf", "--code-enrichment"]).code_enrichment is True
