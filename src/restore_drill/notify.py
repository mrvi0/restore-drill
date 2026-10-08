"""Telegram notification. Sends only pass/fail and metrics — no table names, no error text,
since error messages can quote row values."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

from .checks import format_duration
from .report import Report, human_size

TELEGRAM_API = "https://api.telegram.org"


def telegram_text(r: Report) -> str:
    lines = [f"{'✅' if r.ok else '❌'} restore-check {'PASS' if r.ok else 'FAIL'}"]
    if r.backup:
        size = f" ({human_size(r.backup_size_bytes)})" if r.backup_size_bytes is not None else ""
        lines.append(f"backup: {r.backup.rsplit('/', 1)[-1]}{size}")
    lines.append(f"postgres: {r.pg_version}")
    if r.restore_seconds is not None:
        lines.append(f"restore: {r.restore_seconds:.1f}s, errors: {r.restore_error_count}")
    if r.table_count is not None:
        lines.append(f"tables: {r.table_count}, rows: ~{r.total_rows:,}")
    if f := r.freshness:
        if f.age_seconds is not None:
            lines.append(
                f"freshness: {format_duration(f.age_seconds)} (max {format_duration(f.max_age_seconds)})"
                f" {'OK' if f.ok else 'FAIL'}"
            )
        else:
            lines.append("freshness: FAIL")
    if r.error:
        # Only the stage, not the message: messages can contain hostnames or data.
        lines.append(f"drill error at: {r.error.split(':', 1)[0]}")
    return "\n".join(lines)


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    *,
    urlopen: Callable = urllib.request.urlopen,
    timeout: float = 15,
) -> None:
    req = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read() or b"{}")
    if not body.get("ok"):
        raise RuntimeError(f"telegram API error: {body.get('description', 'unknown')}")
