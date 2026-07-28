from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.background import BackgroundTask

from pdfmd import __version__
from pdfmd.config import Settings
from pdfmd.engines import engine_statuses
from pdfmd.jobs import JobManager, JobNotFoundError, JobStore
from pdfmd.models import (
    BatchArchiveRequest,
    ConversionOptions,
    JobRecord,
    JobStatus,
    JobView,
    QualityReport,
)

JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
LOCAL_FRONTEND_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
LOCAL_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_BATCH_ARCHIVE_SOURCE_BYTES = 512 * 1024 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    store = JobStore(settings.database_path)
    manager = JobManager(settings, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(
        title="PDF Markdown Studio",
        version=__version__,
        description="本地优先、带质量门控的 PDF 转 Markdown 服务",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.job_store = store
    app.state.job_manager = manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(LOCAL_FRONTEND_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def reject_cross_site_mutations(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.url.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and origin
            and not _is_allowed_mutation_origin(request, origin)
        ):
            return JSONResponse(
                {"detail": "仅允许本地页面修改任务"},
                status_code=403,
            )
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "engines": [item.model_dump() for item in engine_statuses()],
        }

    @app.get("/api/engines")
    def engines() -> list[dict[str, object]]:
        return [item.model_dump() for item in engine_statuses()]

    @app.get("/api/config")
    def public_config() -> dict[str, int]:
        return {
            "max_file_size": settings.max_file_size,
            "max_pages": settings.max_pages,
            "max_batch_files": settings.max_batch_files,
            "job_ttl_hours": settings.job_ttl_hours,
        }

    @app.get("/api/jobs", response_model=list[JobView])
    def list_jobs(limit: int = 30) -> list[JobRecord]:
        return store.list(limit=limit)

    @app.post("/api/jobs", response_model=JobView, status_code=202)
    async def create_job(
        file: Annotated[UploadFile, File()],
        options_json: Annotated[str, Form()] = "{}",
    ) -> JobRecord:
        if not file.filename:
            raise HTTPException(status_code=400, detail="缺少文件名")
        try:
            raw_options = json.loads(options_json)
            options = ConversionOptions.model_validate(raw_options)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=f"转换参数无效：{exc}") from exc

        job_id = uuid.uuid4().hex
        job_dir = settings.jobs_dir / job_id
        input_dir = job_dir / "input"
        output_dir = job_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=False)
        input_path = input_dir / "source.pdf"

        try:
            size = 0
            first_chunk = True
            with input_path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    if first_chunk:
                        if not chunk.startswith(b"%PDF-"):
                            raise HTTPException(status_code=400, detail="上传文件不是有效的 PDF")
                        first_chunk = False
                    size += len(chunk)
                    if size > settings.max_file_size:
                        raise HTTPException(
                            status_code=413,
                            detail=f"文件超过 {settings.max_file_size // 1024 // 1024} MB 限制",
                        )
                    destination.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="上传文件为空")
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        finally:
            await file.close()

        record = JobRecord(
            id=job_id,
            status=JobStatus.QUEUED,
            filename=_safe_filename(file.filename),
            options=options.model_copy(update={"password": None}),
        )
        store.create(record)
        manager.submit(record, input_path, output_dir, options)
        return record

    def build_batch_archive(job_ids: list[str]) -> FileResponse:
        if len(job_ids) > settings.max_batch_files:
            raise HTTPException(
                status_code=422,
                detail=f"单次最多批量下载 {settings.max_batch_files} 份结果",
            )
        if len(job_ids) != len(set(job_ids)):
            raise HTTPException(status_code=422, detail="批量下载任务不能重复")
        if any(not JOB_ID_PATTERN.fullmatch(job_id) for job_id in job_ids):
            raise HTTPException(status_code=422, detail="批量下载包含非法任务编号")

        jobs = [_completed_job(store, job_id) for job_id in job_ids]
        entries: list[tuple[JobRecord, Path, list[Path], str]] = []
        total_size = 0
        for index, job in enumerate(jobs, start=1):
            output_root = _job_output_root(settings, job)
            artifacts = _archive_artifacts(output_root)
            try:
                total_size += sum(path.stat().st_size for path in artifacts)
            except OSError as exc:
                raise HTTPException(status_code=500, detail="无法读取部分结果文件") from exc
            if total_size > MAX_BATCH_ARCHIVE_SOURCE_BYTES:
                raise HTTPException(status_code=413, detail="所选结果包总大小超过 512 MB")
            folder = f"results/{index:02d}-{job.id[:8]}"
            entries.append((job, output_root, artifacts, folder))

        downloads_dir = settings.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        archive = downloads_dir / f".pdf-markdown-batch-{uuid.uuid4().hex}.zip"
        manifest = {
            "format": "pdf-markdown-batch",
            "version": __version__,
            "count": len(entries),
            "jobs": [
                {
                    "job_id": job.id,
                    "source_filename": job.filename,
                    "quality_score": job.quality_score,
                    "directory": folder,
                }
                for job, _, _, folder in entries
            ],
        }
        try:
            with zipfile.ZipFile(
                archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as bundle:
                bundle.writestr(
                    "batch-manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                for _, output_root, artifacts, folder in entries:
                    for path in artifacts:
                        relative = path.relative_to(output_root)
                        bundle.write(path, (Path(folder) / relative).as_posix())
        except Exception as exc:
            archive.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="批量结果包生成失败") from exc

        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"pdf-markdown-batch-{len(entries)}.zip",
            background=BackgroundTask(archive.unlink, missing_ok=True),
        )

    @app.post("/api/jobs/archive")
    def post_batch_archive(payload: BatchArchiveRequest) -> FileResponse:
        return build_batch_archive(payload.job_ids)

    @app.get("/api/jobs/archive")
    def download_batch_archive(
        job_id: Annotated[list[str], Query(min_length=1)],
    ) -> FileResponse:
        return build_batch_archive(job_id)

    @app.get("/api/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: str) -> JobRecord:
        return _get_job(store, job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str) -> Response:
        job = _get_job(store, job_id)
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            raise HTTPException(status_code=409, detail="运行中的任务不能删除")
        job_dir = (settings.jobs_dir / job_id).resolve()
        try:
            job_dir.relative_to(settings.jobs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="非法任务路径") from exc
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=False)
        store.delete(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/markdown")
    def get_markdown(job_id: str) -> FileResponse:
        job = _completed_job(store, job_id)
        path = _result_file(job, "document.md")
        return FileResponse(path, media_type="text/markdown; charset=utf-8", filename="document.md")

    @app.get("/api/jobs/{job_id}/document")
    def get_document(job_id: str) -> FileResponse:
        job = _completed_job(store, job_id)
        return FileResponse(_result_file(job, "document.json"), media_type="application/json")

    @app.get("/api/jobs/{job_id}/quality", response_model=QualityReport)
    def get_quality(job_id: str) -> QualityReport:
        job = _completed_job(store, job_id)
        content = _result_file(job, "quality-report.json").read_text(encoding="utf-8")
        return QualityReport.model_validate_json(content)

    @app.get("/api/jobs/{job_id}/assets/{asset_path:path}")
    def get_asset(job_id: str, asset_path: str) -> FileResponse:
        job = _completed_job(store, job_id)
        root = (Path(job.output_dir or "") / "assets").resolve()
        target = (root / asset_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="非法资源路径") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="资源不存在")
        return FileResponse(target)

    @app.get("/api/jobs/{job_id}/archive")
    def get_archive(job_id: str) -> FileResponse:
        job = _completed_job(store, job_id)
        output_root = _job_output_root(settings, job)
        archive = output_root.parent / "pdf-markdown-result.zip"
        temporary = archive.with_name(f".{archive.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for path in _archive_artifacts(output_root):
                    bundle.write(path, path.relative_to(output_root))
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"{Path(job.filename).stem}-markdown.zip",
        )

    _mount_frontend(app)
    return app


def _is_allowed_mutation_origin(request: Request, origin: str) -> bool:
    if origin in LOCAL_FRONTEND_ORIGINS:
        return True
    if request.url.hostname not in LOCAL_LOOPBACK_HOSTS:
        return False
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    return origin == request_origin


def _get_job(store: JobStore, job_id: str) -> JobRecord:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return store.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


def _completed_job(store: JobStore, job_id: str) -> JobRecord:
    job = _get_job(store, job_id)
    if job.status is not JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="任务尚未完成")
    return job


def _result_file(job: JobRecord, filename: str) -> Path:
    path = Path(job.output_dir or "") / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    return path


def _job_output_root(settings: Settings, job: JobRecord) -> Path:
    expected = (settings.jobs_dir / job.id / "output").resolve()
    actual = Path(job.output_dir or "").resolve()
    if actual != expected:
        raise HTTPException(status_code=500, detail="任务结果目录无效")
    if not actual.is_dir():
        raise HTTPException(status_code=500, detail="任务结果目录不存在")
    return actual


def _archive_artifacts(output_root: Path) -> list[Path]:
    manifest_path = output_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="结果清单损坏，无法生成下载包") from exc

    recorded = manifest.get("files")
    if not isinstance(recorded, list):
        raise HTTPException(status_code=500, detail="结果清单缺少文件列表")

    allowed_root_files = {
        "document.md",
        "document.json",
        "quality-report.json",
        "manifest.json",
    }
    names = [*recorded, "manifest.json"]
    artifacts: dict[str, Path] = {}
    root = output_root.resolve()
    for raw_name in names:
        if not isinstance(raw_name, str):
            raise HTTPException(status_code=500, detail="结果清单包含非法文件名")
        relative = Path(raw_name)
        if relative.is_absolute() or not relative.parts:
            raise HTTPException(status_code=500, detail="结果清单包含非法路径")
        if relative.as_posix() not in allowed_root_files and relative.parts[0] != "assets":
            raise HTTPException(status_code=500, detail="结果清单包含非产物文件")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="结果清单包含越界路径") from exc
        if not target.is_file():
            raise HTTPException(status_code=500, detail=f"结果文件缺失：{relative.as_posix()}")
        artifacts[relative.as_posix()] = target
    return [artifacts[name] for name in sorted(artifacts)]


def _safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.replace("\x00", "").strip()
    return name[:200] or "document.pdf"


def _mount_frontend(app: FastAPI) -> None:
    package_frontend = Path(__file__).resolve().parent / "web"
    project_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    frontend_dist = package_frontend if package_frontend.is_dir() else project_frontend
    if frontend_dist.is_dir():
        static_dir = frontend_dist / "static"
        if static_dir.is_dir():
            app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str = ""):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return FileResponse(frontend_dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def no_frontend() -> dict[str, str]:
            return {
                "message": "PDF Markdown Studio API is running",
                "docs": "/docs",
                "frontend": "run `npm run build` in frontend/",
            }
