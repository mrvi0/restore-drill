import pytest

from restore_drill.checks import format_duration, freshness_sql, parse_duration, quote_ident


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90s", 90), ("30m", 1800), ("24h", 86400), ("7d", 604800), ("1w", 604800), ("1d12h", 129600), (" 2H ", 7200)],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "24", "h", "1x", "-1h", "0h", "1h 30m", "1.5h"])
def test_parse_duration_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize(("seconds", "text"), [(5, "5s"), (60, "1m"), (3 * 3600 + 120, "3h 2m"), (90000, "1d 1h")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_quote_ident():
    assert quote_ident("orders") == '"orders"'
    assert quote_ident("sales.Orders") == '"sales"."Orders"'
    assert quote_ident('we"ird') == '"we""ird"'


@pytest.mark.parametrize("name", ["", "a.b.c", ".t", "s."])
def test_quote_ident_invalid(name):
    with pytest.raises(ValueError):
        quote_ident(name)


def test_freshness_sql_quotes_everything():
    sql = freshness_sql('orders"; DROP TABLE x; --', "created_at")
    assert 'FROM "orders""; DROP TABLE x; --"' in sql
    assert 'max("created_at")' in sql


def test_freshness_sql_rejects_qualified_column():
    with pytest.raises(ValueError):
        freshness_sql("orders", "orders.created_at")
