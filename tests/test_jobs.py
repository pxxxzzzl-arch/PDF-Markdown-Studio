from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdfmd.config import Settings
from pdfmd.jobs import JobManager, JobNotFoundError, JobStore, _safe_error
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


def test_job_manager_shutdown_waits_and_cancels_pending_work(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, max_workers=1)
    store = JobStore(settings.database_path)
    manager = JobManager(settings, store)
    converter = _BlockingConverter()
    manager.converter = converter

    running = JobRecord(id="f" * 32, status=JobStatus.QUEUED, filename="running.pdf")
    pending = JobRecord(id="0" * 32, status=JobStatus.QUEUED, filename="pending.pdf")
    store.create(running)
    store.create(pending)
    options = ConversionOptions()
    manager.submit(running, tmp_path / "running.pdf", tmp_path / "running", options)
    assert converter.started.wait(timeout=2)
    manager.submit(pending, tmp_path / "pending.pdf", tmp_path / "pending", options)

    with manager._lock:
        pending_future = manager._futures[pending.id]
    pending_cancelled = threading.Event()
    pending_future.add_done_callback(
        lambda future: pending_cancelled.set() if future.cancelled() else None
    )
    shutdown_complete = threading.Event()

    def shutdown() -> None:
        manager.shutdown(wait=True)
        shutdown_complete.set()

    shutdown_thread = threading.Thread(target=shutdown)
    shutdown_thread.start()
    try:
        assert pending_cancelled.wait(timeout=2)
        assert not shutdown_complete.wait(timeout=0.1)
        assert store.get(running.id).status is JobStatus.RUNNING
        cancelled = store.get(pending.id)
        assert cancelled.status is JobStatus.FAILED
        assert "取消" in cancelled.stage

        rejected = JobRecord(id="1" * 32, status=JobStatus.QUEUED, filename="rejected.pdf")
        with pytest.raises(RuntimeError, match="shutting down"):
            manager.submit(rejected, tmp_path / "rejected.pdf", tmp_path / "rejected", options)
    finally:
        converter.release.set()
        shutdown_thread.join(timeout=2)

    assert not shutdown_thread.is_alive()
    assert shutdown_complete.is_set()
    assert converter.calls == [running.id]
    assert store.get(running.id).status is JobStatus.COMPLETED
    assert store.get(pending.id).status is JobStatus.FAILED


def test_safe_error_redacts_local_paths(tmp_path: Path) -> None:
    secret = tmp_path / "jobs" / "secret.pdf"
    message = _safe_error(
        RuntimeError(f"parser failed while reading {secret}"),
        sensitive_paths=(tmp_path,),
    )

    assert str(tmp_path) not in message
    assert "<本机路径>/jobs/secret.pdf" in message


class _BlockingConverter:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []

    def convert(
        self,
        _input_path: Path,
        output_dir: Path,
        _options: ConversionOptions,
        *,
        job_id: str,
        source_filename: str,
        progress: object,
    ) -> SimpleNamespace:
        del source_filename, progress
        self.calls.append(job_id)
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test converter was not released")
        output_dir.mkdir(parents=True)
        return SimpleNamespace(
            output_dir=str(output_dir),
            quality=SimpleNamespace(score=100.0),
        )
