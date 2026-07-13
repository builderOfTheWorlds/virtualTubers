#!/bin/bash
# ============================================================================
# ASCII Avatar — Single-Command Installer
# ============================================================================
# Installs the avatar, downloads TTS models, configures Claude Code hooks,
# and sets up the tmux launcher.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/Angelopvtac/ascii-avatar/master/install.sh | bash
#
# Or locally:
#   bash install.sh
#
# Options:
#   --no-tts        Skip TTS model download (animation only)
#   --no-hooks      Skip Claude Code hooks setup
#   --no-tmux       Skip tmux alias setup
#   --uninstall     Remove everything
# ============================================================================
set -euo pipefail

# --- Parse flags -----------------------------------------------------------
INSTALL_TTS=true
INSTALL_HOOKS=true
INSTALL_TMUX=true
UNINSTALL=false

for arg in "$@"; do
    case "$arg" in
        --no-tts)    INSTALL_TTS=false ;;
        --no-hooks)  INSTALL_HOOKS=false ;;
        --no-tmux)   INSTALL_TMUX=false ;;
        --uninstall) UNINSTALL=true ;;
    esac
done

# --- Colors ----------------------------------------------------------------
CYAN='\033[38;2;0;200;180m'
MAGENTA='\033[38;2;255;0;170m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

info()  { echo -e "${CYAN}[*]${RESET} $1"; }
ok()    { echo -e "${CYAN}[+]${RESET} $1"; }
warn()  { echo -e "${MAGENTA}[!]${RESET} $1"; }
step()  { echo -e "\n${BOLD}${CYAN}=== $1 ===${RESET}"; }

# --- Uninstall -------------------------------------------------------------
if [ "$UNINSTALL" = true ]; then
    step "Uninstalling ASCII Avatar"
    pipx uninstall ascii-avatar 2>/dev/null && ok "Removed pipx package" || true
    rm -rf "$HOME/.cache/ascii-avatar" && ok "Removed TTS models"
    rm -f "$HOME/.claude/agents/avatar-start.sh" && ok "Removed launcher"
    # Remove hooks from Claude settings (leave file intact)
    if [ -f "$HOME/.claude/settings.json" ]; then
        warn "Check ~/.claude/settings.json — remove avatar hook entries manually"
    fi
    # Remove bash alias
    if grep -q "ascii-avatar" "$HOME/.bashrc" 2>/dev/null; then
        sed -i '/# ASCII Avatar/d;/clauded-avatar/d' "$HOME/.bashrc"
        ok "Removed bash alias"
    fi
    ok "Uninstall complete"
    exit 0
fi

# --- Banner ----------------------------------------------------------------
echo -e "${CYAN}"
cat << 'BANNER'
     _    ____   ____ ___ ___      _    _     _____  _____  _    ____
    / \  / ___| / ___|_ _|_ _|   / \  | |   |_   _|/ _ \ \/ /  |  _ \
   / _ \ \___ \| |    | | | |   / _ \ | |     | | | |_| |>  <   | |_) |
  / ___ \ ___) | |___ | | | |  / ___ \| |___  | | |  _  / /\ \  |  _ <
 /_/   \_\____/ \____|___|___| /_/   \_\_____| |_| |_| |_/_/  \_\|_| \_\
BANNER
echo -e "${RESET}"
info "Single-command installer for the ASCII Avatar companion"
echo ""

# --- Preflight checks ------------------------------------------------------
step "Checking prerequisites"

# Python 3.11+
if ! command -v python3 &>/dev/null; then
    warn "python3 not found. Install Python 3.11+ first."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    warn "Python $PY_VER found, but 3.11+ required."
    exit 1
fi
ok "Python $PY_VER"

# pipx
if ! command -v pipx &>/dev/null; then
    info "Installing pipx..."
    python3 -m pip install --user pipx 2>/dev/null || pip install --user pipx
    python3 -m pipx ensurepath 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi
ok "pipx available"

# portaudio (required for sounddevice)
PORTAUDIO_OK=false
if pkg-config --exists portaudio-2.0 2>/dev/null; then
    PORTAUDIO_OK=true
elif [ -f /usr/include/portaudio.h ] || [ -f /usr/local/include/portaudio.h ]; then
    PORTAUDIO_OK=true
elif ldconfig -p 2>/dev/null | grep -q libportaudio; then
    PORTAUDIO_OK=true
fi

if [ "$PORTAUDIO_OK" = false ]; then
    warn "portaudio dev headers not found. TTS audio requires them."
    echo ""
    if command -v apt &>/dev/null; then
        echo -e "  ${BOLD}sudo apt install portaudio19-dev${RESET}"
    elif command -v dnf &>/dev/null; then
        echo -e "  ${BOLD}sudo dnf install portaudio-devel${RESET}"
    elif command -v brew &>/dev/null; then
        echo -e "  ${BOLD}brew install portaudio${RESET}"
    elif command -v pacman &>/dev/null; then
        echo -e "  ${BOLD}sudo pacman -S portaudio${RESET}"
    fi
    echo ""
    read -p "Continue without portaudio? (avatar works, TTS won't) [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    ok "portaudio"
fi

# tmux (optional but recommended)
if command -v tmux &>/dev/null; then
    ok "tmux $(tmux -V | cut -d' ' -f2)"
else
    warn "tmux not found — avatar will work standalone but tmux integration unavailable"
    INSTALL_TMUX=false
fi

# --- Install package -------------------------------------------------------
step "Installing ascii-avatar"

REPO_URL="git+https://github.com/Angelopvtac/ascii-avatar.git"

if pipx list 2>/dev/null | grep -q "ascii-avatar"; then
    info "Upgrading existing installation..."
    pipx upgrade ascii-avatar 2>/dev/null || pipx install --force "${REPO_URL}[kokoro]"
else
    info "Installing from GitHub..."
    pipx install "${REPO_URL}[kokoro]"
fi

# Verify install
if command -v avatar &>/dev/null; then
    ok "avatar CLI installed at $(which avatar)"
else
    # pipx bin might not be on PATH yet
    export PATH="$HOME/.local/bin:$PATH"
    if command -v avatar &>/dev/null; then
        ok "avatar CLI installed at $(which avatar)"
    else
        warn "avatar command not found on PATH. Add ~/.local/bin to your PATH."
    fi
fi

# --- Download TTS models ---------------------------------------------------
if [ "$INSTALL_TTS" = true ]; then
    step "Downloading Kokoro TTS models"

    MODEL_DIR="$HOME/.cache/ascii-avatar/models"
    mkdir -p "$MODEL_DIR"
    RELEASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

    if [ -f "$MODEL_DIR/kokoro-v1.0.onnx" ] && [ -f "$MODEL_DIR/voices-v1.0.bin" ]; then
        ok "Models already downloaded"
    else
        if ! command -v wget &>/dev/null && ! command -v curl &>/dev/null; then
            warn "Neither wget nor curl found. Skipping model download."
            warn "Run this step manually later."
        else
            DL_CMD="curl -fSL -o"
            if command -v wget &>/dev/null; then
                DL_CMD="wget -q --show-progress -O"
            fi

            ONNX_SHA256="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
            VOICES_SHA256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"

            if [ ! -f "$MODEL_DIR/kokoro-v1.0.onnx" ]; then
                info "Downloading kokoro-v1.0.onnx (~311 MB)..."
                $DL_CMD "$MODEL_DIR/kokoro-v1.0.onnx" "$RELEASE_URL/kokoro-v1.0.onnx"
            fi

            if [ ! -f "$MODEL_DIR/voices-v1.0.bin" ]; then
                info "Downloading voices-v1.0.bin (~27 MB)..."
                $DL_CMD "$MODEL_DIR/voices-v1.0.bin" "$RELEASE_URL/voices-v1.0.bin"
            fi

            # Verify checksums
            info "Verifying model checksums..."
            echo "$ONNX_SHA256  $MODEL_DIR/kokoro-v1.0.onnx" | sha256sum -c --quiet 2>/dev/null || { warn "kokoro-v1.0.onnx checksum mismatch — file may be corrupted"; rm -f "$MODEL_DIR/kokoro-v1.0.onnx"; exit 1; }
            echo "$VOICES_SHA256  $MODEL_DIR/voices-v1.0.bin" | sha256sum -c --quiet 2>/dev/null || { warn "voices-v1.0.bin checksum mismatch — file may be corrupted"; rm -f "$MODEL_DIR/voices-v1.0.bin"; exit 1; }
            ok "TTS models verified and ready at $MODEL_DIR"
        fi
    fi
fi

# --- Configure Claude Code hooks -------------------------------------------
if [ "$INSTALL_HOOKS" = true ]; then
    step "Configuring Claude Code hooks"

    CLAUDE_DIR="$HOME/.claude"
    SETTINGS="$CLAUDE_DIR/settings.json"

    # Resolve the pipx venv python for hook commands
    PIPX_PY="$(dirname "$(which avatar)")/../lib/python*/site-packages" 2>/dev/null || true
    AVATAR_BIN="$(which avatar-bridge 2>/dev/null || echo "avatar-bridge")"
    AVATAR_PIPX_PY="$(python3 -c "import shutil; p=shutil.which('avatar-bridge'); print(p)" 2>/dev/null || echo "avatar-bridge")"

    # Find the pipx venv python interpreter for reliable hook execution
    PIPX_PYTHON=""
    if [ -d "$HOME/.local/share/pipx/venvs/ascii-avatar" ]; then
        PIPX_PYTHON="$HOME/.local/share/pipx/venvs/ascii-avatar/bin/python"
    elif [ -d "$HOME/.local/pipx/venvs/ascii-avatar" ]; then
        PIPX_PYTHON="$HOME/.local/pipx/venvs/ascii-avatar/bin/python"
    fi

    if [ -n "$PIPX_PYTHON" ] && [ -f "$PIPX_PYTHON" ]; then
        HOOK_PREFIX="$PIPX_PYTHON -m avatar.bridge"
    else
        HOOK_PREFIX="avatar-bridge"
    fi

    mkdir -p "$CLAUDE_DIR"

    # Build the hooks JSON
    HOOKS_JSON=$(cat <<HOOKEOF
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_PREFIX.hook_think",
            "timeout": 3000
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_PREFIX.hook_tool",
            "timeout": 5000
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_PREFIX.hook_notify",
            "timeout": 10000
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_PREFIX.hook_stop",
            "timeout": 15000
          }
        ]
      }
    ]
  }
}
HOOKEOF
)

    if [ -f "$SETTINGS" ]; then
        # Check if hooks are already configured
        if grep -q "hook_think" "$SETTINGS" 2>/dev/null; then
            ok "Claude Code hooks already configured"
        else
            warn "~/.claude/settings.json exists but has no avatar hooks."
            echo ""
            echo -e "${DIM}Add these hooks to your settings.json:${RESET}"
            echo "$HOOKS_JSON"
            echo ""
            info "Or run: avatar-bridge setup-hooks"
        fi
    else
        echo "$HOOKS_JSON" > "$SETTINGS"
        ok "Created $SETTINGS with avatar hooks"
    fi
fi

# --- tmux launcher ----------------------------------------------------------
if [ "$INSTALL_TMUX" = true ]; then
    step "Setting up tmux launcher"

    LAUNCHER_DIR="$HOME/.claude/agents"
    mkdir -p "$LAUNCHER_DIR"

    PIPX_PYTHON=""
    if [ -d "$HOME/.local/share/pipx/venvs/ascii-avatar" ]; then
        PIPX_PYTHON="$HOME/.local/share/pipx/venvs/ascii-avatar/bin/python"
    elif [ -d "$HOME/.local/pipx/venvs/ascii-avatar" ]; then
        PIPX_PYTHON="$HOME/.local/pipx/venvs/ascii-avatar/bin/python"
    fi

    AVATAR_CMD="$(which avatar 2>/dev/null || echo "$HOME/.local/bin/avatar")"
    CLAUDE_CMD="$(which claude 2>/dev/null || echo "claude")"
    cat > "$LAUNCHER_DIR/avatar-start.sh" << LAUNCHEOF
#!/bin/bash
# ASCII Avatar + Claude Code tmux launcher
# Usage: bash ~/.claude/agents/avatar-start.sh [--yolo]
set -euo pipefail

# Socket path matches paths.py: XDG_RUNTIME_DIR > ~/.local/share fallback
if [ -n "\${XDG_RUNTIME_DIR:-}" ]; then
    AVATAR_RUNTIME="\$XDG_RUNTIME_DIR/ascii-avatar"
else
    AVATAR_RUNTIME="\$HOME/.local/share/ascii-avatar"
fi
mkdir -p "\$AVATAR_RUNTIME" && chmod 700 "\$AVATAR_RUNTIME"
SOCKET="\${AVATAR_SOCKET:-\$AVATAR_RUNTIME/ascii-avatar.sock}"
CLAUDE="$CLAUDE_CMD"
AVATAR="$AVATAR_CMD"
PIPX_PY="$PIPX_PYTHON"

CLAUDE_FLAGS="--agent avatar"
for arg in "\$@"; do
    case "\$arg" in
        --yolo) CLAUDE_FLAGS="--agent avatar --dangerously-skip-permissions" ;;
    esac
done

tmux kill-session -t avatar 2>/dev/null || true
[ -e "\$SOCKET" ] && rm -f "\$SOCKET"

WORKSPACE=\$(mktemp -d /tmp/avatar-workspace.XXXXXX)
mkdir -p "\$WORKSPACE/.claude"
cat > "\$WORKSPACE/.claude/settings.local.json" << 'INNEREOF'
{
  "hooks": {
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [{ "type": "command", "command": "$PIPX_PYTHON -m avatar.bridge.hook_think", "timeout": 3000 }] }
    ],
    "PostToolUse": [
      { "matcher": "", "hooks": [{ "type": "command", "command": "$PIPX_PYTHON -m avatar.bridge.hook_tool", "timeout": 5000 }] }
    ],
    "Notification": [
      { "matcher": "", "hooks": [{ "type": "command", "command": "$PIPX_PYTHON -m avatar.bridge.hook_notify", "timeout": 10000 }] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [{ "type": "command", "command": "$PIPX_PYTHON -m avatar.bridge.hook_stop", "timeout": 15000 }] }
    ]
  }
}
INNEREOF

tmux set -g allow-passthrough on 2>/dev/null || true

tmux new-session -d -s avatar \\
    "cd \$WORKSPACE && \$CLAUDE \$CLAUDE_FLAGS" \; \\
    split-window -h -l 65 \\
    "sleep 1 && \$AVATAR --socket \$SOCKET --charset auto" \; \\
    select-pane -t 0

tmux attach -t avatar
LAUNCHEOF

    chmod +x "$LAUNCHER_DIR/avatar-start.sh"
    ok "Launcher: $LAUNCHER_DIR/avatar-start.sh"

    # Add shell alias
    ALIAS_LINE='alias avatar-session="bash $HOME/.claude/agents/avatar-start.sh"'
    ALIAS_YOLO='alias avatar-yolo="bash $HOME/.claude/agents/avatar-start.sh --yolo"'

    SHELL_RC="$HOME/.bashrc"
    if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "$SHELL")" = "zsh" ]; then
        SHELL_RC="$HOME/.zshrc"
    fi

    if grep -q "avatar-session" "$SHELL_RC" 2>/dev/null; then
        ok "Shell aliases already in $SHELL_RC"
    else
        {
            echo ""
            echo "# ASCII Avatar for Claude Code"
            echo "$ALIAS_LINE"
            echo "$ALIAS_YOLO"
        } >> "$SHELL_RC"
        ok "Added aliases to $SHELL_RC"
        info "  avatar-session  — start with normal permissions"
        info "  avatar-yolo     — start with --dangerously-skip-permissions"
    fi
fi

# --- Done -------------------------------------------------------------------
step "Installation complete"
echo ""
echo -e "${BOLD}Quick start:${RESET}"
echo ""
echo -e "  ${CYAN}avatar --no-voice${RESET}          # test standalone (no TTS)"
echo -e "  ${CYAN}avatar${RESET}                     # run with TTS"
echo -e "  ${CYAN}avatar-session${RESET}             # tmux: Claude Code + avatar side-by-side"
echo -e "  ${CYAN}avatar-yolo${RESET}                # tmux: skip permissions mode"
echo ""
echo -e "${DIM}Uninstall: bash <(curl -sSL https://raw.githubusercontent.com/Angelopvtac/ascii-avatar/master/install.sh) --uninstall${RESET}"
echo ""
