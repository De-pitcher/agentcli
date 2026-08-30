FROM python:3.12-slim

LABEL maintainer="De-pitcher <emmanwa000@gmail.com>"
LABEL description="agentcli — Budget-conscious, model-agnostic AI agent CLI"

WORKDIR /app

# Copy packaging metadata and source code
COPY pyproject.toml README.md LICENSE ./
COPY agentcli/ ./agentcli/

# Install agentcli into container environment
RUN pip install --no-cache-dir .

# Default configuration directory volume
VOLUME ["/root/.config/agentcli", "/root/.local/share/agentcli"]

ENV OPENROUTER_API_KEY=""

ENTRYPOINT ["agentcli"]
CMD ["chat"]
