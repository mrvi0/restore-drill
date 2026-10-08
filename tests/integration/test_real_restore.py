"""Real restores against Docker. Dumps are produced by pg_dump inside a helper container."""

import gzip
import io
import json
import tarfile

import pytest
from typer.testing import CliRunner

from restore_drill.cli import app
from restore_drill.drill import DrillConfig, run_drill
from restore_drill.sandbox import LABEL, PostgresSandbox

PG = "16-alpine"

docker = pytest.importorskip("docker")
pytestmark = pytest.mark.docker

SEED = """
CREATE SCHEMA sales;
CREATE TABLE sales.orders (id serial PRIMARY KEY, created_at timestamptz NOT NULL);
INSERT INTO sales.orders (created_at) SELECT now() - i * interval '1 minute' FROM generate_series(1, 5000) i;
CREATE TABLE stale (id int, seen_at timestamp);
INSERT INTO stale VALUES (1, now() - interval '10 days');
"""


@pytest.fixture(scope="session")
def client():
    try:
        c = docker.from_env()
        c.ping()
    except Exception as exc:
        pytest.skip(f"docker not available: {exc}")
    return c


@pytest.fixture(scope="session")
def dumps(client, tmp_path_factory):
    out = tmp_path_factory.mktemp("dumps")
    with PostgresSandbox(PG, client=client) as sb:
        assert sb.container.exec_run(["createdb", "-U", "postgres", "restored"], user="postgres")[0] == 0
        sb.query(SEED)
        for name, args in {"db.dump": ["-Fc"], "db.sql": [], "db.tar": ["-Ft"]}.items():
            code, (_, err) = sb.container.exec_run(
                ["pg_dump", "-U", "postgres", "-d", "restored", *args, "-f", f"/tmp/{name}"],
                user="postgres", demux=True,
            )
            assert code == 0, err
            stream, _ = sb.container.get_archive(f"/tmp/{name}")
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tf:
                (out / name).write_bytes(tf.extractfile(name).read())
    (out / "db.sql.gz").write_bytes(gzip.compress((out / "db.sql").read_bytes()))
    return out


@pytest.fixture(autouse=True)
def no_leftover_containers(client):
    before = {c.id for c in client.containers.list(all=True, filters={"label": LABEL})}
    yield
    after = {c.id for c in client.containers.list(all=True, filters={"label": LABEL})}
    assert after <= before, "restore-drill container was left behind"


def drill(path, **kw):
    return run_drill(DrillConfig(source=str(path), pg_version=PG, **kw))


@pytest.mark.parametrize(("name", "fmt"), [("db.dump", "custom"), ("db.tar", "tar"), ("db.sql", "plain"), ("db.sql.gz", "plain+gzip")])
def test_restores_each_format(dumps, name, fmt):
    r = drill(dumps / name)
    assert r.ok, (r.error, r.restore_errors)
    assert r.format == fmt
    assert r.table_count == 2
    assert r.top_tables[0].name == "sales.orders"
    assert r.top_tables[0].rows == 5000
    assert r.restore_seconds is not None


def test_gzipped_custom(dumps, tmp_path):
    p = tmp_path / "db.dump.gz"
    p.write_bytes(gzip.compress((dumps / "db.dump").read_bytes()))
    r = drill(p)
    assert r.ok, (r.error, r.restore_errors)
    assert r.format == "custom+gzip"


def test_picks_newest_in_directory(dumps):
    r = drill(dumps, pattern="*.dump")
    assert r.ok and r.backup.endswith("db.dump")


def test_freshness_pass_and_fail(dumps):
    ok = drill(dumps / "db.dump", fresh_table="sales.orders", fresh_column="created_at", max_age_seconds=3600)
    assert ok.ok and ok.freshness.ok and ok.freshness.age_seconds < 600

    stale = drill(dumps / "db.dump", fresh_table="stale", fresh_column="seen_at", max_age_seconds=86400)
    assert not stale.ok and not stale.freshness.ok
    assert stale.freshness.age_seconds > 9 * 86400

    missing = drill(dumps / "db.dump", fresh_table="nope", fresh_column="created_at")
    assert not missing.ok and "does not exist" in missing.freshness.error


def test_truncated_custom_dump_fails(dumps, tmp_path):
    p = tmp_path / "broken.dump"
    data = (dumps / "db.dump").read_bytes()
    p.write_bytes(data[: len(data) // 2])
    r = drill(p)
    assert not r.ok
    assert r.restore_error_count > 0


def test_sql_errors_fail(tmp_path):
    p = tmp_path / "bad.sql"
    p.write_text("CREATE TABLE t (id int);\nINSERT INTO t VALUES (1);\nALTER TABLE t OWNER TO app;\n")
    r = drill(p)
    assert not r.ok
    assert r.restore_error_count == 1
    assert 'role "app" does not exist' in r.restore_errors[0]
    assert r.table_count == 1  # partial data still inspected


def test_corrupt_gzip_fails(dumps, tmp_path):
    p = tmp_path / "db.sql.gz"
    data = gzip.compress((dumps / "db.sql").read_bytes())
    p.write_bytes(data[: len(data) - 200])
    r = drill(p)
    assert not r.ok


def test_cli_end_to_end_json(dumps):
    res = CliRunner().invoke(app, ["run", "--source", str(dumps / "db.dump"), "--pg-version", PG, "--json",
                                   "--fresh-table", "sales.orders", "--fresh-column", "created_at"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ok"] and data["total_rows"] == 5001
