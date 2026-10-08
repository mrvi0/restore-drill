"""Detect the backup format by content, not by file name."""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

GZIP_MAGIC = b"\x1f\x8b"
CUSTOM_MAGIC = b"PGDMP"  # pg_dump -Fc


class Kind(StrEnum):
    CUSTOM = "custom"  # pg_dump -Fc, restored with pg_restore
    TAR = "tar"  # pg_dump -Ft, restored with pg_restore
    PLAIN = "plain"  # plain SQL, restored with psql


@dataclass(frozen=True)
class BackupFormat:
    kind: Kind
    gzipped: bool = False

    @property
    def uses_pg_restore(self) -> bool:
        return self.kind in (Kind.CUSTOM, Kind.TAR)

    def __str__(self) -> str:
        return f"{self.kind}+gzip" if self.gzipped else str(self.kind)


class UnsupportedFormat(Exception):
    pass


def _classify(head: bytes) -> Kind:
    if head.startswith(CUSTOM_MAGIC):
        return Kind.CUSTOM
    if len(head) >= 262 and head[257:262] == b"ustar":
        return Kind.TAR
    if not head:
        raise UnsupportedFormat("backup is empty")
    # Plain SQL is text: no NUL bytes and mostly printable.
    if b"\x00" in head:
        raise UnsupportedFormat("binary file that is neither pg_dump -Fc nor -Ft")
    printable = len(re.findall(rb"[\t\n\r\x20-\x7e\x80-\xff]", head))
    if printable / len(head) < 0.95:
        raise UnsupportedFormat("file does not look like plain SQL")
    return Kind.PLAIN


def detect_format(path: Path, sample: int = 8192) -> BackupFormat:
    with open(path, "rb") as f:
        head = f.read(sample)
    if head.startswith(GZIP_MAGIC):
        try:
            with gzip.open(path, "rb") as g:
                inner = g.read(sample)
        except (OSError, EOFError) as exc:
            raise UnsupportedFormat(f"corrupt gzip: {exc}") from exc
        return BackupFormat(_classify(inner), gzipped=True)
    return BackupFormat(_classify(head))
