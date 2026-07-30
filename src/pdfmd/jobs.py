from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from pdfmd.config import Settings
from pdfmd.conversion import ConversionService
from pdfmd.models import ConversionOptions, JobRecord, JobStatus


class JobNotFoundError(KeyError):
    pass


class JobStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    output_dir TEXT,
                    quality_score REAL,
                    options_json TEXT NOT NULL
                )
                """
            )
            # Running work cannot be resumed safely after an application restart.
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, error = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    JobStatus.FAILED.value,
                    "应用重启，任务已中止",
                    "任务在应用重启时尚未完成，请重新提交",
                    now,
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                ),
            )

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, status, filename, progress, stage, created_at, updated_at,
                    error, output_dir, quality_score, options_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_row(record),
            )
        return record

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._from_row(row)

    def list(self, limit: int = 30) -> list[JobRecord]:
        limit = max(1, min(100, limit))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, job_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            if cursor.rowcount == 0:
                raise JobNotFoundError(job_id)

    def update(self, job_id: str, **changes: object) -> JobRecord:
        allowed = {
            "status",
            "progress",
            "stage",
            "error",
            "output_dir",
            "quality_score",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported job fields: {sorted(unknown)}")
        if "status" in changes and isinstance(changes["status"], JobStatus):
            changes["status"] = changes["status"].value
        changes["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{name} = ?" for name in changes)
        values = list(changes.values()) + [job_id]
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608 - fixed allow-list
                values,
            )
            if cursor.rowcount == 0:
                raise JobNotFoundError(job_id)
        return self.get(job_id)

    @staticmethod
    def _to_row(record: JobRecord) -> tuple[object, ...]:
        safe_options = record.options.model_dump(mode="json", exclude={"password"})
        return (
            record.id,
            record.status.value,
            record.filename,
            record.progress,
            record.stage,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
            record.error,
            record.output_dir,
            record.quality_score,
            json.dumps(safe_options, ensure_ascii=False),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            status=JobStatus(row["status"]),
            filename=row["filename"],
            progress=row["progress"],
            stage=row["stage"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            error=row["error"],
            output_dir=row["output_dir"],
            quality_score=row["quality_score"],
            options=ConversionOptions.model_validate_json(row["options_json"]),
        )


class JobManager:
    def __init__(self, settings: Settings, store: JobStore):
        self.settings = settings
        self.store = store
        self.converter = ConversionService(settings)
        self.executor = ThreadPoolExecutor(
            max_workers=settings.max_workers,
            thread_name_prefix="pdfmd-worker",
        )
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.RLock()
        self._shutdown_started = False

    def submit(
        self,
        record: JobRecord,
        input_path: Path,
        output_dir: Path,
        runtime_options: ConversionOptions,
    ) -> None:
        with self._lock:
            if self._shutdown_started:
                raise RuntimeError("job manager is shutting down")
            future = self.executor.submit(
                self._run,
                record.id,
                record.filename,
                input_path,
                output_dir,
                runtime_options,
            )
            self._futures[record.id] = future
            future.add_done_callback(
                lambda completed, job_id=record.id: self._finish(job_id, completed)
            )

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._shutdown_started = True
        self.executor.shutdown(wait=wait, cancel_futures=True)

    def _run(
        self,
        job_id: str,
        source_filename: str,
        input_path: Path,
        output_dir: Path,
        options: ConversionOptions,
    ) -> None:
        self.store.update(job_id, status=JobStatus.RUNNING, progress=1, stage="开始转换")

        def update_progress(value: int, stage: str) -> None:
            self.store.update(job_id, progress=value, stage=stage)

        try:
            result = self.converter.convert(
                input_path,
                output_dir,
                options,
                job_id=job_id,
                source_filename=source_filename,
                progress=update_progress,
            )
            self.store.update(
                job_id,
                status=JobStatus.COMPLETED,
                progress=100,
                stage="转换完成",
                output_dir=result.output_dir,
                quality_score=result.quality.score,
            )
        except Exception as exc:
            self.store.update(
                job_id,
                status=JobStatus.FAILED,
                stage="转换失败",
                error=_safe_error(
                    exc,
                    sensitive_paths=(
                        self.settings.data_dir,
                        self.settings.data_dir.parent,
                        Path.home(),
                    ),
                ),
            )

    def _finish(self, job_id: str, future: Future[None]) -> None:
        if future.cancelled():
            try:
                self.store.update(
                    job_id,
                    status=JobStatus.FAILED,
                    stage="应用关闭，排队任务已取消",
                    error="任务在开始转换前被取消，请重新提交",
                )
            except JobNotFoundError:
                pass
        with self._lock:
            self._futures.pop(job_id, None)


def _safe_error(
    exc: Exception,
    *,
    sensitive_paths: tuple[Path, ...] = (),
) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    for path in sensitive_paths:
        try:
            value = str(path.expanduser().resolve())
        except OSError:
            value = str(path.expanduser())
        for variant in {value, value.replace("\\", "/"), value.replace("/", "\\")}:
            if variant:
                message = message.replace(variant, "<本机路径>")
    return message[:1000]
