"""Storage layer for memory files — handles file I/O, frontmatter parsing, and search."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from typing import List, Optional

import yaml

from .errors import (
    DiskFullError,
    FileNotFoundInMemoryError,
    FileSizeExceededError,
    InvalidMemoryTypeError,
    MissingRequiredFieldError,
)
from .errors import (
    MemoryPermissionError as MemPermissionError,
)
from .models import (
    ManifestEntry,
    MemoryEntry,
    MemoryMetadata,
    MemoryType,
)
from .path_security import PathSecurity

MAX_FILE_SIZE = 50 * 1024
VALID_TYPES = {t.value for t in MemoryType}

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class MemoryStore:
    """File I/O and frontmatter parsing for memory files."""

    def __init__(self, memory_root: str):
        self.memory_root = os.path.abspath(memory_root)
        os.makedirs(self.memory_root, exist_ok=True)

    def _safe_path(self, path: str) -> str:
        return PathSecurity.resolve_memory_path(path, self.memory_root)

    def scan_files(self) -> List[ManifestEntry]:
        """Scan memory directory and return manifest entries (excludes MEMORY.md)."""
        entries: List[ManifestEntry] = []
        if not os.path.isdir(self.memory_root):
            return entries

        for fname in os.listdir(self.memory_root):
            if not fname.endswith(".md") or fname == "MEMORY.md":
                continue
            fpath = os.path.join(self.memory_root, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                metadata, _ = self._parse_file(fpath)
                entries.append(
                    ManifestEntry(
                        filename=fname,
                        name=metadata.name,
                        description=metadata.description,
                        type=metadata.type,
                    )
                )
            except Exception:
                continue
        return entries

    def read_file(self, filename: str) -> MemoryEntry:
        """Read a single memory file and return a MemoryEntry."""
        safe = self._safe_path(filename)
        if not os.path.isfile(safe):
            raise FileNotFoundInMemoryError(filename)

        metadata, content = self._parse_file(safe)
        stat = os.stat(safe)
        created_at = metadata.created or datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        updated_at = metadata.updated or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        freshness, is_stale = self._compute_freshness(updated_at)

        return MemoryEntry(
            id=filename,
            filename=filename,
            name=metadata.name,
            description=metadata.description,
            type=metadata.type,
            content=content,
            created_at=created_at,
            updated_at=updated_at,
            freshness=freshness,
            is_stale=is_stale,
            tags=metadata.tags or [],
        )

    def write_file(self, entry: MemoryEntry) -> None:
        """Atomically write a memory file (temp file + rename)."""
        safe = self._safe_path(entry.filename)
        content = self._serialize(
            metadata=MemoryMetadata(
                name=entry.name,
                description=entry.description,
                type=entry.type,
                tags=entry.tags,
                created=entry.created_at,
                updated=entry.updated_at,
            ),
            body=entry.content,
        )
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_SIZE:
            raise FileSizeExceededError(len(encoded), MAX_FILE_SIZE)

        dir_name = os.path.dirname(safe)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_memory_", suffix=".md")
        try:
            os.write(fd, encoded)
            os.fsync(fd)
            os.close(fd)
            os.replace(tmp_path, safe)
        except OSError as e:
            if hasattr(e, "errno") and e.errno == 28:
                raise DiskFullError()
            raise MemPermissionError(safe)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def delete_file(self, filename: str) -> bool:
        """Delete a memory file. Returns True if deleted, False if file didn't exist."""
        safe = self._safe_path(filename)
        if not os.path.isfile(safe):
            return False
        os.remove(safe)
        return True

    def get_all_files(self) -> List[MemoryEntry]:
        """Return all memory entries by scanning the directory."""
        entries: List[MemoryEntry] = []
        manifest = self.scan_files()
        for m in manifest:
            try:
                entry = self.read_file(m.filename)
                entries.append(entry)
            except Exception:
                continue
        return entries

    def search_fulltext(self, keyword: str, case_sensitive: bool = False) -> List[str]:
        """Full-text search across all memory files. Returns matching filenames."""
        matches: List[str] = []
        search_key = keyword if case_sensitive else keyword.lower()
        for fname in os.listdir(self.memory_root):
            if not fname.endswith(".md") or fname == "MEMORY.md":
                continue
            fpath = os.path.join(self.memory_root, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()
                if not case_sensitive:
                    text = text.lower()
                if search_key in text:
                    matches.append(fname)
            except Exception:
                continue
        return matches

    def _parse_file(self, filepath: str):
        """Parse a memory file into (MemoryMetadata, body_text)."""
        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()

        match = FRONTMATTER_PATTERN.match(raw)
        if not match:
            raise MissingRequiredFieldError("frontmatter", os.path.basename(filepath))

        frontmatter_text = match.group(1)
        body = raw[match.end():].strip()

        try:
            fm = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as e:
            raise MissingRequiredFieldError(
                "frontmatter", os.path.basename(filepath)
            ) from e

        if not isinstance(fm, dict):
            raise MissingRequiredFieldError(
                "frontmatter", os.path.basename(filepath)
            )

        name = fm.get("name", "")
        description = fm.get("description", "")
        if not name:
            raise MissingRequiredFieldError("name", os.path.basename(filepath))
        if not description:
            raise MissingRequiredFieldError("description", os.path.basename(filepath))

        mem_type_str = fm.get("type", "user")
        if mem_type_str not in VALID_TYPES:
            raise InvalidMemoryTypeError(mem_type_str)
        mem_type = MemoryType(mem_type_str)

        tags = fm.get("tags", []) or []

        created = self._parse_datetime(fm.get("created"))
        updated = self._parse_datetime(fm.get("updated"))

        metadata = MemoryMetadata(
            name=name,
            description=description,
            type=mem_type,
            tags=tags,
            created=created,
            updated=updated,
        )
        return metadata, body

    @staticmethod
    def _serialize(metadata: MemoryMetadata, body: str) -> str:
        """Serialize metadata and body into a .md file string."""
        fm: dict = {
            "name": metadata.name,
            "description": metadata.description,
            "type": metadata.type.value,
        }
        if metadata.tags:
            fm["tags"] = metadata.tags
        fm["created"] = (
            metadata.created.isoformat()
            if metadata.created
            else datetime.now(timezone.utc).isoformat()
        )
        fm["updated"] = (
            metadata.updated.isoformat()
            if metadata.updated
            else datetime.now(timezone.utc).isoformat()
        )

        yaml_text = yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()
        return f"---\n{yaml_text}\n---\n\n{body}\n"

    @staticmethod
    def _compute_freshness(updated_at: datetime) -> tuple:
        """Return (freshness_label, is_stale) tuple."""
        now = datetime.now(timezone.utc)
        diff_days = (now - updated_at).days

        if diff_days == 0:
            label = "今天"
            is_stale = False
        elif diff_days == 1:
            label = "昨天"
            is_stale = False
        elif diff_days <= 30:
            label = f"{diff_days}天前"
            is_stale = False
        else:
            label = f"{diff_days}天前"
            is_stale = True

        return label, is_stale

    @staticmethod
    def compute_freshness_bonus(updated_at: datetime) -> float:
        """Compute freshness bonus for ranking (0.0 ~ 1.0)."""
        now = datetime.now(timezone.utc)
        diff_days = (now - updated_at).days

        if diff_days == 0:
            return 1.0
        elif diff_days == 1:
            return 0.9
        elif diff_days <= 7:
            return 0.7
        elif diff_days <= 30:
            return 0.4
        else:
            return 0.1

    @staticmethod
    def _parse_datetime(val) -> Optional[datetime]:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def generate_slug(name: str) -> str:
        """Generate a file-name slug from a memory name."""
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug.strip("-")


def generate_filename(mem_type: MemoryType, name: str) -> str:
    """Generate a filename following the {type}-{slug}.md convention.

    Idempotent: if the slug already starts with the type prefix, use it as-is.
    """
    slug = MemoryStore.generate_slug(name)
    prefix = f"{mem_type.value}-"
    if slug.startswith(prefix):
        return f"{slug}.md"
    return f"{prefix}{slug}.md"
