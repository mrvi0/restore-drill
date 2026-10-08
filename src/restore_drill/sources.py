"""Backup sources: a local file/directory or an S3-compatible bucket.

Sources are strictly read-only: listing and downloading, never writing or deleting.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class BackupRef:
    uri: str
    name: str
    size: int
    modified: datetime


class Source(Protocol):
    def latest(self) -> BackupRef: ...

    def fetch(self, ref: BackupRef, workdir: Path) -> Path:
        """Return a local path to the backup, downloading into workdir if needed."""
        ...


def _newest(refs: list[BackupRef], where: str, pattern: str) -> BackupRef:
    if not refs:
        raise SourceError(f"no backups matching {pattern!r} in {where}")
    # Tie-break on name so the choice is deterministic.
    return max(refs, key=lambda r: (r.modified, r.name))


class LocalSource:
    def __init__(self, path: Path, pattern: str = "*"):
        self.path = path
        self.pattern = pattern

    def _ref(self, p: Path) -> BackupRef:
        st = p.stat()
        return BackupRef(
            uri=str(p),
            name=p.name,
            size=st.st_size,
            modified=datetime.fromtimestamp(st.st_mtime, UTC),
        )

    def latest(self) -> BackupRef:
        if self.path.is_file():
            return self._ref(self.path)
        if not self.path.is_dir():
            raise SourceError(f"{self.path} does not exist")
        refs = [
            self._ref(p)
            for p in self.path.iterdir()
            if p.is_file() and not p.name.startswith(".") and fnmatch.fnmatch(p.name, self.pattern)
        ]
        return _newest(refs, str(self.path), self.pattern)

    def fetch(self, ref: BackupRef, workdir: Path) -> Path:
        return Path(ref.uri)


class S3Source:
    def __init__(self, bucket: str, prefix: str, pattern: str = "*", client: Any = None, endpoint_url: str | None = None):
        self.bucket = bucket
        self.prefix = prefix
        self.pattern = pattern
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url)
        self.client = client

    def latest(self) -> BackupRef:
        refs = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                name = PurePosixPath(key).name
                if not fnmatch.fnmatch(name, self.pattern):
                    continue
                refs.append(
                    BackupRef(
                        uri=f"s3://{self.bucket}/{key}",
                        name=name,
                        size=obj["Size"],
                        modified=obj["LastModified"],
                    )
                )
        return _newest(refs, f"s3://{self.bucket}/{self.prefix}", self.pattern)

    def fetch(self, ref: BackupRef, workdir: Path) -> Path:
        key = ref.uri.removeprefix(f"s3://{self.bucket}/")
        dest = workdir / ref.name
        self.client.download_file(self.bucket, key, str(dest))
        return dest


def open_source(spec: str, pattern: str = "*", s3_endpoint: str | None = None) -> Source:
    if spec.startswith("s3://"):
        parsed = urlparse(spec)
        if not parsed.netloc:
            raise SourceError(f"bad S3 URI {spec!r}, expected s3://bucket/prefix")
        return S3Source(parsed.netloc, parsed.path.lstrip("/"), pattern, endpoint_url=s3_endpoint)
    return LocalSource(Path(spec).expanduser(), pattern)
