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
    # Python runtime
    python3 python3-pip \
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

# ── Python dependencies ────────────────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt

# ── App code ──────────────────────────────────────────────────────────────────
COPY app/ /app/

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
