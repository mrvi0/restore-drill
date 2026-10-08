"""Throwaway Postgres container: start, load a backup, query, always remove."""

from __future__ import annotations

import os
import secrets
import tarfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .formats import BackupFormat

DB = "restored"
DUMP_PATH = "/tmp/restore-drill-backup"
LABEL = "restore-drill"
GUNZIP_FAILED = "restore-drill: gunzip failed"
_MAX_ERRORS_KEPT = 20


class SandboxError(Exception):
    pass


@dataclass
class RestoreResult:
    exit_code: int
    errors: list[str] = field(default_factory=list)
    error_count: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error_count == 0


def tar_stream(path: Path, arcname: str, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    """Stream a one-file tar archive without buffering the backup in memory or on disk."""
    size = path.stat().st_size
    info = tarfile.TarInfo(arcname)
    info.size = size
    info.mode = 0o644
    info.mtime = int(time.time())
    yield info.tobuf(format=tarfile.GNU_FORMAT)  # GNU handles files > 8 GiB
    sent = 0
    with open(path, "rb") as f:
        while sent < size and (chunk := f.read(min(chunk_size, size - sent))):
            sent += len(chunk)
            yield chunk
    if sent != size:
        raise SandboxError(f"{path} changed while uploading ({sent} of {size} bytes)")
    if pad := -size % 512:
        yield b"\0" * pad
    yield b"\0" * 1024


def restore_command(fmt: BackupFormat) -> list[str]:
    if fmt.uses_pg_restore:
        tool = f"pg_restore -U postgres -d {DB} --no-owner --no-privileges"
        if not fmt.gzipped:
            return ["sh", "-c", f"{tool} {DUMP_PATH}"]
    else:
        tool = f"psql -X -q -U postgres -d {DB}"
        if not fmt.gzipped:
            return ["sh", "-c", f"{tool} -f {DUMP_PATH}"]
    # No pipefail in dash/busybox sh, so report a gunzip failure via stderr.
    return ["sh", "-c", f"{{ gunzip -c {DUMP_PATH} || echo '{GUNZIP_FAILED}' >&2; }} | {tool}"]


def parse_restore_errors(stderr: str) -> tuple[int, list[str]]:
    lines = [
        line.strip()
        for line in stderr.splitlines()
        if "ERROR:" in line or "FATAL:" in line or "error:" in line or line.startswith(GUNZIP_FAILED)
    ]
    return len(lines), lines[:_MAX_ERRORS_KEPT]


class PostgresSandbox:
    """Context manager around a disposable postgres:<version> container.

    The container has no network and no published ports, so restored data
    cannot leave it; it is force-removed on exit, including on errors.
    """

    def __init__(self, pg_version: str, client: Any = None, ready_timeout: float = 120):
        self.image = f"postgres:{pg_version}"
        self.ready_timeout = ready_timeout
        self._client = client
        self.container: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def __enter__(self) -> PostgresSandbox:
        try:
            self._ensure_image()
            self.container = self.client.containers.run(
                self.image,
                detach=True,
                name=f"restore-drill-{os.getpid()}-{secrets.token_hex(3)}",
                environment={"POSTGRES_PASSWORD": secrets.token_urlsafe(16)},
                labels={LABEL: "1"},
                network_mode="none",
                shm_size="256m",
            )
            self._wait_ready()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self.container is not None:
            try:
                self.container.remove(force=True, v=True)
            finally:
                self.container = None

    def _ensure_image(self) -> None:
        import docker.errors

        try:
            self.client.images.get(self.image)
        except docker.errors.ImageNotFound:
            repo, tag = self.image.split(":", 1)
            self.client.images.pull(repo, tag=tag)

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            self.container.reload()
            if self.container.status in ("exited", "dead"):
                logs = self.container.logs(tail=20).decode(errors="replace")
                raise SandboxError(f"postgres container exited during startup:\n{logs}")
            # -h 127.0.0.1: the image's temporary init server listens on the socket only,
            # so this waits for the real server.
            code, _ = self.container.exec_run(
                ["pg_isready", "-h", "127.0.0.1", "-U", "postgres"], user="postgres"
            )
            if code == 0:
                return
            time.sleep(1)
        raise SandboxError(f"postgres not ready after {self.ready_timeout:.0f}s")

    def _exec(self, cmd: list[str]) -> tuple[int, str, str]:
        code, (out, err) = self.container.exec_run(
            cmd,
            user="postgres",
            demux=True,
            environment={"PGOPTIONS": "-c client_min_messages=warning"},
        )
        return code, (out or b"").decode(errors="replace"), (err or b"").decode(errors="replace")

    def upload(self, path: Path) -> None:
        if not self.container.put_archive("/tmp", tar_stream(path, Path(DUMP_PATH).name)):
            raise SandboxError("failed to copy backup into container")

    def restore(self, fmt: BackupFormat) -> RestoreResult:
        code, _, err = self._exec(["createdb", "-U", "postgres", DB])
        if code != 0:
            raise SandboxError(f"createdb failed: {err.strip()}")
        code, _, err = self._exec(restore_command(fmt))
        count, errors = parse_restore_errors(err)
        if code != 0 and not errors:
            errors = [line for line in err.strip().splitlines()[-_MAX_ERRORS_KEPT:]] or [f"exit code {code}"]
            count = len(errors)
        return RestoreResult(exit_code=code, errors=errors, error_count=count)

    def query(self, sql: str) -> list[list[str]]:
        code, out, err = self._exec(["psql", "-X", "-At", "-F", "\t", "-U", "postgres", "-d", DB, "-c", sql])
        if code != 0:
            raise SandboxError(err.strip() or f"psql exit code {code}")
        return [line.split("\t") for line in out.splitlines() if line]
