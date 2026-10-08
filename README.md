# restore-drill

Your backups exist. Do they restore?

`restore-check` takes your newest Postgres backup, restores it into a throwaway
`postgres:<version>` Docker container and checks the data is really there.
Run it from cron; it exits `0` on PASS and `1` on FAIL.

- **Your data never leaves your server.** The container has no network and no
  published ports. Notifications carry only pass/fail and metrics — no table
  names, no error text.
- **Read-only.** The tool only lists and downloads backups; it never writes to
  the backup location.
- **No leftovers.** The container is force-removed on success, failure, Ctrl-C
  and SIGTERM.

## Install

As a command-line tool, with [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/)
(both install it into an isolated environment and put `restore-check` on your PATH):

```bash
pipx install git+https://github.com/mrvi0/restore-drill
# or
uv tool install git+https://github.com/mrvi0/restore-drill
```

Or as a Docker image. It starts the sandbox containers through the host Docker daemon,
so it needs the Docker socket:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/backups/pg:/backups:ro \
  ghcr.io/mrvi0/restore-drill run --source /backups --pg-version 16
```

For S3, pass credentials as environment variables (`-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY ...`).

## Usage

```bash
# newest file in a directory (or pass a single file)
restore-check run --source /var/backups/pg --pg-version 16

# S3 or S3-compatible storage (credentials from the usual AWS env vars / profile)
restore-check run --source s3://my-bucket/pg/ --pattern '*.dump' \
  --s3-endpoint https://s3.eu-central-1.wasabisys.com

# also require rows newer than 24h in orders.created_at
restore-check run --source /var/backups/pg \
  --fresh-table orders --fresh-column created_at --max-age 24h

# machine-readable output + Telegram alert
restore-check run --source /var/backups/pg --json \
  --telegram-token "$TOKEN" --telegram-chat "$CHAT_ID"
```

Telegram settings can also come from `RESTORE_CHECK_TELEGRAM_TOKEN` and
`RESTORE_CHECK_TELEGRAM_CHAT`, which keeps the token out of `ps` and crontab.

Cron example:

```cron
0 6 * * * restore-check run --source s3://my-bucket/pg/ --pg-version 16 >> /var/log/restore-check.log 2>&1
```

## What is checked

1. The newest backup (by modification time, filtered by `--pattern`) is picked.
2. The format is detected by content: `pg_dump -Fc`, `pg_dump -Ft`, or plain SQL,
   each optionally gzipped.
3. Archives are restored with `pg_restore --no-owner --no-privileges`; plain SQL with `psql`.
   Any `ERROR` or a non-zero exit code means FAIL.
4. `ANALYZE`, then table count, total rows and the top 10 tables by row count (estimates).
5. Optional freshness: the newest value in `--fresh-column` must be younger than `--max-age`.
6. Restore time (RTO) in seconds, plus download time for S3.

A restore that succeeds but produces no user tables is a FAIL.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | PASS |
| 1 | FAIL (restore errors, stale data, empty backup, or the drill itself broke) |
| 2 | Invalid command-line arguments |

## Known limitations (v0.1)

- Plain SQL dumps are replayed as-is, so `ALTER ... OWNER TO app` fails when role `app`
  does not exist. Dump with `--no-owner --no-privileges`, or treat it as a finding:
  your backup does not restore into an empty server.
- `pg_dumpall` output and directory-format (`-Fd`) dumps are not supported.
- S3 backups are downloaded to `$TMPDIR` first, so you need free space equal to the backup size.

## Development

```bash
uv sync
uv run pytest -m "not docker"   # unit tests
uv run pytest -m docker         # real restores, needs Docker
```

Releases: push a `vX.Y.Z` tag and GitHub Actions publishes the image to
`ghcr.io/mrvi0/restore-drill` (`X.Y.Z`, `X.Y` and `latest` tags, amd64 + arm64).
