FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV PULSE_SINK=vout

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    # Virtual display + terminal
    xvfb xterm \
    # Window manager + resize tool
    openbox xdotool \
    # Terminal multiplexer + tools
    tmux neovim htop tree curl git \
    # Python runtime (venv needed for the isolated aider install below)
    python3 python3-pip python3-venv \
    # Audio
    pulseaudio pulseaudio-utils \
    # Stream broadcaster
    ffmpeg \
    # Misc utilities
    inotify-tools procps wget \
    && rm -rf /var/lib/apt/lists/*

# lsd (prettier ls with icons)
RUN curl -sL https://github.com/lsd-rs/lsd/releases/download/v1.1.1/lsd-v1.1.1-x86_64-unknown-linux-gnu.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=1 lsd-v1.1.1-x86_64-unknown-linux-gnu/lsd

# ── Coding backends (see docs/coding_backend.md) ──────────────────────────────
# Node 18 (required by the OpenCode CLI) via NodeSource.
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# OpenCode CLI — the `opencode` coding backend.
# TODO(queued): pin OPENCODE_VERSION once verified (couldn't check registry
# offline at build-plan time — see .claude/prompts/coding_backend_ab_test.md).
ARG OPENCODE_VERSION=latest
RUN npm install -g opencode-ai@${OPENCODE_VERSION}

# aider — the `aider` coding backend. Installed in its OWN venv so its heavy
# dependency tree can never conflict with the agent runtime's packages; only
# the binary is exposed on PATH.
ARG AIDER_VERSION=""
RUN python3 -m venv /opt/aider \
    && /opt/aider/bin/pip install --no-cache-dir \
        $(test -n "${AIDER_VERSION}" && echo "aider-chat==${AIDER_VERSION}" || echo "aider-chat") \
    && ln -s /opt/aider/bin/aider /usr/local/bin/aider

# ── Python dependencies ────────────────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt

# ── App code ──────────────────────────────────────────────────────────────────
COPY app/ /app/

# Sandbox template — seeds coder workspaces on first startup (workspace_setup.py)
COPY sandbox/ /app/sandbox/

# ── Default config (overridden by mount at runtime) ───────────────────────────
COPY config/worker.yaml /config/worker.yaml

# Panel-type + layout-preset config read by the layout engine (build_layout.py).
# In k8s these become a shared panels ConfigMap + per-role layout ConfigMaps.
COPY config/panels/ /config/panels/
COPY config/layouts/ /config/layouts/

# ── Startup ───────────────────────────────────────────────────────────────────
COPY startup.sh /startup.sh
RUN chmod +x /startup.sh

# Shared data volumes (world state + workspace repo)
VOLUME ["/data/world-state", "/data/repo"]

ENTRYPOINT ["/startup.sh"]
