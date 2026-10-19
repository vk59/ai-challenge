"""Bounded, root-confined file operations. No shell, execution or deletion."""
import difflib
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath

SUFFIXES = {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".js", ".ts", ".tsx", ".txt"}
SKIP = {"node_modules", "venv", "__pycache__", "dist", "build", "memory", "vendor", "reports"}
SECRET = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:api[_-]?key|password|access[_-]?token|secret|credential)\b[\"']?\s*[:=]\s*[\"'][^\"']+[\"'])")


class FileError(Exception):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(message)


def digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Workspace:
    def __init__(self, root, allow_apply=False):
        self.root = Path(root).resolve()
        if not self.root.is_dir() or self.root in {Path(self.root.anchor), Path.home()}:
            raise FileError("invalid_root", "Укажите отдельную директорию проекта.")
        self.allow_apply = allow_apply
        self.reads = {}
        self.staged = {}

    def path(self, name):
        if (not isinstance(name, str) or not 1 <= len(name) <= 240
                or "\\" in name or "\0" in name or ":" in name):
            raise FileError("invalid_path", "Некорректный относительный путь.")
        parts = PurePosixPath(name).parts
        if name != PurePosixPath(name).as_posix():
            raise FileError("invalid_path", "Используйте канонический относительный путь без // и /./.")
        if not parts or PurePosixPath(name).is_absolute() or any(
            part in {"..", "."} or part.startswith(".") or part in SKIP - {"reports"}
            or re.search(r"(?i)secret|credential|private[-_]?key", part) for part in parts
        ):
            raise FileError("protected_path", "Путь защищён или находится вне проекта.")
        target = self.root.joinpath(*parts)
        cursor = self.root
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise FileError("protected_path", "Символические ссылки не поддерживаются.")
        if not target.resolve().is_relative_to(self.root):
            raise FileError("protected_path", "Путь находится вне проекта.")
        if target.suffix.lower() not in SUFFIXES:
            raise FileError("unsupported_file", "Поддерживаются только текстовые файлы проекта.")
        return target

    def list_files(self):
        files = []
        for folder, directories, names in os.walk(self.root, followlinks=False):
            directories[:] = sorted(d for d in directories if not d.startswith(".")
                                    and d not in SKIP and not (Path(folder) / d).is_symlink())
            for name in sorted(names):
                relative = (Path(folder) / name).relative_to(self.root).as_posix()
                try:
                    target = self.path(relative)
                    if not target.is_file():
                        continue
                except FileError:
                    continue
                if len(files) >= 300:
                    return {"files": files, "truncated": True}
                files.append(relative)
        return {"files": files, "truncated": False}

    def _read(self, name):
        target = self.path(name)
        if not target.is_file():
            raise FileError("not_found", "Файл не найден.")
        if target.stat().st_size > 64_000:
            raise FileError("too_large", "Файл превышает лимит чтения 64 КБ.")
        try:
            content = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise FileError("unsupported_file", "Файл не является UTF-8 текстом.") from None
        if "\0" in content or SECRET.search(content):
            raise FileError("protected_content", "Бинарный файл или распознаваемый секрет исключён.")
        return content

    def read_file(self, path):
        content = self._read(path)
        identity = self.path(path).stat()
        self.reads.setdefault(path, (content, digest(content)))
        return {"path": path, "content": content, "sha256": digest(content),
                "file_id": f"{identity.st_dev}:{identity.st_ino}"}

    def search_text(self, query):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise FileError("invalid_query", "Запрос поиска должен содержать 1–200 символов.")
        listing = self.list_files()
        matches, skipped = [], 0
        for path in listing["files"]:
            try:
                content = self._read(path)
            except (FileError, OSError):
                skipped += 1
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if query.lower() in line.lower():
                    if len(matches) >= 80:
                        return {"matches": matches, "truncated": True, "skipped": skipped}
                    matches.append({"path": path, "line": number, "text": line[:240]})
        return {"matches": matches, "truncated": listing["truncated"], "skipped": skipped}

    def propose_file(self, path, content):
        target = self.path(path)
        parts = PurePosixPath(path).parts
        if target.suffix != ".md" or not (
            parts[0] in {"docs", "reports"} or path in {"README.md", "CHANGELOG.md"}
        ):
            raise FileError("write_scope", "Записывать можно только Markdown в docs/, reports/, README.md или CHANGELOG.md.")
        if not isinstance(content, str) or not content.strip() or len(content) > 20_000:
            raise FileError("invalid_content", "Содержимое должно содержать 1–20000 символов.")
        if "\0" in content or SECRET.search(content):
            raise FileError("protected_content", "Содержимое содержит распознаваемый секрет или бинарные данные.")
        if target.exists() and path not in self.reads:
            raise FileError("read_required", "Сначала прочитайте существующий файл через read_file.")
        before, sha = self.reads.get(path, ("", None))
        if content == before:
            self.staged.pop(path, None)
            return {"path": path, "changed": False}
        total = sum(len(entry["after"]) for p, entry in self.staged.items() if p != path) + len(content)
        if (path not in self.staged and len(self.staged) >= 5) or total > 50_000:
            raise FileError("stage_limit", "Лимит: 5 файлов и 50000 символов суммарно.")
        self.staged[path] = {"before": before, "after": content, "sha": sha}
        return {"path": path, "changed": True, "saved": False}

    def show_diff(self):
        parts = []
        for path, entry in sorted(self.staged.items()):
            patch = difflib.unified_diff(entry["before"].splitlines(keepends=True),
                entry["after"].splitlines(keepends=True),
                fromfile="a/" + path if entry["sha"] else "/dev/null", tofile="b/" + path)
            for line in patch:
                parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        return {"diff": "".join(parts), "staged_files": sorted(self.staged)}

    def _check_version(self, path, entry):
        target = self.path(path)
        current = digest(self._read(path)) if target.exists() else None
        if current != entry["sha"]:
            raise FileError("conflict", f"Файл изменился после чтения: {path}. Запустите задачу повторно.")
        return target

    def apply_changes(self):
        if not self.allow_apply:
            raise FileError("apply_disabled", "Запись разрешается только явным --apply при запуске.")
        for path, entry in self.staged.items():
            self._check_version(path, entry)
        applied = []
        try:
            for path, entry in sorted(self.staged.items()):
                target = self._check_version(path, entry)
                target.parent.mkdir(parents=True, exist_ok=True)
                mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
                fd, temporary = tempfile.mkstemp(prefix=".day34-", dir=target.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                        stream.write(entry["after"])
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chmod(temporary, mode)
                    self._check_version(path, entry)
                    os.replace(temporary, target)
                    applied.append(path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        except (FileError, OSError) as exc:
            code = exc.code if isinstance(exc, FileError) else "write_failed"
            raise FileError(code, "Не все изменения применены. Проверьте applied_files и запустите задачу заново.",
                            applied_files=applied) from None
        self.staged.clear()
        return {"applied_files": applied}
