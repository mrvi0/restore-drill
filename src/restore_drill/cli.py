from __future__ import annotations

import signal
import sys
from typing import Annotated, Optional

import typer

from . import __version__
from .checks import parse_duration
from .drill import DrillConfig, run_drill
from .notify import send_telegram, telegram_text
from .report import render_human

app = typer.Typer(add_completion=False, help="Check that your latest Postgres backup actually restores.")


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[Optional[bool], typer.Option("--version", callback=_version, is_eager=True)] = None,
) -> None:
    pass


def _max_age(value: str) -> int:
    try:
        return parse_duration(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def run(
    source: Annotated[str, typer.Option(help="s3://bucket/prefix, a backup file, or a directory of backups.")],
    pg_version: Annotated[str, typer.Option(help="postgres image tag; use your production major version.")] = "16",
    pattern: Annotated[str, typer.Option(help="Glob on file names to pick backups from, e.g. '*.dump'.")] = "*",
    s3_endpoint: Annotated[
        Optional[str], typer.Option(envvar="AWS_ENDPOINT_URL", help="Endpoint for S3-compatible storage.")
    ] = None,
    fresh_table: Annotated[Optional[str], typer.Option(help="Table (or schema.table) that must have recent rows.")] = None,
    fresh_column: Annotated[Optional[str], typer.Option(help="Timestamp column for --fresh-table.")] = None,
    max_age: Annotated[str, typer.Option(help="Max age of the newest row, e.g. 24h, 7d.")] = "24h",
    json_output: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
    telegram_token: Annotated[Optional[str], typer.Option(envvar="RESTORE_CHECK_TELEGRAM_TOKEN")] = None,
    telegram_chat: Annotated[Optional[str], typer.Option(envvar="RESTORE_CHECK_TELEGRAM_CHAT")] = None,
    ready_timeout: Annotated[float, typer.Option(help="Seconds to wait for postgres to start.")] = 120,
) -> None:
    """Restore the newest backup in a throwaway container and check it. Exit 0 on PASS, 1 on FAIL."""
    if bool(fresh_table) != bool(fresh_column):
        raise typer.BadParameter("--fresh-table and --fresh-column go together")
    if bool(telegram_token) != bool(telegram_chat):
        raise typer.BadParameter("--telegram-token and --telegram-chat go together")
    max_age_seconds = _max_age(max_age)

    # Turn SIGTERM (cron timeout, docker stop) into SystemExit so the container is still removed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    report = run_drill(
        DrillConfig(
            source=source,
            pg_version=pg_version,
            pattern=pattern,
            s3_endpoint=s3_endpoint,
            fresh_table=fresh_table,
            fresh_column=fresh_column,
            max_age_seconds=max_age_seconds,
            ready_timeout=ready_timeout,
        )
    )
    typer.echo(report.to_json() if json_output else render_human(report))

    if telegram_token and telegram_chat:
        try:
            send_telegram(telegram_token, telegram_chat, telegram_text(report))
        except Exception as exc:
            typer.echo(f"warning: telegram notification failed: {type(exc).__name__}", err=True)

    raise typer.Exit(0 if report.ok else 1)
