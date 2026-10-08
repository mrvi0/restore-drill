import io
import tarfile

import docker.errors
import pytest

from restore_drill.formats import BackupFormat, Kind
from restore_drill.sandbox import (
    GUNZIP_FAILED,
    PostgresSandbox,
    SandboxError,
    parse_restore_errors,
    restore_command,
    tar_stream,
)


def test_tar_stream_roundtrip(tmp_path):
    p = tmp_path / "dump"
    data = bytes(range(256)) * 41  # not a multiple of 512
    p.write_bytes(data)
    blob = b"".join(tar_stream(p, "backup", chunk_size=1000))
    assert len(blob) % 512 == 0
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        assert tf.getnames() == ["backup"]
        assert tf.extractfile("backup").read() == data


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (BackupFormat(Kind.CUSTOM), "pg_restore -U postgres -d restored --no-owner --no-privileges /tmp/restore-drill-backup"),
        (BackupFormat(Kind.PLAIN), "psql -X -q -U postgres -d restored -f /tmp/restore-drill-backup"),
    ],
)
def test_restore_command_plain_files(fmt, expected):
    assert restore_command(fmt) == ["sh", "-c", expected]


def test_restore_command_gzip_pipes_and_flags_gunzip_failure():
    cmd = restore_command(BackupFormat(Kind.PLAIN, gzipped=True))[2]
    assert cmd.startswith("{ gunzip -c /tmp/restore-drill-backup ||")
    assert GUNZIP_FAILED in cmd
    assert cmd.endswith("| psql -X -q -U postgres -d restored")
    assert "| pg_restore" in restore_command(BackupFormat(Kind.CUSTOM, gzipped=True))[2]


def test_parse_restore_errors():
    stderr = "\n".join([
        "psql:/tmp/restore-drill-backup:10: ERROR:  role \"app\" does not exist",
        "pg_restore: error: could not execute query: ERROR:  relation exists",
        "Command was: CREATE TABLE ...",
        "pg_restore: warning: errors ignored on restore: 2",
        GUNZIP_FAILED,
        "gzip: stdin: unexpected end of file",
    ])
    count, errors = parse_restore_errors(stderr)
    assert count == 3
    assert errors[0].startswith("psql:")
    assert errors[2] == GUNZIP_FAILED


def test_parse_restore_errors_truncates():
    count, errors = parse_restore_errors("\n".join(["ERROR: x"] * 50))
    assert count == 50
    assert len(errors) == 20


class FakeContainer:
    def __init__(self, ready_after=0, status="running", exec_results=None):
        self.status = status
        self.ready_after = ready_after
        self.exec_results = exec_results or {}
        self.execs = []
        self.removed = False

    def reload(self):
        pass

    def logs(self, tail):
        return b"FATAL: boom"

    def exec_run(self, cmd, **kw):
        self.execs.append((cmd, kw))
        if cmd[0] == "pg_isready":
            self.ready_after -= 1
            return (0 if self.ready_after < 0 else 2), b""
        return self.exec_results.get(cmd[0], (0, (b"", b"")))

    def put_archive(self, path, data):
        b"".join(data)
        return True

    def remove(self, force, v):
        assert force
        self.removed = True


class FakeDocker:
    def __init__(self, container, have_image=True):
        self.container = container
        self.have_image = have_image
        self.pulled = []
        self.run_kwargs = None
        outer = self

        class Images:
            def get(self, name):
                if not outer.have_image:
                    raise docker.errors.ImageNotFound(name)

            def pull(self, repo, tag):
                outer.pulled.append((repo, tag))

        class Containers:
            def run(self, image, **kw):
                outer.run_kwargs = dict(kw, image=image)
                return outer.container

        self.images = Images()
        self.containers = Containers()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("restore_drill.sandbox.time.sleep", lambda s: None)


def test_container_is_isolated_and_removed():
    c = FakeContainer(ready_after=2)
    client = FakeDocker(c, have_image=False)
    with PostgresSandbox("16", client=client) as sb:
        assert sb.container is c
    assert c.removed
    assert client.pulled == [("postgres", "16")]
    kw = client.run_kwargs
    assert kw["image"] == "postgres:16"
    assert kw["network_mode"] == "none"
    assert "ports" not in kw
    isready = [cmd for cmd, _ in c.execs if cmd[0] == "pg_isready"]
    assert len(isready) == 3
    assert isready[0] == ["pg_isready", "-h", "127.0.0.1", "-U", "postgres"]


def test_container_removed_on_error_inside_block():
    c = FakeContainer()
    with pytest.raises(RuntimeError):
        with PostgresSandbox("16", client=FakeDocker(c)):
            raise RuntimeError("boom")
    assert c.removed


def test_container_removed_on_keyboard_interrupt():
    c = FakeContainer()
    with pytest.raises(KeyboardInterrupt):
        with PostgresSandbox("16", client=FakeDocker(c)):
            raise KeyboardInterrupt
    assert c.removed


def test_container_removed_when_startup_fails():
    c = FakeContainer(status="exited")
    with pytest.raises(SandboxError, match="exited during startup"):
        PostgresSandbox("16", client=FakeDocker(c)).__enter__()
    assert c.removed


def test_container_removed_on_ready_timeout():
    c = FakeContainer(ready_after=10**9)
    with pytest.raises(SandboxError, match="not ready"):
        PostgresSandbox("16", client=FakeDocker(c), ready_timeout=0.05).__enter__()
    assert c.removed


def test_restore_reports_errors():
    c = FakeContainer(exec_results={"sh": (1, (b"", b"pg_restore: error: could not execute query: ERROR:  x\n"))})
    with PostgresSandbox("16", client=FakeDocker(c)) as sb:
        result = sb.restore(BackupFormat(Kind.CUSTOM))
    assert not result.ok
    assert result.error_count == 1


def test_restore_nonzero_exit_without_error_lines_still_fails():
    c = FakeContainer(exec_results={"sh": (2, (b"", b"could not open input file\n"))})
    with PostgresSandbox("16", client=FakeDocker(c)) as sb:
        result = sb.restore(BackupFormat(Kind.CUSTOM))
    assert not result.ok
    assert result.errors == ["could not open input file"]


def test_query_parses_tab_separated():
    c = FakeContainer(exec_results={"psql": (0, (b"public.a\t10\npublic.b\t5\n", None))})
    with PostgresSandbox("16", client=FakeDocker(c)) as sb:
        assert sb.query("SELECT 1") == [["public.a", "10"], ["public.b", "5"]]
