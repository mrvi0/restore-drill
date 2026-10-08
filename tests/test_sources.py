import os
from datetime import UTC, datetime

import pytest

from restore_drill.sources import LocalSource, S3Source, SourceError, open_source


def touch(path, mtime, data=b"x"):
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def test_local_file(tmp_path):
    p = touch(tmp_path / "db.dump", 1_700_000_000, b"abc")
    ref = LocalSource(p).latest()
    assert ref.uri == str(p)
    assert ref.size == 3
    assert ref.modified == datetime.fromtimestamp(1_700_000_000, UTC)
    assert LocalSource(p).fetch(ref, tmp_path / "unused") == p


def test_local_dir_picks_newest(tmp_path):
    touch(tmp_path / "old.dump", 1_700_000_000)
    touch(tmp_path / "new.dump", 1_700_000_100)
    touch(tmp_path / ".hidden", 1_700_000_200)
    (tmp_path / "subdir").mkdir()
    assert LocalSource(tmp_path).latest().name == "new.dump"


def test_local_dir_pattern(tmp_path):
    touch(tmp_path / "db.dump", 1_700_000_000)
    touch(tmp_path / "db.dump.sha256", 1_700_000_100)
    assert LocalSource(tmp_path, "*.dump").latest().name == "db.dump"


def test_local_empty_dir(tmp_path):
    with pytest.raises(SourceError, match="no backups"):
        LocalSource(tmp_path).latest()


def test_local_missing(tmp_path):
    with pytest.raises(SourceError, match="does not exist"):
        LocalSource(tmp_path / "nope").latest()


class FakeS3:
    """Read-only surface of boto3's S3 client; any other call fails the test."""

    def __init__(self, pages):
        self.pages = pages
        self.downloads = []
        self.list_calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class P:
            def paginate(self, **kw):
                fake.list_calls.append(kw)
                return iter(fake.pages)

        return P()

    def download_file(self, bucket, key, dest):
        self.downloads.append((bucket, key, dest))
        with open(dest, "wb") as f:
            f.write(b"PGDMP")


def obj(key, day, size=10):
    return {"Key": key, "Size": size, "LastModified": datetime(2026, 10, day, tzinfo=UTC)}


def test_s3_picks_newest_across_pages(tmp_path):
    client = FakeS3([
        {"Contents": [obj("pg/a.dump", 1), obj("pg/", 9)]},
        {"Contents": [obj("pg/b.dump", 3), obj("pg/c.dump", 2)]},
        {},
    ])
    src = S3Source("bkt", "pg/", client=client)
    ref = src.latest()
    assert ref.uri == "s3://bkt/pg/b.dump"
    assert client.list_calls == [{"Bucket": "bkt", "Prefix": "pg/"}]

    path = src.fetch(ref, tmp_path)
    assert path == tmp_path / "b.dump"
    assert client.downloads == [("bkt", "pg/b.dump", str(tmp_path / "b.dump"))]


def test_s3_pattern_and_empty():
    client = FakeS3([{"Contents": [obj("pg/a.log", 5)]}])
    with pytest.raises(SourceError, match="no backups"):
        S3Source("bkt", "pg/", pattern="*.dump", client=client).latest()


def test_open_source_dispatch(tmp_path):
    assert isinstance(open_source(str(tmp_path)), LocalSource)
    s3 = open_source("s3://bucket/some/prefix", s3_endpoint="http://minio:9000")
    assert isinstance(s3, S3Source)
    assert (s3.bucket, s3.prefix) == ("bucket", "some/prefix")
    assert s3.client.meta.endpoint_url == "http://minio:9000"
    with pytest.raises(SourceError):
        open_source("s3:///prefix")
