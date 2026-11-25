# wc3files-mcp, the MCP server of wc3files, in a container (#198).
#
#   docker build -t wc3files-mcp .
#   docker run -i --rm -v D:/Maps:/data:ro wc3files-mcp
#
# The server talks MCP over stdin and stdout, so run it with -i. It reads
# only files under /data; mount a folder of maps, models or replays there,
# read-only is enough. Without a mount it starts on an empty folder.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY setup.py MANIFEST.in README.md CHANGELOG.md LICENSE ./
COPY src ./src
RUN pip install ".[mcp]" && rm -rf /src

RUN useradd --create-home wc3files && mkdir /data && chown wc3files /data
USER wc3files
WORKDIR /data

ENTRYPOINT ["wc3files-mcp"]
CMD ["--root", "/data"]
