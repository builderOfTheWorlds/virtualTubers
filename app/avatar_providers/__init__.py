#!/usr/bin/env python3
"""
avatar_providers/__init__.py
Registry + fallback loader for pluggable avatar rendering backends.

Provider selection: env AVATAR_PROVIDER > config `avatar.provider` >
"builtin". An unknown provider name, or ANY exception raised while
constructing the selected provider (bad config, missing vendored repo,
terminal init failure, ...), is logged to stderr and we fall back to
BuiltinProvider — the avatar pane runs inside a tmux pane whose only job
is to stay up, so it must never crash the container over a provider
problem.
"""
import os
import sys


def _load_builtin():
    from avatar_providers.builtin import BuiltinProvider
    return BuiltinProvider


def _load_ascii_avatar():
    from avatar_providers.ascii_avatar import AsciiAvatarProvider
    return AsciiAvatarProvider


# name -> lazy factory returning the provider class. Lazy so a worker only
# pays the import cost (and only needs the deps) of the provider it
# actually selected.
PROVIDERS = {
    "builtin": _load_builtin,
    "ascii_avatar": _load_ascii_avatar,
}


def load_provider(avatar_config, name, title):
    """Resolve + construct the configured provider, falling back to
    BuiltinProvider on any unknown name or construction failure."""
    avatar_config = avatar_config or {}
    provider_name = os.environ.get("AVATAR_PROVIDER") or avatar_config.get("provider") or "builtin"

    factory = PROVIDERS.get(provider_name)
    if factory is None:
        print(
            f"[avatar] unknown avatar provider {provider_name!r} "
            f"(expected one of {sorted(PROVIDERS)}) — falling back to builtin",
            file=sys.stderr,
        )
        from avatar_providers.builtin import BuiltinProvider
        return BuiltinProvider(avatar_config, name, title)

    try:
        provider_cls = factory()
        provider = provider_cls(avatar_config, name, title)
    except Exception as exc:
        print(
            f"[avatar] provider {provider_name!r} failed to initialize "
            f"({exc!r}) — falling back to builtin",
            file=sys.stderr,
        )
        from avatar_providers.builtin import BuiltinProvider
        return BuiltinProvider(avatar_config, name, title)

    print(f"[avatar] using provider={provider_name!r}", file=sys.stderr)
    return provider
