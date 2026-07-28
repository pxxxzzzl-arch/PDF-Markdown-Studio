from __future__ import annotations

import importlib.util
import math
import re
import statistics
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pdfmd.code_analysis import infer_code_kind, infer_code_language
from pdfmd.models import BlockType, BoundingBox, DocumentBlock

_LONG_SINGLE_LINE = 80
_MIN_SIMILARITY = 0.68
_STATUS_LINE = re.compile(r"^[✓✔✅\s]*\[\d+]\s+\d+(?:\.\d+)?s(?:\s+\d+ms)?\s*$", re.I)
_STANDALONE_NUMBER = re.compile(r"^\d{1,4}$")
_NORMALIZE_PUNCTUATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "（": "(",
        "）": ")",
        "：": ":",
        "，": ",",
    }
)


@dataclass(frozen=True, slots=True)
class OcrRecord:
    box: tuple[tuple[float, float], ...]
    text: str
    confidence: float

    @property
    def left(self) -> float:
        return min(point[0] for point in self.box)

    @property
    def right(self) -> float:
        return max(point[0] for point in self.box)

    @property
    def top(self) -> float:
        return min(point[1] for point in self.box)

    @property
    def bottom(self) -> float:
        return max(point[1] for point in self.box)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass(frozen=True, slots=True)
class OcrCodeCandidate:
    text: str
    confidence: float
    line_count: int
    line_numbers_removed: bool
    repairs: tuple[str, ...] = ()


@dataclass(slots=True)
class EmbeddedImageRecoveryStats:
    candidates_found: int = 0
    embedded_images_found: int = 0
    ocr_pages: int = 0
    blocks_recovered: int = 0


@dataclass(frozen=True, slots=True)
class _PlacedImage:
    name: str
    data: bytes
    width: int
    height: int
    # PDF image unit square -> page coordinates.
    ctm: tuple[float, float, float, float, float, float]

    @property
    def bbox(self) -> BoundingBox:
        points = [_transform(self.ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return BoundingBox(left=min(xs), top=max(ys), right=max(xs), bottom=min(ys))

    def image_point_to_page(self, x: float, y: float) -> tuple[float, float]:
        # OCR/image coordinates start at the top left; PDF image coordinates at
        # the bottom left.
        return _transform(self.ctm, x / self.width, 1 - y / self.height)


@dataclass(slots=True)
class _Row:
    records: list[OcrRecord]

    @property
    def center_y(self) -> float:
        return statistics.mean(record.center_y for record in self.records)

    @property
    def height(self) -> float:
        return statistics.median(record.height for record in self.records)


def reconstruct_ocr_candidates(records: Sequence[OcrRecord]) -> list[OcrCodeCandidate]:
    """Rebuild a screenshot's code layout from OCR boxes.

    One candidate is returned for the complete record set.  A list return type is
    intentional: callers may later add segmentation without changing the public
    API.  The function has no OCR/model dependency and is unit-test friendly.
    """

    clean_records = [record for record in records if record.text.strip()]
    if len(clean_records) < 3:
        return []
    rows = _group_rows(clean_records)
    if len(rows) < 3:
        return []

    char_width = _estimate_char_width(clean_records)
    gutter = _stable_number_gutter(rows)
    body_lefts = [
        record.left
        for row_index, row in enumerate(rows)
        for record_index, record in enumerate(sorted(row.records, key=lambda item: item.left))
        if not (gutter and gutter.get(row_index) == record_index)
        and not _STATUS_LINE.match(record.text.strip())
    ]
    if not body_lefts:
        return []
    minimum_left = min(body_lefts)
    base_cluster = [left for left in body_lefts if left <= minimum_left + char_width * 1.5]
    base_left = statistics.median(base_cluster)

    output: list[str] = []
    previous_number: int | None = None
    confidences: list[float] = []
    for row_index, row in enumerate(rows):
        ordered = sorted(row.records, key=lambda item: item.left)
        gutter_index = gutter.get(row_index) if gutter else None
        number: int | None = None
        if gutter_index is not None:
            number = int(ordered[gutter_index].text.strip())
            ordered = [item for index, item in enumerate(ordered) if index != gutter_index]
            if previous_number is not None and number > previous_number + 1:
                output.extend("" for _ in range(number - previous_number - 1))
            previous_number = number

        if not ordered:
            if number is not None:
                output.append("")
            continue
        text = _join_row(ordered, char_width)
        if _STATUS_LINE.match(text):
            continue
        indent = _indent_width(ordered[0].left - base_left, char_width)
        output.append(" " * indent + text)
        confidences.extend(record.confidence for record in ordered)

    while output and not output[-1].strip():
        output.pop()
    output, repairs = _repair_ocr_code(output)
    if len(output) < 3:
        return []
    confidence = statistics.mean(confidences) if confidences else 0.0
    return [
        OcrCodeCandidate(
            text="\n".join(output),
            confidence=max(0.0, min(1.0, confidence)),
            line_count=len(output),
            line_numbers_removed=bool(gutter),
            repairs=repairs,
        )
    ]


def recover_embedded_image_code(
    pdf_path: Path,
    blocks_by_page: dict[int, list[DocumentBlock]],
) -> EmbeddedImageRecoveryStats:
    """Recover collapsed Docling CODE blocks from embedded screenshots.

    OCR is loaded only after a long, one-line CODE block and an overlapping
    embedded image have both been found.  Any missing optional dependency/model
    or malformed PDF image leaves the original blocks untouched.
    """

    stats = EmbeddedImageRecoveryStats()
    candidates_by_page = {
        page_number: [block for block in blocks if _is_candidate(block)]
        for page_number, blocks in blocks_by_page.items()
    }
    candidates_by_page = {
        page_number: blocks for page_number, blocks in candidates_by_page.items() if blocks
    }
    stats.candidates_found = sum(len(blocks) for blocks in candidates_by_page.values())
    if not candidates_by_page or not _optional_runtime_available():
        return stats

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path), strict=False)
    except Exception:
        return stats

    pages_with_images: dict[int, list[_PlacedImage]] = {}
    for page_number, blocks in candidates_by_page.items():
        if page_number < 1 or page_number > len(reader.pages):
            continue
        try:
            images = [
                image
                for image in _extract_placed_images(reader.pages[page_number - 1])
                if any(_overlap_ratio(block.bbox, image.bbox) >= 0.5 for block in blocks)
            ]
        except Exception:
            continue
        if images:
            pages_with_images[page_number] = images
            stats.embedded_images_found += len(images)
    if not pages_with_images:
        return stats

    engine = _load_rapidocr_engine()
    if engine is None:
        return stats

    ocr_cache: dict[tuple[int, str, int], list[OcrRecord]] = {}
    for page_number, images in pages_with_images.items():
        page_had_ocr = False
        for block in candidates_by_page[page_number]:
            best: tuple[float, OcrCodeCandidate, _PlacedImage] | None = None
            for image in images:
                if _overlap_ratio(block.bbox, image.bbox) < 0.5:
                    continue
                cache_key = (page_number, image.name, len(image.data))
                if cache_key not in ocr_cache:
                    try:
                        ocr_cache[cache_key] = _run_ocr(engine, image.data)
                        page_had_ocr = True
                    except Exception:
                        ocr_cache[cache_key] = []
                selected = _records_for_block(ocr_cache[cache_key], image, block.bbox)
                for recovered in reconstruct_ocr_candidates(selected):
                    similarity = _normalized_similarity(block.text, recovered.text)
                    containment = _normalized_containment(block.text, recovered.text)
                    if similarity < _MIN_SIMILARITY or containment < 0.55:
                        continue
                    score = similarity * 0.8 + recovered.confidence * 0.2
                    if best is None or score > best[0]:
                        best = (score, recovered, image)
            if best is None:
                continue
            score, recovered, image = best
            similarity = _normalized_similarity(block.text, recovered.text)
            block.text = recovered.text
            block.metadata.update(
                {
                    "image_layout_recovered": True,
                    "image_layout_confidence": round(score, 3),
                    "image_layout_similarity": round(similarity, 3),
                    "image_ocr_confidence": round(recovered.confidence, 3),
                    "image_source_name": image.name,
                    "image_line_numbers_removed": recovered.line_numbers_removed,
                    "language": infer_code_language(recovered.text),
                }
            )
            code_kind = infer_code_kind(recovered.text)
            if code_kind:
                block.metadata["code_kind"] = code_kind
            if recovered.repairs:
                block.metadata["image_ocr_repairs"] = list(recovered.repairs)
            stats.blocks_recovered += 1
        if page_had_ocr:
            stats.ocr_pages += 1
    return stats


def _is_candidate(block: DocumentBlock) -> bool:
    return bool(
        block.type is BlockType.CODE
        and not block.metadata.get("layout_recovered")
        and block.bbox is not None
        and len(block.text) >= _LONG_SINGLE_LINE
        and len([line for line in block.text.splitlines() if line.strip()]) == 1
    )


def _optional_runtime_available() -> bool:
    return all(
        importlib.util.find_spec(name) is not None for name in ("docling", "rapidocr", "torch")
    )


def _load_rapidocr_engine() -> Any | None:
    try:
        spec = importlib.util.find_spec("rapidocr")
        if spec is None or spec.origin is None:
            return None
        model_dir = Path(spec.origin).parent / "models"
        model_paths = {
            "Det.model_path": model_dir / "ch_PP-OCRv4_det_mobile.pth",
            "Cls.model_path": model_dir / "ch_ptocr_mobile_v2.0_cls_mobile.pth",
            "Rec.model_path": model_dir / "ch_PP-OCRv4_rec_mobile.pth",
        }
        if not all(path.is_file() for path in model_paths.values()):
            return None

        from rapidocr import (
            EngineType,
            LangCls,
            LangDet,
            LangRec,
            ModelType,
            OCRVersion,
            RapidOCR,
        )

        params: dict[str, Any] = {"Global.log_level": "error"}
        for section, language in (("Det", LangDet.CH), ("Cls", LangCls.CH), ("Rec", LangRec.CH)):
            params.update(
                {
                    f"{section}.engine_type": EngineType.TORCH,
                    f"{section}.ocr_version": OCRVersion.PPOCRV4,
                    f"{section}.model_type": ModelType.MOBILE,
                    f"{section}.lang_type": language,
                }
            )
        params.update({key: str(value) for key, value in model_paths.items()})
        return RapidOCR(params=params)
    except Exception:
        return None


def _run_ocr(engine: Any, image_data: bytes) -> list[OcrRecord]:
    result = engine(image_data, return_word_box=True, text_score=0.35)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None or scores is None:
        return []
    records: list[OcrRecord] = []
    for box, text, confidence in zip(boxes, texts, scores, strict=False):
        points = tuple((float(point[0]), float(point[1])) for point in box)
        if len(points) >= 4 and str(text).strip():
            records.append(OcrRecord(points, str(text).strip(), float(confidence)))
    return records


def _extract_placed_images(page: Any) -> list[_PlacedImage]:
    from pypdf.generic import ContentStream

    stream = ContentStream(page.get_contents(), page.pdf)
    current = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack: list[tuple[float, float, float, float, float, float]] = []
    images: list[_PlacedImage] = []
    for operands, operator in stream.operations:
        if operator == b"q":
            stack.append(current)
        elif operator == b"Q":
            current = stack.pop() if stack else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        elif operator == b"cm" and len(operands) == 6:
            matrix = tuple(float(value) for value in operands)
            current = _multiply_ctm(matrix, current)
        elif operator == b"Do" and operands:
            name = str(operands[0])
            try:
                image_file = page.images[name]
                pil_image = image_file.image
                width, height = pil_image.size
                if width < 200 or height < 80:
                    continue
                images.append(
                    _PlacedImage(
                        name=image_file.name,
                        data=image_file.data,
                        width=int(width),
                        height=int(height),
                        ctm=current,
                    )
                )
            except Exception:
                continue
    return images


def _records_for_block(
    records: Sequence[OcrRecord], image: _PlacedImage, bbox: BoundingBox | None
) -> list[OcrRecord]:
    if bbox is None:
        return []
    margin = max(3.0, abs(bbox.top - bbox.bottom) * 0.015)
    selected: list[OcrRecord] = []
    for record in records:
        center_x = (record.left + record.right) / 2
        page_x, page_y = image.image_point_to_page(center_x, record.center_y)
        if (
            bbox.left - margin <= page_x <= bbox.right + margin
            and bbox.bottom - margin <= page_y <= bbox.top + margin
        ):
            selected.append(record)
    return selected


def _group_rows(records: Sequence[OcrRecord]) -> list[_Row]:
    rows: list[_Row] = []
    for record in sorted(records, key=lambda item: (item.center_y, item.left)):
        best: _Row | None = None
        best_distance = math.inf
        for row in rows:
            distance = abs(record.center_y - row.center_y)
            tolerance = max(3.0, min(record.height, row.height) * 0.55)
            if distance <= tolerance and distance < best_distance:
                best = row
                best_distance = distance
        if best is None:
            rows.append(_Row([record]))
        else:
            best.records.append(record)
    return sorted(rows, key=lambda row: row.center_y)


def _stable_number_gutter(rows: Sequence[_Row]) -> dict[int, int] | None:
    entries: list[tuple[int, int, float]] = []
    for row_index, row in enumerate(rows):
        ordered = sorted(row.records, key=lambda item: item.left)
        if ordered and _STANDALONE_NUMBER.match(ordered[0].text.strip()):
            entries.append((row_index, int(ordered[0].text.strip()), ordered[0].left))
    if len(entries) < 3 or len(entries) / len(rows) < 0.55:
        return None
    numbers = [entry[1] for entry in entries]
    if any(
        right <= left or right - left > 3 for left, right in zip(numbers, numbers[1:], strict=False)
    ):
        return None
    lefts = [entry[2] for entry in entries]
    if max(lefts) - min(lefts) > max(8.0, statistics.median(row.height for row in rows) * 0.45):
        return None
    return {row_index: 0 for row_index, _, _ in entries}


def _estimate_char_width(records: Sequence[OcrRecord]) -> float:
    widths: list[float] = []
    heights = [record.height for record in records]
    for record in records:
        units = sum(
            2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in record.text
        )
        if units >= 2:
            widths.append((record.right - record.left) / units)
    median_height = statistics.median(heights)
    estimate = statistics.median(widths) if widths else median_height * 0.48
    return max(median_height * 0.3, min(median_height * 0.7, estimate))


def _join_row(records: Sequence[OcrRecord], char_width: float) -> str:
    ordered = sorted(records, key=lambda item: item.left)
    output = ordered[0].text.strip()
    previous_right = ordered[0].right
    for record in ordered[1:]:
        gap = max(0.0, record.left - previous_right)
        spaces = 0 if gap < char_width * 0.4 else max(1, round(gap / char_width))
        output += " " * spaces + record.text.strip()
        previous_right = max(previous_right, record.right)
    return output.rstrip()


def _indent_width(delta: float, char_width: float) -> int:
    raw = max(0, round(delta / max(1.0, char_width)))
    if raw <= 1:
        return 0
    return max(0, round(raw / 4) * 4)


def _repair_ocr_code(lines: list[str]) -> tuple[list[str], tuple[str, ...]]:
    repaired = list(lines)
    repairs: list[str] = []
    balance = 0
    for index, line in enumerate(repaired):
        stripped = line.strip()
        if stripped == "C" and balance == 1:
            previous = next(
                (item for item in reversed(repaired[:index]) if item.strip()),
                "",
            )
            following = next(
                (item for item in repaired[index + 1 :] if item.strip()),
                "",
            )
            next_starts_new_statement = bool(
                following.lstrip().startswith("#")
                or re.match(
                    r"\s*(?:[A-Za-z_]\w*\s*=|(?:print|rprint|response)\s*\()",
                    following,
                )
            )
            if previous.rstrip().endswith(",") and next_starts_new_statement:
                repaired[index] = f"{line[: len(line) - len(line.lstrip())]})"
                line = repaired[index]
                repairs.append("standalone_C_to_closing_parenthesis")
        normalized = re.sub(r"\bLoad_dotenv(?=\s*\()", "load_dotenv", line)
        if normalized != line:
            repaired[index] = normalized
            line = normalized
            repairs.append("normalize_load_dotenv_case")
        balance = max(0, balance + _parenthesis_delta(line))
    return repaired, tuple(dict.fromkeys(repairs))


def _parenthesis_delta(line: str) -> int:
    delta = 0
    quote = ""
    escaped = False
    for character in line:
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote:
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "#":
            break
        elif character == "(":
            delta += 1
        elif character == ")":
            delta -= 1
    return delta


def _normalized_similarity(first: str, second: str) -> float:
    left = _normalize_for_match(first)
    right = _normalize_for_match(second)
    if not left or not right:
        return 0.0
    direct = SequenceMatcher(None, left, right, autojunk=False).ratio()
    # Docling often interleaves screenshot gutter numbers into its flattened
    # string.  Ignoring standalone integers is safe for a match score only; OCR
    # text is never copied without the direct structural gates above.
    left_without_numbers = re.sub(r"(?<![\w.])\d{1,4}(?![\w.])", "", first)
    right_without_numbers = re.sub(r"(?<![\w.])\d{1,4}(?![\w.])", "", second)
    numberless = SequenceMatcher(
        None,
        _normalize_for_match(left_without_numbers),
        _normalize_for_match(right_without_numbers),
        autojunk=False,
    ).ratio()
    return max(direct, numberless)


def _normalized_containment(first: str, second: str) -> float:
    left = _normalize_for_match(first)
    right = _normalize_for_match(second)
    if not left or not right:
        return 0.0
    return min(len(left), len(right)) / max(len(left), len(right))


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text.translate(_NORMALIZE_PUNCTUATION)).casefold()


def _overlap_ratio(first: BoundingBox | None, second: BoundingBox) -> float:
    if first is None:
        return 0.0
    width = max(0.0, min(first.right, second.right) - max(first.left, second.left))
    first_low, first_high = sorted((first.bottom, first.top))
    second_low, second_high = sorted((second.bottom, second.top))
    height = max(0.0, min(first_high, second_high) - max(first_low, second_low))
    first_area = max(1.0, abs(first.right - first.left) * abs(first.top - first.bottom))
    return width * height / first_area


def _multiply_ctm(
    first: Iterable[float], second: Iterable[float]
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _transform(
    ctm: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = ctm
    return x * a + y * c + e, x * b + y * d + f
