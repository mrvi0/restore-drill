from datetime import UTC, datetime
from pathlib import Path

import pytest

from restore_drill.drill import DrillConfig, run_drill
from restore_drill.sandbox import RestoreResult
from restore_drill.sources import BackupRef, SourceError


class FakeSource:
    def __init__(self, path: Path, remote=False):
        self.path = path
        self.remote = remote

    def latest(self):
        uri = f"s3://b/{self.path.name}" if self.remote else str(self.path)
        return BackupRef(uri=uri, name=self.path.name, size=self.path.stat().st_size,
                         modified=datetime(2026, 10, 8, 3, tzinfo=UTC))

    def fetch(self, ref, workdir):
        assert workdir.is_dir()
        return self.path


class FakeSandbox:
    instances = []

    def __init__(self, pg_version, ready_timeout, restore=RestoreResult(0), tables=(("public.orders", "42"),),
                 fresh=("2026-10-08 02:00:00+00", "3600"), fail_on=None):
        self.pg_version = pg_version
        self.restore_result = restore
        self.tables = tables
        self.fresh = fresh
        self.fail_on = fail_on
        self.closed = False
        self.queries = []
        FakeSandbox.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def upload(self, path):
        if self.fail_on == "upload":
            raise RuntimeError("disk full")

    def restore(self, fmt):
        return self.restore_result

    def query(self, sql):
        self.queries.append(sql)
        if "count(*)" in sql:
            return [[str(len(self.tables)), str(sum(int(r) for _, r in self.tables))]]
        if "max(" in sql:
            if self.fail_on == "freshness":
                raise RuntimeError('relation "orders" does not exist\nLINE 1: ...')
            return [list(self.fresh)]
        if "LIMIT" in sql:
            return [list(t) for t in self.tables]
        return []


@pytest.fixture
def dump(tmp_path):
    p = tmp_path / "db.dump"
    p.write_bytes(b"PGDMP" + b"\x00" * 64)
    return p


def drill(dump, sandbox_kw=None, remote=False, **cfg):
    FakeSandbox.instances.clear()
    ticks = iter(range(0, 1000, 5))
    report = run_drill(
        DrillConfig(source=str(dump), **cfg),
        source_factory=lambda *a: FakeSource(dump, remote),
        sandbox_factory=lambda v, ready_timeout: FakeSandbox(v, ready_timeout, **(sandbox_kw or {})),
        clock=lambda: next(ticks),
    )
    return report, (FakeSandbox.instances[0] if FakeSandbox.instances else None)


def test_pass(dump):
    report, sb = drill(dump, pg_version="15")
    assert report.ok, report.error
    assert report.format == "custom"
    assert report.restore_seconds == 5
    assert report.download_seconds is None  # local file, nothing downloaded
    assert report.table_count == 1 and report.total_rows == 42
    assert report.top_tables[0].name == "public.orders"
    assert report.backup_modified == "2026-10-08T03:00:00+00:00"
    assert sb.pg_version == "15" and sb.closed
    assert sb.queries[0] == "ANALYZE"


def test_download_time_measured_for_remote(dump):
    report, _ = drill(dump, remote=True)
    assert report.download_seconds == 5
    assert report.restore_seconds == 5


def test_restore_errors_fail_but_still_inspect(dump):
    report, sb = drill(dump, sandbox_kw={"restore": RestoreResult(1, ["ERROR: x"], 1)})
    assert not report.ok
    assert report.restore_errors == ["ERROR: x"]
    assert report.top_tables  # partial data still reported
    assert sb.closed


def test_empty_database_fails(dump):
    report, _ = drill(dump, sandbox_kw={"tables": ()})
    assert not report.ok
    assert "no user tables" in report.error


def test_fresh_ok(dump):
    report, sb = drill(dump, fresh_table="orders", fresh_column="created_at", max_age_seconds=7200)
    assert report.ok
    assert report.freshness.age_seconds == 3600
    assert any('FROM "orders"' in q for q in sb.queries)


def test_fresh_too_old(dump):
    report, _ = drill(dump, fresh_table="orders", fresh_column="created_at", max_age_seconds=1800)
    assert not report.ok
    assert not report.freshness.ok
    assert report.error is None


def test_fresh_empty_table(dump):
    report, _ = drill(dump, sandbox_kw={"fresh": ("", "")}, fresh_table="orders", fresh_column="created_at")
    assert not report.ok
    assert report.freshness.error == "table is empty"


def test_fresh_query_error_keeps_first_line(dump):
    report, _ = drill(dump, sandbox_kw={"fail_on": "freshness"}, fresh_table="orders", fresh_column="created_at")
    assert not report.ok
    assert report.freshness.error == 'relation "orders" does not exist'


def test_exception_inside_sandbox_is_reported_with_stage(dump):
    report, sb = drill(dump, sandbox_kw={"fail_on": "upload"})
    assert not report.ok
    assert report.error == "upload: disk full"
    assert sb.closed


def test_source_error(tmp_path):
    def bad_source(*a):
        raise SourceError("no backups matching '*' in /x")

    report = run_drill(DrillConfig(source="/x"), source_factory=bad_source)
    assert not report.ok
    assert report.error.startswith("source: no backups")


def test_unsupported_format_never_starts_container(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    report, sb = drill(p)
    assert report.error.startswith("format:")
    assert sb is None
