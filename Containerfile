FROM registry.access.redhat.com/ubi9/python-312:latest

USER root 

WORKDIR /app

COPY requirements.txt freshdesk_mcp pyproject.toml uv.lock .python-version ./

RUN pip install -r requirements.txt \
    && chown -R 1001:0 /app

USER 1001
ENTRYPOINT ["python", "server.py"]
