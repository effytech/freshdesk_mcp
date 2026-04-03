FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=http \
    FASTMCP_STATELESS_HTTP=true \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 8000

CMD ["freshdesk-mcp"]
