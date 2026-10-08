FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Needs the host Docker socket: docker run -v /var/run/docker.sock:/var/run/docker.sock ...
# (runs as root by default so it can use the socket; the backup is streamed into the
# sandbox via the Docker API, so no host paths need to be shared with it)
ENTRYPOINT ["restore-check"]
CMD ["--help"]
