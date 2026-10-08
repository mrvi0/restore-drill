"""One restore drill: pick the newest backup, restore it in a sandbox, run checks."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checks import TABLE_STATS_SQL, TOP_TABLES_SQL, freshness_sql
from .formats import detect_format
from .report import Freshness, Report, TableStat
from .sandbox import PostgresSandbox
from .sources import Source, open_source


@dataclass
class DrillConfig:
    source: str
    pg_version: str = "16"
    pattern: str = "*"
    s3_endpoint: str | None = None
    fresh_table: str | None = None
    fresh_column: str | None = None
    max_age_seconds: int = 24 * 3600
    ready_timeout: float = 120


def run_drill(
    cfg: DrillConfig,
    *,
    source_factory: Callable[..., Source] = open_source,
    sandbox_factory: Callable[..., Any] = PostgresSandbox,
    clock: Callable[[], float] = time.monotonic,
) -> Report:
    report = Report(source=cfg.source, pg_version=cfg.pg_version)
    stage = "source"
    try:
        source = source_factory(cfg.source, cfg.pattern, cfg.s3_endpoint)
        ref = source.latest()
        report.backup = ref.uri
        report.backup_size_bytes = ref.size
        report.backup_modified = ref.modified.isoformat(timespec="seconds")

        with tempfile.TemporaryDirectory(prefix="restore-drill-") as workdir:
            stage = "download"
            t0 = clock()
            path = source.fetch(ref, Path(workdir))
            if path != Path(ref.uri):
                report.download_seconds = round(clock() - t0, 2)

            stage = "format"
            fmt = detect_format(path)
            report.format = str(fmt)

            stage = "container"
            with sandbox_factory(cfg.pg_version, ready_timeout=cfg.ready_timeout) as sb:
                stage = "upload"
                sb.upload(path)

                stage = "restore"
                t0 = clock()
                result = sb.restore(fmt)
                report.restore_seconds = round(clock() - t0, 2)
                report.restore_error_count = result.error_count
                report.restore_errors = result.errors

                # Inspect even after a failed restore: partial data shows how bad it is.
                stage = "analyze"
                sb.query("ANALYZE")
                count, rows = sb.query(TABLE_STATS_SQL)[0]
                report.table_count, report.total_rows = int(count), int(rows)
                report.top_tables = [TableStat(n, int(c)) for n, c in sb.query(TOP_TABLES_SQL.format(limit=10))]

                if cfg.fresh_table:
                    stage = "freshness"
                    report.freshness = _check_freshness(sb, cfg)

        report.ok = (
            result.ok
            and report.table_count > 0
            and (report.freshness is None or report.freshness.ok)
        )
        if result.ok and report.table_count == 0:
            report.error = "restore: backup restored but contains no user tables"
    except Exception as exc:  # report every failure as FAIL instead of a traceback
        report.ok = False
        report.error = f"{stage}: {exc}"
    return report


def _check_freshness(sb: Any, cfg: DrillConfig) -> Freshness:
    assert cfg.fresh_table and cfg.fresh_column
    f = Freshness(table=cfg.fresh_table, column=cfg.fresh_column, max_age_seconds=cfg.max_age_seconds)
    try:
        latest, age = sb.query(freshness_sql(cfg.fresh_table, cfg.fresh_column))[0]
    except Exception as exc:
        f.error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return f
    if not latest:
        f.error = "table is empty"
        return f
    f.latest = latest
    f.age_seconds = int(age)
    f.ok = f.age_seconds <= cfg.max_age_seconds
    return f
