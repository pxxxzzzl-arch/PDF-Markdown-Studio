from __future__ import annotations

import re
from collections.abc import Iterable

_LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "application/json": "json",
    "json5": "json",
}


def normalize_code_language(language: str) -> str:
    normalized = language.strip().casefold()
    return _LANGUAGE_ALIASES.get(normalized, normalized)


def infer_code_language(text: str) -> str:
    """Infer a fence language only from multiple, reasonably strong signals."""

    lowered = text.casefold()
    if re.search(r"\b(?:const|let|function|interface|export|console\.log)\b", lowered):
        if re.search(r"\binterface\b|:\s*[A-Z][A-Za-z]+", text):
            return "typescript"
        return "javascript"

    python_signals = [
        bool(
            re.search(
                r"\b(?:async|await|class|def|elif|except|from|import|lambda|print|"
                r"raise|return|try|while|with|yield)\b",
                lowered,
            )
        ),
        bool(re.search(r"(?m)^\s*@\w+", text)),
        bool(re.search(r"\.(?:invoke|from_messages)\s*\(", text)),
        bool(
            re.search(
                r"(?m)^\s*[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?\s*=\s*"
                r"(?:[\"'\[{]|[A-Za-z_]\w*\.|\w+\()",
                text,
            )
        ),
        bool(re.search(r"(?m)^\s*#(?!#|\s)", text)),
    ]
    if sum(python_signals) >= 2 or (
        python_signals[0] and re.search(r"(?m)^\s*(?:from|import|class|def|async\s+def)\b", text)
    ):
        return "python"

    if text.lstrip().startswith(("{", "[")) and re.search(r'"[^"\n]+"\s*:', text):
        return "json"
    if re.search(
        r"\b(?:select|insert\s+into|update|delete\s+from|create\s+table)\b",
        lowered,
    ):
        return "sql"
    if re.search(r"^\s*(?:#!/|\$\s)|\b(?:echo|fi|done|export)\b", text, flags=re.MULTILINE):
        return "bash"
    if re.search(r"\b(?:public\s+class|private\s+static|System\.out\.println)\b", text):
        return "java"
    if re.search(r"#include\s*<|\bstd::|\b(?:int|void)\s+main\s*\(", text):
        return "cpp"
    return ""


def resolve_code_language(
    text: str,
    candidates: Iterable[tuple[str, str | None]],
) -> str:
    """Resolve a logical block language from provenance and its complete text."""

    normalized = [
        (normalize_code_language(language), (source or "").strip().casefold())
        for language, source in candidates
        if language and normalize_code_language(language)
    ]
    for language, source in normalized:
        if source == "docling":
            return language

    inferred = infer_code_language(text)
    if inferred:
        return inferred
    return normalized[0][0] if normalized else ""


def infer_code_kind(text: str) -> str:
    """Identify common SDK/debug representations that are output, not source."""

    stripped = text.lstrip()
    if re.match(
        r"^(?:AIMessage|HumanMessage|SystemMessage|ToolMessage|ChatPromptValue)\s*\(",
        stripped,
    ) and re.search(
        r"\b(?:content|additional_kwargs|response_metadata|usage_metadata)\s*=",
        stripped,
    ):
        return "output"
    return ""
