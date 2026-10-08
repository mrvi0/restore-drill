# restore-drill

[![CI](https://github.com/mrvi0/restore-drill/actions/workflows/ci.yml/badge.svg)](https://github.com/mrvi0/restore-drill/actions/workflows/ci.yml)
[![Docker image](https://img.shields.io/docker/v/mrvi0/restore-drill?sort=semver&label=docker)](https://hub.docker.com/r/mrvi0/restore-drill)
[![Docker pulls](https://img.shields.io/docker/pulls/mrvi0/restore-drill)](https://hub.docker.com/r/mrvi0/restore-drill)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/mrvi0/restore-drill/blob/master/LICENSE)

**Your backups exist. Do they restore?**

`restore-check` takes your newest Postgres backup, restores it into a throwaway
`postgres:<version>` container, checks the data is really there, and exits `0` or `1`.
Put it in cron and find out the morning a backup breaks — not the day you need it.

```console
$ docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v /var/backups/pg:/backups:ro \
    mrvi0/restore-drill run --source /backups --pg-version 16 --fresh-table orders --fresh-column created_at
restore-check: PASS
  backup     /backups/shop-2026-10-08.dump (13.9 MB, modified 2026-10-08T15:09:25+00:00)
  format     custom
  postgres   16
  restore    OK in 1.3s
  freshness  orders.created_at: newest 2026-10-08 15:08:23.982371+00, age 1m (max 1d) OK
  tables     4 tables, ~1,642,032 rows
  top tables by rows (estimate after ANALYZE)
    public.order_items       1,180,544
    public.orders              412,377
    public.customers            48,210
    public.products                901
```

## Why

A backup you have never restored is a hope, not a backup. Dumps break silently:
a missing role, an extension that is not installed, a cron job that wrote an
empty or cut-off file for three weeks. The file is still there, the size looks
fine, and nobody notices until the restore.

Here is the same database with a dump that was cut off mid-write (for example, the disk filled up):

```console
restore-check: FAIL
  backup     /backups/shop-2026-10-09.dump (7.6 MB, modified 2026-10-08T15:09:37+00:00)
  format     custom
  postgres   16
  restore    FAILED with 1 error(s) in 0.8s
    pg_restore: error: could not read from input file: end of file
  tables     4 tables, ~1,228,754 rows
  top tables by rows (estimate after ANALYZE)
    public.order_items       1,180,544
    public.customers            48,210
    public.orders                    0
    public.products                  0
```

Half the tables restored and `orders` is empty. `restore-check` exits `1` and, if configured, sends a Telegram alert.

## Quick start

You need Docker on the machine that can read your backups. Point it at a file, a directory
(the newest file wins) or an S3 prefix:

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/backups/pg:/backups:ro \
  mrvi0/restore-drill run --source /backups --pg-version 16
```

Use the major version of your production server for `--pg-version`.
The image is also on GHCR as `ghcr.io/mrvi0/restore-drill`.

Prefer a native command? Install with [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/);
it still needs Docker to run the sandbox:

```bash
pipx install git+https://github.com/mrvi0/restore-drill
# or
uv tool install git+https://github.com/mrvi0/restore-drill
```

## Your data stays on your server

- **No network in the sandbox.** The restore runs in a container with `network_mode: none`
  and no published ports.
- **Read-only access to backups.** The tool only lists and downloads; it never writes to
  your bucket or backup folder.
- **No leftovers.** The container is force-removed on success, failure, Ctrl-C and SIGTERM.
- **Alerts carry metrics only.** Telegram messages contain pass/fail, restore time and row
  counts — no table names and no error text, because Postgres errors can quote row values.

## What is checked

1. The newest backup is picked (by modification time, filtered by `--pattern`).
2. The format is detected by content: `pg_dump -Fc`, `pg_dump -Ft` or plain SQL, each optionally gzipped.
3. Archives are restored with `pg_restore --no-owner --no-privileges`, plain SQL with `psql`.
   Any `ERROR` or a non-zero exit code is a FAIL.
4. `ANALYZE`, then table count, total rows and the 10 largest tables.
5. Optional freshness: the newest value in `--fresh-column` must be younger than `--max-age`.
6. Restore time (RTO) in seconds, plus download time for S3.

A restore that succeeds but produces no user tables is also a FAIL.

## Usage

```bash
# newest file in a directory, or pass a single file
restore-check run --source /var/backups/pg --pg-version 16

# S3 or S3-compatible storage (credentials from the usual AWS env vars or profile)
restore-check run --source s3://my-bucket/pg/ --pattern '*.dump' \
  --s3-endpoint https://s3.eu-central-1.wasabisys.com

# require rows newer than 24h in orders.created_at
restore-check run --source /var/backups/pg \
  --fresh-table orders --fresh-column created_at --max-age 24h

# machine-readable output and a Telegram alert
restore-check run --source /var/backups/pg --json \
  --telegram-token "$TOKEN" --telegram-chat "$CHAT_ID"
```

With the Docker image, pass S3 credentials as environment variables:
`-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY`.
Telegram settings can come from `RESTORE_CHECK_TELEGRAM_TOKEN` and `RESTORE_CHECK_TELEGRAM_CHAT`,
which keeps the token out of `ps` and your crontab.

### Nightly check with cron

```cron
0 6 * * * restore-check run --source s3://my-bucket/pg/ --pg-version 16 >> /var/log/restore-check.log 2>&1
```

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | PASS |
| 1 | FAIL: restore errors, stale data, an empty backup, or the drill itself broke |
| 2 | Invalid command-line arguments |

### JSON output

`--json` prints the full report for scripts and monitoring (shortened here):

```json
{
  "ok": true,
  "backup": "/backups/shop-2026-10-08.dump",
  "backup_size_bytes": 14577891,
  "format": "custom",
  "restore_seconds": 1.21,
  "restore_error_count": 0,
  "table_count": 4,
  "total_rows": 1642032,
  "top_tables": [{"name": "public.order_items", "rows": 1180544}],
  "freshness": null,
  "error": null
}
```

## Known limitations

- Plain SQL dumps are replayed as-is, so `ALTER ... OWNER TO app` fails when the role `app`
  does not exist. Dump with `--no-owner --no-privileges`, or treat it as a finding:
  your backup does not restore into an empty server.
- `pg_dumpall` output and directory-format (`-Fd`) dumps are not supported yet.
- S3 backups are downloaded to `$TMPDIR` first, so you need free space equal to the backup size.
- The Docker image needs the host Docker socket, which is root-equivalent on that host.

## Roadmap

Open source (this repo): MySQL support, directory-format dumps, more checks.
Tell us what you need in [issues](https://github.com/mrvi0/restore-drill/issues).

**restore-drill Cloud (planned):** an alert when the drill did *not* run, restore history and
RTO trends, one dashboard for all your databases, and a monthly restore report for audits and
clients. The agent stays open source and your data never leaves your servers.

**[Join the early-access list →](https://restore-drill.b4dcat.tech/#early-access)**

## Development

```bash
uv sync
uv run pytest -m "not docker"   # unit tests
uv run pytest -m docker         # real restores, needs Docker
```

Releases: push a `vX.Y.Z` tag and GitHub Actions publishes the image to Docker Hub and GHCR
with `X.Y.Z`, `X.Y` and `latest` tags, for amd64 and arm64.

Security issues: see [SECURITY.md](https://github.com/mrvi0/restore-drill/blob/master/SECURITY.md).

## License

[Apache-2.0](https://github.com/mrvi0/restore-drill/blob/master/LICENSE)
