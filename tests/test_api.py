from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pdfmd.api import create_app
from pdfmd.config import Settings
from pdfmd.models import JobRecord, JobStatus


def test_api_serves_health_and_built_frontend(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health")
        index = client.get("/")

        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["version"] == "0.8.0"
        assert index.status_code == 200
        assert "PDF Markdown Studio" in index.text
        assert index.headers["cache-control"] == "no-store"

        asset_match = re.search(r'src="(/static/[^\"]+\.js)"', index.text)
        assert asset_match is not None
        asset = client.get(asset_match.group(1))
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith("text/javascript")
        assert "immutable" in asset.headers["cache-control"]


def test_api_converts_pdf(sample_pdf: Path, tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        max_file_size=10_000_000,
        max_pages=10,
        max_workers=1,
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        with sample_pdf.open("rb") as stream:
            response = client.post(
                "/api/jobs",
                headers={"Origin": "http://127.0.0.1:8000"},
                files={"file": ("sample.pdf", stream, "application/pdf")},
                data={
                    "options_json": json.dumps(
                        {
                            "primary_engine": "native",
                            "fallback_engine": "native",
                            "extract_images": False,
                        }
                    )
                },
            )
        assert response.status_code == 202, response.text
        job = response.json()
        assert "output_dir" not in job
        assert "password" not in job["options"]

        for _ in range(100):
            current = client.get(f"/api/jobs/{job['id']}").json()
            if current["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert current["status"] == "completed", current
        markdown = client.get(f"/api/jobs/{job['id']}/markdown")
        assert markdown.status_code == 200
        assert 'source: "sample.pdf"' in markdown.text
        assert "<!-- page:" not in markdown.text
        quality = client.get(f"/api/jobs/{job['id']}/quality")
        assert quality.status_code == 200
        assert quality.json()["score"] >= 72
        output_dir = Path(client.app.state.job_store.get(job["id"]).output_dir)
        (output_dir / ".fallback-pages.pdf").write_bytes(b"temporary subset")
        archive = client.get(f"/api/jobs/{job['id']}/archive")
        assert archive.status_code == 200
        assert archive.content.startswith(b"PK")
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            names = set(bundle.namelist())
            manifest = json.loads(bundle.read("manifest.json"))
            document = json.loads(bundle.read("document.json"))
        assert {"document.md", "document.json", "quality-report.json", "manifest.json"} <= names
        assert ".fallback-pages.pdf" not in names
        assert manifest["source_filename"] == "sample.pdf"
        assert document["source_filename"] == "sample.pdf"
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)

        traversal = client.get(f"/api/jobs/{job['id']}/assets/%2E%2E/document.md")
        assert traversal.status_code in {400, 404}

        job_dir = settings.jobs_dir / job["id"]
        assert job_dir.is_dir()
        assert (
            client.delete(
                f"/api/jobs/{job['id']}",
                headers={"Origin": "http://127.0.0.1:8000"},
            ).status_code
            == 204
        )
        assert client.get(f"/api/jobs/{job['id']}").status_code == 404
        assert not job_dir.exists()


def test_api_queues_multiple_independent_pdfs(sample_pdf: Path, tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        max_file_size=10_000_000,
        max_pages=10,
        max_workers=1,
    )
    origin = "http://127.0.0.1:8000"
    options = json.dumps(
        {
            "primary_engine": "native",
            "fallback_engine": "native",
            "extract_images": False,
        }
    )
    with TestClient(create_app(settings), base_url=origin) as client:
        jobs = []
        for index in range(3):
            with sample_pdf.open("rb") as stream:
                response = client.post(
                    "/api/jobs",
                    headers={"Origin": origin},
                    files={"file": (f"batch-{index + 1}.pdf", stream, "application/pdf")},
                    data={"options_json": options},
                )
            assert response.status_code == 202, response.text
            jobs.append(response.json())

        assert len({job["id"] for job in jobs}) == 3
        completed = {}
        for _ in range(200):
            completed = {job["id"]: client.get(f"/api/jobs/{job['id']}").json() for job in jobs}
            if all(job["status"] in {"completed", "failed"} for job in completed.values()):
                break
            time.sleep(0.05)

        assert all(job["status"] == "completed" for job in completed.values()), completed
        records = [client.app.state.job_store.get(job["id"]) for job in jobs]
        assert len({record.output_dir for record in records}) == 3
        assert all(
            Path(record.output_dir or "").joinpath("document.md").is_file() for record in records
        )

        selected_ids = [jobs[0]["id"], jobs[2]["id"]]
        batch_archive = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": selected_ids},
        )
        assert batch_archive.status_code == 200, batch_archive.text
        assert batch_archive.content.startswith(b"PK")
        assert "pdf-markdown-batch-2.zip" in batch_archive.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(batch_archive.content)) as bundle:
            names = set(bundle.namelist())
            manifest = json.loads(bundle.read("batch-manifest.json"))

        assert manifest["count"] == 2
        assert [item["job_id"] for item in manifest["jobs"]] == selected_ids
        assert [item["source_filename"] for item in manifest["jobs"]] == [
            "batch-1.pdf",
            "batch-3.pdf",
        ]
        selected_folders = [item["directory"] for item in manifest["jobs"]]
        for folder in selected_folders:
            assert {
                f"{folder}/document.md",
                f"{folder}/document.json",
                f"{folder}/quality-report.json",
                f"{folder}/manifest.json",
            } <= names
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        assert not any(jobs[1]["id"][:8] in name for name in names)
        assert not any((settings.data_dir / "downloads").iterdir())

        direct_download = client.get(
            "/api/jobs/archive",
            params=[("job_id", job_id) for job_id in selected_ids],
        )
        assert direct_download.status_code == 200, direct_download.text
        assert direct_download.content.startswith(b"PK")
        assert "pdf-markdown-batch-2.zip" in direct_download.headers["content-disposition"]
        assert not any((settings.data_dir / "downloads").iterdir())


def test_batch_archive_validates_selection(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_batch_files=2)
    origin = "http://127.0.0.1:8000"
    with TestClient(create_app(settings), base_url=origin) as client:
        empty = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": []},
        )
        duplicate = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": ["a" * 32, "a" * 32]},
        )
        invalid = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": ["not-a-job-id"]},
        )
        too_many = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": ["a" * 32, "b" * 32, "c" * 32]},
        )
        unknown = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": ["d" * 32]},
        )

        queued_id = "e" * 32
        client.app.state.job_store.create(
            JobRecord(
                id=queued_id,
                status=JobStatus.QUEUED,
                filename="queued.pdf",
            )
        )
        unfinished = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": [queued_id]},
        )
        escaped_id = "f" * 32
        escaped_output = tmp_path / "outside-output"
        escaped_output.mkdir()
        client.app.state.job_store.create(
            JobRecord(
                id=escaped_id,
                status=JobStatus.COMPLETED,
                filename="escaped.pdf",
                output_dir=str(escaped_output),
            )
        )
        escaped = client.post(
            "/api/jobs/archive",
            headers={"Origin": origin},
            json={"job_ids": [escaped_id]},
        )
        cross_site = client.post(
            "/api/jobs/archive",
            headers={"Origin": "https://malicious.example"},
            json={"job_ids": [queued_id]},
        )

    assert empty.status_code == 422
    assert duplicate.status_code == 422
    assert invalid.status_code == 422
    assert too_many.status_code == 422
    assert unknown.status_code == 404
    assert unfinished.status_code == 409
    assert escaped.status_code == 500
    assert str(tmp_path) not in escaped.text
    assert cross_site.status_code == 403


def test_api_rejects_fake_pdf(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_file_size=1000, max_pages=10)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            files={"file": ("fake.pdf", b"not pdf", "application/pdf")},
            data={"options_json": "{}"},
        )
    assert response.status_code == 400


def test_api_exposes_safe_runtime_config(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "private-data",
        max_file_size=12345,
        max_pages=7,
        max_batch_files=9,
        job_ttl_hours=24,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "max_file_size": 12345,
        "max_pages": 7,
        "max_batch_files": 9,
        "job_ttl_hours": 24,
    }
    assert str(settings.data_dir) not in response.text


def test_api_allows_delete_cors_preflight_for_local_frontend(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/jobs/" + "a" * 32,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "DELETE",
            },
        )

    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize(
    "local_origin",
    [
        "http://127.0.0.1:8000",
        "http://localhost:9137",
    ],
)
def test_api_allows_same_origin_mutations_on_local_ports(
    tmp_path: Path,
    local_origin: str,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings), base_url=local_origin) as client:
        response = client.post(
            "/api/jobs",
            headers={"Origin": local_origin},
        )

    assert response.status_code == 422


def test_api_rejects_cross_site_mutating_request(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            headers={"Origin": "https://malicious.example"},
            files={"file": ("sample.pdf", b"%PDF-placeholder", "application/pdf")},
            data={"options_json": "{}"},
        )

    assert response.status_code == 403
    assert list(settings.jobs_dir.iterdir()) == []


def test_api_rejects_unknown_engine_before_creating_job(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_file_size=1000, max_pages=10)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            files={"file": ("sample.pdf", b"%PDF-placeholder", "application/pdf")},
            data={"options_json": json.dumps({"primary_engine": "unknown"})},
        )
    assert response.status_code == 422
    assert list(settings.jobs_dir.iterdir()) == []


def test_api_rejects_oversized_upload_and_cleans_partial_file(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_file_size=10, max_pages=10)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            files={"file": ("large.pdf", b"%PDF-" + b"x" * 100, "application/pdf")},
            data={"options_json": "{}"},
        )
    assert response.status_code == 413
    assert list(settings.jobs_dir.iterdir()) == []


def test_api_sanitizes_windows_and_posix_filenames(sample_pdf: Path, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_file_size=10_000_000, max_pages=10)
    with TestClient(create_app(settings)) as client, sample_pdf.open("rb") as stream:
        response = client.post(
            "/api/jobs",
            files={"file": ("..\\secret\\report.pdf", stream, "application/pdf")},
            data={
                "options_json": json.dumps(
                    {"primary_engine": "native", "fallback_engine": "native"}
                )
            },
        )
    assert response.status_code == 202
    assert response.json()["filename"] == "report.pdf"
