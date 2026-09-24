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
    # C toolchain + PPA tooling — build-essential compiles termgl's C
    # extension (below); software-properties-common/gnupg let us add
    # deadsnakes for python3.11 (see the render3d venv step below — termgl
    # requires >=3.11, this image's system python3 is 3.10).
    build-essential software-properties-common gnupg \
    # X11/EGL/GL dev headers — needed to COMPILE glcontext (moderngl's
    # native context backend, app/gl_raster.py, docs/gl_raster_benchmark.md)
    # from source. glcontext auto-picks a backend by probing headers at
    # build time; installing all three means it builds successfully
    # whether the target actually has a running X server (x11) or not
    # (egl — the no-X11-needed path the local Windows/moderngl test used).
    # Without these, `pip install moderngl` still succeeds (it's pure
    # Python + prebuilt-wheel friendly) but its glcontext dependency fails
    # to build from source on architectures with no prebuilt wheel (seen
    # on aarch64/gx10: "fatal error: X11/Xlib.h: No such file or
    # directory" building glcontext's x11.cpp).
    libx11-dev libgl1-mesa-dev libegl1-mesa-dev \
    && rm -rf /var/lib/apt/lists/*

# PulseAudio's system-wide mode (startup.sh: `pulseaudio --system`) gates
# every client connection — pactl, paplay, ffmpeg's `-f pulse` input — on
# membership in the "pulse-access" group (the default system.pa's
# `auth-group=pulse-access` on module-native-protocol-unix). Everything in
# this container runs as root with no other user ever created, so without
# this, every Pulse client gets "Access denied": stream_supervisor.py's
# ffmpeg falls back to silent audio, and audio_player.py's paplay fails
# silently (its output is discarded), so narration never reaches the
# stream even though every step upstream of playback works correctly.
RUN usermod -aG pulse-access root

# lsd (prettier ls with icons) — arch-aware: TARGETARCH is set automatically
# by BuildKit (amd64/arm64), lsd's release assets use x86_64/aarch64 triples.
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
        amd64)  LSD_ARCH=x86_64-unknown-linux-gnu ;; \
        arm64)  LSD_ARCH=aarch64-unknown-linux-gnu ;; \
        *) echo "unsupported architecture for lsd: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -sL "https://github.com/lsd-rs/lsd/releases/download/v1.1.1/lsd-v1.1.1-${LSD_ARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin --strip-components=1 "lsd-v1.1.1-${LSD_ARCH}/lsd"

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

# render3d — Python 3.11 venv for termgl (3D terminal rendering; see
# docs/panels.md), used by radar_pane.py, knowledge_graph_pane.py, and
# avatar_providers/termgl_avatar.py. termgl requires Python >=3.11 (uses
# enum.FlagBoundary.CONFORM, added in 3.11) — this image's system python3
# is 3.10 (Ubuntu 22.04's default), so like aider above, it gets its own
# isolated venv rather than forcing every other dependency in
# requirements.txt to resolve against a bumped system interpreter.
# python3.11-dev is needed for the Cython/C-extension build (Python.h).
#
# The FULL requirements.txt (not just termgl+numpy) is installed here too:
# config/panels/avatar.yaml now invokes ALL avatar providers (builtin,
# ascii_avatar, termgl_avatar) under this interpreter — see that file's
# comment — so it needs every dep avatar.py/avatar_providers/* import
# (message_bus's pyyaml/kafka-python, wcwidth, blessed), not just termgl's
# own numpy dependency. radar_pane.py and knowledge_graph_pane.py need
# message_bus too. requirements.txt itself is unaffected (still installs
# against system python3.10 below) — this is a second, independent
# installation of the same file into a second interpreter.
COPY requirements.txt /app/requirements.txt
RUN add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y python3.11 python3.11-venv python3.11-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python3.11 -m venv /opt/render3d \
    && /opt/render3d/bin/pip install --no-cache-dir -r /app/requirements.txt \
    && /opt/render3d/bin/pip install --no-cache-dir termgl

# ── Python dependencies ────────────────────────────────────────────────────────
RUN pip3 install -r /app/requirements.txt

# ── App code ──────────────────────────────────────────────────────────────────
COPY app/ /app/

# Sandbox template — seeds coder workspaces on first startup (workspace_setup.py)
COPY sandbox/ /app/sandbox/

# Vendored repos — currently just ascii-avatar (avatar_providers/ascii_avatar.py
# adds /repos/ascii-avatar/src to sys.path at runtime; ONLY its animation/
# rendering modules are used, see that file for details).
COPY repos/ /repos/

# ── Default config (overridden by mount at runtime) ───────────────────────────
COPY config/worker.yaml /config/worker.yaml

# Panel-type + layout-preset config read by the layout engine (build_layout.py).
# In k8s these become a shared panels ConfigMap + per-role layout ConfigMaps.
COPY config/panels/ /config/panels/
COPY config/layouts/ /config/layouts/
# Console color schemes (app/console_theme.py) — the full Gogh dump, so a
# worker can be retargeted to any of the 1247 built-in schemes without a
# rebuild (config file default + live switch via message-api).
COPY config/themes/ /config/themes/

# ── Startup ───────────────────────────────────────────────────────────────────
COPY startup.sh /startup.sh
RUN chmod +x /startup.sh

# Shared data volumes (world state + workspace repo)
VOLUME ["/data/world-state", "/data/repo"]

ENTRYPOINT ["/startup.sh"]
