import json

import pytest
from typer.testing import CliRunner

from restore_drill import __version__, cli
from restore_drill.report import Report

runner = CliRunner()


@pytest.fixture
def fake_drill(monkeypatch):
    calls = {"cfg": None, "telegram": []}
    result = {"ok": True}

    def run_drill(cfg):
        calls["cfg"] = cfg
        return Report(source=cfg.source, pg_version=cfg.pg_version, ok=result["ok"], restore_seconds=1.0)

    monkeypatch.setattr(cli, "run_drill", run_drill)
    monkeypatch.setattr(cli, "send_telegram", lambda *a: calls["telegram"].append(a))
    calls["result"] = result
    return calls


def test_pass_exit_0(fake_drill):
    res = runner.invoke(cli.app, ["run", "--source", "/b", "--pg-version", "17"])
    assert res.exit_code == 0, res.output
    assert "restore-check: PASS" in res.output
    assert fake_drill["cfg"].pg_version == "17"


def test_fail_exit_1(fake_drill):
    fake_drill["result"]["ok"] = False
    res = runner.invoke(cli.app, ["run", "--source", "/b"])
    assert res.exit_code == 1
    assert "FAIL" in res.output


def test_json(fake_drill):
    res = runner.invoke(cli.app, ["run", "--source", "/b", "--json"])
    assert json.loads(res.output)["ok"] is True


def test_freshness_options(fake_drill):
    res = runner.invoke(cli.app, ["run", "--source", "/b", "--fresh-table", "orders",
                                  "--fresh-column", "created_at", "--max-age", "6h"])
    assert res.exit_code == 0, res.output
    cfg = fake_drill["cfg"]
    assert (cfg.fresh_table, cfg.fresh_column, cfg.max_age_seconds) == ("orders", "created_at", 21600)


@pytest.mark.parametrize(
    "args",
    [
        ["--fresh-table", "orders"],
        ["--fresh-column", "created_at"],
        ["--fresh-table", "o", "--fresh-column", "c", "--max-age", "soon"],
        ["--telegram-token", "t"],
    ],
)
def test_bad_params_exit_2(fake_drill, args):
    res = runner.invoke(cli.app, ["run", "--source", "/b", *args])
    assert res.exit_code == 2
    assert fake_drill["cfg"] is None


def test_telegram_sent(fake_drill):
    res = runner.invoke(cli.app, ["run", "--source", "/b", "--telegram-token", "t", "--telegram-chat", "c"])
    assert res.exit_code == 0
    token, chat, text = fake_drill["telegram"][0]
    assert (token, chat) == ("t", "c") and "PASS" in text


def test_telegram_from_env(fake_drill):
    env = {"RESTORE_CHECK_TELEGRAM_TOKEN": "t", "RESTORE_CHECK_TELEGRAM_CHAT": "c"}
    runner.invoke(cli.app, ["run", "--source", "/b"], env=env)
    assert fake_drill["telegram"]


def test_telegram_failure_does_not_change_exit_code(fake_drill, monkeypatch):
    def boom(*a):
        raise OSError("network down https://api.telegram.org/botSECRET")

    monkeypatch.setattr(cli, "send_telegram", boom)
    res = runner.invoke(cli.app, ["run", "--source", "/b", "--telegram-token", "SECRET", "--telegram-chat", "c"])
    assert res.exit_code == 0
    assert "SECRET" not in res.output


def test_version():
    res = runner.invoke(cli.app, ["--version"])
    assert res.exit_code == 0 and res.output.strip() == __version__


def test_help_mentions_early_access_once():
    res = runner.invoke(cli.app, ["--help"])
    assert res.exit_code == 0
    assert cli.EARLY_ACCESS_URL in res.output


def test_run_output_has_no_marketing(fake_drill):
    res = runner.invoke(cli.app, ["run", "--source", "/b"])
    assert "early-access" not in res.output
