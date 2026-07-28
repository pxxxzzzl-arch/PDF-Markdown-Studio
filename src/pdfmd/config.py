from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    max_file_size: int = 200 * 1024 * 1024
    max_pages: int = 500
    max_batch_files: int = 20
    max_workers: int = 1
    job_ttl_hours: int = 72

    @classmethod
    def from_env(cls) -> Settings:
        default_data = Path.cwd() / "data"
        return cls(
            data_dir=Path(os.getenv("PDFMD_DATA_DIR", default_data)).expanduser().resolve(),
            max_file_size=int(os.getenv("PDFMD_MAX_FILE_SIZE", 200 * 1024 * 1024)),
            max_pages=int(os.getenv("PDFMD_MAX_PAGES", 500)),
            max_batch_files=max(1, min(100, int(os.getenv("PDFMD_MAX_BATCH_FILES", 20)))),
            max_workers=max(1, int(os.getenv("PDFMD_MAX_WORKERS", 1))),
            job_ttl_hours=max(1, int(os.getenv("PDFMD_JOB_TTL_HOURS", 72))),
        )

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    def ensure_directories(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
