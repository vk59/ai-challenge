"""Safe, revision-pinned source collection for day 32 RAG."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

ALLOWED_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".toml", ".json"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
    "build", "coverage", "vendor", ".superpowers",
}
MAX_CORPUS_FILE_BYTES = 256_000
MAX_CORPUS_FILES = 200
MAX_CORPUS_TOTAL_BYTES = 1_000_000

_SECRET_VALUE = re.compile(
    r"(?i)(?:"
    r"sk-[a-z0-9_-]{16,}|"
    r"gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"xox[baprs]-[a-z0-9-]{16,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r")"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|"
    r"client[_-]?secret|password|private[_-]?key|credential)\b\s*[:=]\s*"
    r"(?:['\"][^'\"]*['\"]|[^\s,;}]+)"
)
_SENSITIVE_NAME = re.compile(r"(?i)(?:secret|credential|private[-_]?key)")


def is_indexable_path(path: str) -> bool:
    """Allow source/config text while excluding credentials and generated files."""
    normalized = path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in SKIP_DIRS for part in parts):
        return False
    name = parts[-1]
    lower = name.lower()
    if (lower.startswith(".env") or _SENSITIVE_NAME.search(name)
            or re.match(r"(?i)^(?:access|auth|github|gh|api)[-_]?token", name)):
        return False
    if PurePosixPath(lower).suffix not in ALLOWED_SUFFIXES:
        return False
    if lower.endswith((".lock", "-lock.json", "lock.yaml", "lock.yml")):
        return False
    return True


def contains_secret(text: str) -> bool:
    """Detect common credential formats and quoted sensitive assignments."""
    return bool(_SECRET_VALUE.search(text) or _SENSITIVE_ASSIGNMENT.search(text))


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Не удалось прочитать Git revision ({args[0]})") from exc
    return result.stdout


def collect_documents(repo: Path, base_ref: str) -> list:
    """Return allowlisted documents from a Git tree, never from the PR checkout."""
    from index import Document  # imported after callers set temporary memory dir

    records = _git(Path(repo), "ls-tree", "-r", "-l", "-z", base_ref).split(b"\0")
    documents = []
    total_bytes = 0
    for record in records:
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, oid, size = metadata.decode("ascii").split()
            path = os.fsdecode(raw_path)
            byte_size = int(size)
        except (ValueError, UnicodeDecodeError):
            continue
        if object_type != "blob" or mode not in {"100644", "100755"}:
            continue
        if not is_indexable_path(path) or byte_size > MAX_CORPUS_FILE_BYTES:
            continue
        if len(documents) >= MAX_CORPUS_FILES or total_bytes + byte_size > MAX_CORPUS_TOTAL_BYTES:
            break
        try:
            raw = _git(Path(repo), "cat-file", "blob", oid)
            text = raw.decode("utf-8")
        except (RuntimeError, UnicodeDecodeError):
            continue
        if not text.strip() or "\0" in text or contains_secret(text):
            continue

        suffix = PurePosixPath(path).suffix.lower()
        title = PurePosixPath(path).name
        if suffix == ".md":
            title = next((line[2:].strip() for line in text.splitlines()
                          if line.startswith("# ")), title)
        documents.append(Document(
            path=path,
            title=title,
            kind="python" if suffix == ".py" else "markdown",
            text=text,
        ))
        total_bytes += byte_size
    return documents
