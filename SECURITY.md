# Security policy

restore-drill reads database backups and talks to the Docker daemon, so security reports
are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub:
[Report a vulnerability](https://github.com/mrvi0/restore-drill/security/advisories/new).

You will get a reply within 3 working days. Please include the version (`restore-check --version`),
how you run it (pipx or Docker image) and steps to reproduce.

## Supported versions

Only the latest release receives fixes.

## Design notes

- The sandbox container runs with `network_mode: none` and no published ports.
- Backup sources are accessed read-only (list and download only).
- Telegram notifications contain only pass/fail and metrics — no table names and no error text,
  because Postgres error messages can quote row values.
- The Docker image needs the host Docker socket, which is root-equivalent on that host.
  Run it only on machines where that is acceptable.
