"""Character tooling built on app/character_schema.py (OB-20).

The package also holds the character v4 memory loop modules (config, clock,
shapes, db, store/, jobs), imported explicitly as `character.<module>` (D-23).
They are deliberately NOT imported here, so `import character.avatar` stays
cheap and never pulls in psycopg2.
"""
from character.avatar import (
    AvatarResponseError,
    AvatarResult,
    ReplayLLMClient,
    map_appearance,
    map_appearance_result,
    parse_response,
)

__all__ = [
    "AvatarResponseError",
    "AvatarResult",
    "ReplayLLMClient",
    "map_appearance",
    "map_appearance_result",
    "parse_response",
]
