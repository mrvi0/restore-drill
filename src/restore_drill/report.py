"""Drill result and its human / JSON renderings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .checks import format_duration


@dataclass
class TableStat:
    name: str
    rows: int


@dataclass
class Freshness:
    table: str
    column: str
    max_age_seconds: int
    latest: str | None = None
    age_seconds: int | None = None
    ok: bool = False
    error: str | None = None


@dataclass
class Report:
    source: str
    pg_version: str
    ok: bool = False
    backup: str | None = None
    backup_size_bytes: int | None = None
    backup_modified: str | None = None
    format: str | None = None
    download_seconds: float | None = None
    restore_seconds: float | None = None  # RTO
    restore_error_count: int = 0
    restore_errors: list[str] = field(default_factory=list)
    table_count: int | None = None
    total_rows: int | None = None
    top_tables: list[TableStat] = field(default_factory=list)
    freshness: Freshness | None = None
    error: str | None = None  # the drill itself broke (source, docker, ...)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def render_human(r: Report) -> str:
    lines = [f"restore-check: {'PASS' if r.ok else 'FAIL'}"]

    def row(label: str, value: str) -> None:
        lines.append(f"  {label:<10} {value}")

    if r.backup:
        size = f"{human_size(r.backup_size_bytes)}, " if r.backup_size_bytes is not None else ""
        row("backup", f"{r.backup} ({size}modified {r.backup_modified})")
    else:
        row("source", r.source)
    if r.format:
        row("format", r.format)
    row("postgres", r.pg_version)
    if r.restore_seconds is not None:
        status = "OK" if r.restore_error_count == 0 else f"FAILED with {r.restore_error_count} error(s)"
        extra = f" (download {r.download_seconds:.1f}s)" if r.download_seconds else ""
        row("restore", f"{status} in {r.restore_seconds:.1f}s{extra}")
    for err in r.restore_errors:
        lines.append(f"    {err}")
    if r.restore_error_count > len(r.restore_errors):
        lines.append(f"    ... and {r.restore_error_count - len(r.restore_errors)} more")
    if f := r.freshness:
        target = f"{f.table}.{f.column}"
        limit = format_duration(f.max_age_seconds)
        if f.error:
            row("freshness", f"{target}: {f.error} FAIL")
        else:
            verdict = "OK" if f.ok else "FAIL"
            row("freshness", f"{target}: newest {f.latest}, age {format_duration(f.age_seconds or 0)} (max {limit}) {verdict}")
    if r.table_count is not None:
        row("tables", f"{r.table_count} tables, ~{r.total_rows:,} rows")
    if r.top_tables:
        width = max(len(t.name) for t in r.top_tables)
        lines.append("  top tables by rows (estimate after ANALYZE)")
        for t in r.top_tables:
            lines.append(f"    {t.name:<{width}}  {t.rows:>14,}")
    if r.error:
        row("error", r.error)
    return "\n".join(lines)
