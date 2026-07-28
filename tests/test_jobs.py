from __future__ import annotations

from pathlib import Path

import pytest

from pdfmd.jobs import JobNotFoundError, JobStore
from pdfmd.models import ConversionOptions, JobRecord, JobStatus


def test_job_store_does_not_persist_password(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    record = JobRecord(
        id="a" * 32,
        status=JobStatus.QUEUED,
        filename="secret.pdf",
        options=ConversionOptions(password="top-secret"),
    )
    store.create(record)
    restored = store.get(record.id)
    assert restored.options.password is None
    assert b"top-secret" not in (tmp_path / "jobs.sqlite3").read_bytes()


def test_job_store_updates_progress(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    record = JobRecord(id="b" * 32, status=JobStatus.QUEUED, filename="test.pdf")
    store.create(record)
    updated = store.update(record.id, status=JobStatus.RUNNING, progress=42, stage="解析中")
    assert updated.status is JobStatus.RUNNING
    assert updated.progress == 42
    assert updated.stage == "解析中"


def test_job_store_marks_interrupted_work_failed_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    queued = JobRecord(id="c" * 32, status=JobStatus.QUEUED, filename="queued.pdf")
    running = JobRecord(id="d" * 32, status=JobStatus.RUNNING, filename="running.pdf")
    store.create(queued)
    store.create(running)

    reopened = JobStore(path)
    for job_id in (queued.id, running.id):
        restored = reopened.get(job_id)
        assert restored.status is JobStatus.FAILED
        assert "应用重启" in restored.stage
        assert restored.error


def test_job_store_delete_and_update_allowlist(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    record = JobRecord(id="e" * 32, status=JobStatus.FAILED, filename="failed.pdf")
    store.create(record)
    with pytest.raises(ValueError, match="unsupported job fields"):
        store.update(record.id, filename="renamed.pdf")

    store.delete(record.id)
    with pytest.raises(JobNotFoundError):
        store.get(record.id)
