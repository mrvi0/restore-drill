"""SQL for the post-restore checks and helpers to build it safely."""

from __future__ import annotations

import re

_DURATION_PART = re.compile(r"(\d+)([smhdw])")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int:
    """Parse '90s', '30m', '24h', '7d', '1d12h' into seconds."""
    s = text.strip().lower()
    if not s or _DURATION_PART.sub("", s):
        raise ValueError(f"invalid duration {text!r}, use e.g. 30m, 24h, 7d, 1d12h")
    total = sum(int(n) * _UNIT_SECONDS[u] for n, u in _DURATION_PART.findall(s))
    if total <= 0:
        raise ValueError(f"duration must be positive, got {text!r}")
    return total


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        n, seconds = divmod(seconds, size)
        if n:
            parts.append(f"{n}{unit}")
    return " ".join(parts[:2])


def quote_ident(name: str) -> str:
    """Quote a possibly schema-qualified identifier: 'sales.orders' -> '"sales"."orders"'."""
    parts = name.split(".")
    if not 1 <= len(parts) <= 2 or not all(parts):
        raise ValueError(f"invalid identifier {name!r}, expected table or schema.table")
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


# reltuples is refreshed by ANALYZE; pg_stat counters can lag on PG15+.
TOP_TABLES_SQL = """
SELECT n.nspname || '.' || c.relname, GREATEST(c.reltuples, 0)::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
ORDER BY 2 DESC, 1
LIMIT {limit}
"""

TABLE_STATS_SQL = """
SELECT count(*), COALESCE(sum(GREATEST(c.reltuples, 0)), 0)::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'
"""


def freshness_sql(table: str, column: str) -> str:
    if "." in column:
        raise ValueError(f"invalid column {column!r}")
    col = quote_ident(column)
    return (
        f"SELECT max({col})::timestamptz::text,"
        f" EXTRACT(EPOCH FROM now() - max({col})::timestamptz)::bigint"
        f" FROM {quote_ident(table)}"
    )
