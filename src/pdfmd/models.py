from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlockType(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FORMULA = "formula"
    CODE = "code"
    IMAGE = "image"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    RAW_MARKDOWN = "raw_markdown"


class DocumentKind(StrEnum):
    BORN_DIGITAL = "born_digital"
    SCANNED = "scanned"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class OcrMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="ignore")

    left: float
    top: float
    right: float
    bottom: float


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: BlockType
    page: int = Field(ge=1)
    text: str = ""
    bbox: BoundingBox | None = None
    level: int | None = Field(default=None, ge=1, le=6)
    confidence: float | None = Field(default=None, ge=0, le=1)
    engine: str
    asset_path: str | None = None
    table_html: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPage(BaseModel):
    number: int = Field(ge=1)
    width: float | None = None
    height: float | None = None
    blocks: list[DocumentBlock] = Field(default_factory=list)
    engine: str
    source_text_chars: int = 0
    source_image_count: int = 0


class ParsedDocument(BaseModel):
    schema_version: str = "1.0"
    source_filename: str
    source_sha256: str
    title: str | None = None
    page_count: int = Field(ge=0)
    kind: DocumentKind = DocumentKind.UNKNOWN
    pages: list[DocumentPage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pages")
    @classmethod
    def pages_must_be_unique(cls, pages: list[DocumentPage]) -> list[DocumentPage]:
        numbers = [page.number for page in pages]
        if len(numbers) != len(set(numbers)):
            raise ValueError("page numbers must be unique")
        return sorted(pages, key=lambda page: page.number)


class PageInspection(BaseModel):
    number: int
    width: float | None = None
    height: float | None = None
    rotation: int = 0
    native_text_chars: int = 0
    image_count: int = 0
    is_blank: bool = False


class PdfInspection(BaseModel):
    path: str
    filename: str
    sha256: str
    file_size: int
    page_count: int
    encrypted: bool = False
    kind: DocumentKind = DocumentKind.UNKNOWN
    title: str | None = None
    author: str | None = None
    pages: list[PageInspection] = Field(default_factory=list)


class QualityIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str
    page: int | None = None
    block_id: str | None = None


class PageQuality(BaseModel):
    page: int
    score: float = Field(ge=0, le=100)
    extracted_chars: int = 0
    replacement_ratio: float = 0
    duplicate_ratio: float = 0
    needs_fallback: bool = False


class QualityReport(BaseModel):
    score: float = Field(ge=0, le=100)
    passed: bool
    primary_engine: str
    fallback_engine: str | None = None
    fallback_pages: list[int] = Field(default_factory=list)
    issues: list[QualityIssue] = Field(default_factory=list)
    pages: list[PageQuality] = Field(default_factory=list)
    metrics: dict[str, float | int | str | bool] = Field(default_factory=dict)


class ConversionOptions(BaseModel):
    primary_engine: str = "docling"
    fallback_engine: str = "paddleocr"
    ocr_mode: OcrMode = OcrMode.AUTO
    ocr_languages: list[str] = Field(default_factory=lambda: ["ch", "en"])
    # Docling's visual code enrichment is useful for some scans, but can be
    # less accurate than the native text layer for mixed Chinese/code PDFs.
    # Keep it opt-in and retain the deterministic layout recovery by default.
    enable_code_enrichment: bool = False
    extract_images: bool = True
    preserve_page_markers: bool = False
    include_front_matter: bool = True
    enable_quality_fallback: bool = True
    minimum_quality_score: float = Field(default=72, ge=0, le=100)
    password: str | None = Field(default=None, repr=False, exclude=True)

    @field_validator("primary_engine", "fallback_engine")
    @classmethod
    def normalize_engine_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"docling", "native", "paddleocr"}:
            raise ValueError(f"未知解析引擎：{value}")
        return normalized


class EngineStatus(BaseModel):
    name: str
    available: bool
    role: str
    detail: str | None = None


class BatchArchiveRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=100)


class JobRecord(BaseModel):
    id: str
    status: JobStatus
    filename: str
    progress: int = Field(default=0, ge=0, le=100)
    stage: str = "等待处理"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    output_dir: str | None = None
    quality_score: float | None = None
    options: ConversionOptions = Field(default_factory=ConversionOptions)

    @property
    def output_path(self) -> Path | None:
        return Path(self.output_dir) if self.output_dir else None


class JobView(BaseModel):
    """Public job representation that never exposes local filesystem paths."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    filename: str
    progress: int
    stage: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    quality_score: float | None = None
    options: ConversionOptions


class ConversionResult(BaseModel):
    job_id: str | None = None
    output_dir: str
    markdown_path: str
    document_path: str
    quality_path: str
    markdown: str
    document: ParsedDocument
    quality: QualityReport
