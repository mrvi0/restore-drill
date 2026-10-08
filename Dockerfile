FROM python:3.12-slim

LABEL org.opencontainers.image.title="restore-drill" \
      org.opencontainers.image.description="Restore your latest Postgres backup in a throwaway container and check the data is really there" \
      org.opencontainers.image.source="https://github.com/mrvi0/restore-drill" \
      org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Needs the host Docker socket: docker run -v /var/run/docker.sock:/var/run/docker.sock ...
# (runs as root by default so it can use the socket; the backup is streamed into the
# sandbox via the Docker API, so no host paths need to be shared with it)
ENTRYPOINT ["restore-check"]
CMD ["--help"]
