import gzip
import io
import tarfile

import pytest

from restore_drill.formats import BackupFormat, Kind, UnsupportedFormat, detect_format


def write(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_custom(tmp_path):
    assert detect_format(write(tmp_path, "x", b"PGDMP\x01\x0e\x00" + b"\x00" * 100)) == BackupFormat(Kind.CUSTOM)


def test_plain_sql(tmp_path):
    fmt = detect_format(write(tmp_path, "x.sql", b"--\n-- PostgreSQL database dump\n--\nCREATE TABLE t (id int);\n"))
    assert fmt == BackupFormat(Kind.PLAIN)
    assert not fmt.uses_pg_restore


def test_plain_sql_with_utf8(tmp_path):
    assert detect_format(write(tmp_path, "x.sql", "INSERT INTO t VALUES ('Привет');\n".encode())).kind is Kind.PLAIN


def test_gzipped_plain(tmp_path):
    fmt = detect_format(write(tmp_path, "x.sql.gz", gzip.compress(b"CREATE TABLE t (id int);\n")))
    assert fmt == BackupFormat(Kind.PLAIN, gzipped=True)
    assert str(fmt) == "plain+gzip"


def test_gzipped_custom(tmp_path):
    fmt = detect_format(write(tmp_path, "x.gz", gzip.compress(b"PGDMP" + b"\x00" * 50)))
    assert fmt == BackupFormat(Kind.CUSTOM, gzipped=True)
    assert fmt.uses_pg_restore


def test_tar(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("toc.dat")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"PGDMP"))
    assert detect_format(write(tmp_path, "x.tar", buf.getvalue())).kind is Kind.TAR


@pytest.mark.parametrize(
    "data",
    [b"", b"\x89PNG\r\n\x1a\n\x00\x00\x00", bytes(range(256))],
    ids=["empty", "png", "binary"],
)
def test_unsupported(tmp_path, data):
    with pytest.raises(UnsupportedFormat):
        detect_format(write(tmp_path, "x", data))


def test_corrupt_gzip(tmp_path):
    with pytest.raises(UnsupportedFormat, match="gzip"):
        detect_format(write(tmp_path, "x.gz", b"\x1f\x8bgarbage"))
