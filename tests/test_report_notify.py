import io
import json

import pytest

from restore_drill.notify import send_telegram, telegram_text
from restore_drill.report import Freshness, Report, TableStat, human_size, render_human


def sample(**kw):
    base = dict(
        source="s3://b/pg/", pg_version="16", ok=True, backup="s3://b/pg/prod-2026-10-08.dump",
        backup_size_bytes=3 * 1024**3, backup_modified="2026-10-08T03:00:00+00:00", format="custom",
        download_seconds=12.3, restore_seconds=95.0, table_count=2, total_rows=1_234_600,
        top_tables=[TableStat("public.customers_secret", 1_234_567), TableStat("public.x", 33)],
        freshness=Freshness("orders", "created_at", 86400, "2026-10-08 02:00:00+00", 3600, True),
    )
    base.update(kw)
    return Report(**base)


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(3 * 1024**3) == "3.0 GB"


def test_render_human_pass():
    out = render_human(sample())
    assert out.splitlines()[0] == "restore-check: PASS"
    assert "OK in 95.0s (download 12.3s)" in out
    assert "age 1h (max 1d) OK" in out
    assert "1,234,567" in out


def test_render_human_fail_lists_errors():
    out = render_human(sample(ok=False, restore_error_count=25, restore_errors=["ERROR: a", "ERROR: b"]))
    assert out.startswith("restore-check: FAIL")
    assert "FAILED with 25 error(s)" in out
    assert "ERROR: a" in out and "... and 23 more" in out


def test_render_human_early_error():
    out = render_human(Report(source="/backups", pg_version="16", error="source: no backups"))
    assert "source     /backups" in out
    assert "error      source: no backups" in out


def test_json_roundtrip():
    data = json.loads(sample().to_json())
    assert data["ok"] is True
    assert data["restore_seconds"] == 95.0
    assert data["top_tables"][0] == {"name": "public.customers_secret", "rows": 1234567}
    assert data["freshness"]["age_seconds"] == 3600


def test_telegram_text_has_metrics_but_no_schema_or_errors():
    text = telegram_text(sample(ok=False, restore_error_count=1,
                                restore_errors=["ERROR: duplicate key (email)=(bob@example.com)"],
                                error="analyze: connection to host 10.0.0.5 failed"))
    assert "FAIL" in text
    assert "prod-2026-10-08.dump" in text and "s3://" not in text
    assert "restore: 95.0s, errors: 1" in text
    assert "customers_secret" not in text
    assert "bob@example.com" not in text
    assert "10.0.0.5" not in text
    assert "drill error at: analyze" in text


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_send_telegram():
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        return FakeResp(b'{"ok": true}')

    send_telegram("123:abc", "-100", "hi", urlopen=urlopen)
    req = calls[0]
    assert req.full_url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(req.data) == {"chat_id": "-100", "text": "hi"}


def test_send_telegram_api_error():
    with pytest.raises(RuntimeError, match="chat not found"):
        send_telegram("t", "c", "x", urlopen=lambda r, timeout: FakeResp(b'{"ok": false, "description": "chat not found"}'))
