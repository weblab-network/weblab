FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex@0.160.0
RUN python3 -m venv /opt/mcp
COPY requirements-mcp.txt /tmp/requirements-mcp.txt
RUN /opt/mcp/bin/pip install --no-cache-dir -r /tmp/requirements-mcp.txt
WORKDIR /app
COPY agent_companion.py agent_transport.py agent_auth.py agent_attachments.py ./
COPY tools/weblab_mcp.py ./tools/
COPY LICENSE THIRD_PARTY.md ./
COPY packaging/agent/Codex-LICENSE.txt packaging/agent/Codex-NOTICE.txt /usr/local/share/licenses/codex/
RUN useradd --uid 10001 --create-home agent \
    && mkdir -p /workspace /run/weblab-agent \
    && chown agent:agent /workspace /run/weblab-agent
USER agent
WORKDIR /workspace
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/opt/mcp/bin/python", "/app/agent_companion.py"]
