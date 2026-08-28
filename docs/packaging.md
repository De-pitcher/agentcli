# Packaging & Distribution Guide

`agentcli` is designed for friction-free installation and distribution across multiple environments.

---

## 1. PyPI / Wheel Installation (Recommended)

`agentcli` is packaged as a pure Python wheel (`agentcli-1.0.0-py3-none-any.whl`) and source tarball (`agentcli-1.0.0.tar.gz`) with a single runtime dependency (`httpx>=0.27`).

```bash
# Using pip
pip install agentcli

# Using pipx (isolated application environment)
pipx install agentcli
```

---

## 2. Docker Container

A minimal `Dockerfile` is provided for containerized and isolated deployments:

```bash
# Build the image
docker build -t agentcli .

# Run interactive chat
docker run -it -e OPENROUTER_API_KEY="sk-or-..." agentcli chat

# Or with docker-compose
docker compose run agentcli
```

---

## 3. Standalone Binary (PyInstaller Evaluation)

For environments where Python is not pre-installed, `agentcli` can be compiled into a standalone executable:

```bash
# Install pyinstaller
pip install pyinstaller

# Build single-file executable
pyinstaller --onefile --name agentcli agentcli/__main__.py
```

### PyInstaller Evaluation & Tradeoffs:
- **Executable Size**: ~18–24 MB (incorporates Python 3.12/3.14 embedded runtime, C-extensions for SQLite and OpenSSL, and httpx).
- **Startup Latency**: ~300–450 ms first run (due to temp folder decompression) compared to **< 80 ms** native wheel startup.
- **Recommendation**: Native Python wheel via `pipx` remains the recommended deployment method for development workflows. Standalone binary builds are suitable for CI air-gapped workers or non-Python host distributions.
