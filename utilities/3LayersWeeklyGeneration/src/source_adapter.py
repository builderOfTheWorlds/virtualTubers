"""Source adapters — the `--source` parameter per §6F.5.

One contract: `load_source(path) -> list[SourceNote]`, where SourceNote is a
frozen dataclass carrying id, title, text, kind, rel_path, and a stable
sha256 of the text. Two adapters ship with it:

  ObsidianVaultAdapter    — a directory tree of `.md` files, honouring the
                            `*Agent_Ignore*` filename convention the Ashiorid
                            vault already uses. `kind` is derived from the
                            top-level folder of the file's relative path.
  SingleTextAdapter       — ONE large document (the Harry Potter test source:
                            a 5.9 MB single-line .txt). Splits the text into
                            roughly chapter-sized notes; `rel_path` is the
                            source file, and `id` includes the block index.

A third adapter could be added later (wiki export, transcript set) without
touching the pipeline — this module only grows, it does not change.
"""
import hashlib
import pathlib
import re
from dataclasses import dataclass

AGENTS_IGNORE_MARKER = "Agent_Ignore"
FOLDER_TO_KIND = {
    "plots": "plot",
    "plot": "plot",
    "world": "lore",
    "npcs": "npc",
    "characters": "character",
    "locations": "location",
    "items": "item",
    "factions": "faction",
    "spells": "item",
    "attachments": "attachment",
    "maps": "location",
    "classes": "item",
    "unsorted": "unclassified",
    "resources": "unclassified",
}


@dataclass(frozen=True)
class SourceNote:
    id: str
    title: str
    text: str
    kind: str
    rel_path: str
    hash: str


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slugify(text: str, maxlen: int = 64) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out[:maxlen] or "note"


def _kind_for_relpath(rel_path: str) -> str:
    parts = [p.lower().strip() for p in rel_path.split("/")]
    # Match the first component (top-level folder) against the kind map;
    # a second-chance pass covers vaults that nest one level down.
    for piece in parts:
        if piece in FOLDER_TO_KIND:
            return FOLDER_TO_KIND[piece]
    return "unclassified"


class ObsidianVaultAdapter:
    def __init__(self, root: str | pathlib.Path, subpath: str = ""):
        self.root = pathlib.Path(root)
        if subpath:
            self.root = self.root / subpath
        if not self.root.is_dir():
            raise FileNotFoundError(f"source dir {self.root} is not a directory")

    def notes(self) -> list[SourceNote]:
        out = []
        for md in sorted(self.root.rglob("*.md")):
            if AGENTS_IGNORE_MARKER in md.name:
                continue
            rel = md.relative_to(self.root)
            rel_posix = rel.as_posix()
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not text.strip():
                continue
            title = md.stem.replace(AGENTS_IGNORE_MARKER, "").strip() or rel_posix
            out.append(SourceNote(
                id=_slugify(rel_posix),
                title=title,
                text=text,
                kind=_kind_for_relpath(rel_posix),
                rel_path=rel_posix,
                hash=_hash(text),
            ))
        return out


class SingleTextAdapter:
    """One big unstructured document. The HP source is a single line of
    ~1.09M words; splitting on the sentence-like boundary ' . ' would break
    on legitimate punctuation in dialogue, so we chunk at a target character
    count instead and let the author module pick what it needs. `kind` is
    'lore' unless overridden — a flat document has no folder to hint at."""

    DEFAULT_TARGET_CHARS = 4000
    def __init__(self, path: str | pathlib.Path, *, target_chars: int = 4000,
                 kind: str = "lore"):
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"source file {p} does not exist")
        self.path = p
        self.target_chars = max(500, int(target_chars))
        self.kind = kind

    def notes(self) -> list[SourceNote]:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        words = text.split()
        if not words:
            return []
        # Word-boundary chunker: pack words into a chunk up to target_chars,
        # never splitting inside a word. A chunk is a unit the author LLM is
        # asked to turn into a scene, so it must start and end on whole
        # words — a scene that opens mid-word is a defect, not a quirk.
        chunks: list[str] = []
        buf = ""
        for w in words:
            piece = w if not buf else buf + " " + w
            if len(buf) and len(piece) > self.target_chars:
                chunks.append(buf)
                buf = w
            else:
                buf = piece
        if buf:
            chunks.append(buf)
        # Dedup empty chunks
        chunks = [c for c in chunks if c.strip()]
        if not chunks:
            return []
        return [
            SourceNote(
                id=f"block-{i:04d}",
                title=_slugify(chunks[i][:80]),
                text=chunks[i],
                kind=self.kind,
                rel_path=self.path.name,
                # Include the block index: a monolithic document can contain
                # repeated boilerplate (chapter headers, repeated epigraphs),
                # and those copies are DISTINCT positions. The hash is a
                # position+content key so a --refresh can target one block
                # without invalidating its identical twin.
                hash=_hash(f"{i}:{chunks[i]}"),
            )
            for i in range(len(chunks))
        ]


def load_source(path: str | pathlib.Path, *, kind=None, target_chars=None) -> list[SourceNote]:
    """Dispatch on the type of `path`. Directory -> ObsidianVaultAdapter;
    single file -> SingleTextAdapter. A flat single-file .txt is the
    'source as a parameter' case in §6F.4 — different corpus, different
    pack, same pipeline."""
    p = pathlib.Path(path)
    if p.is_dir():
        return ObsidianVaultAdapter(p).notes()
    if p.is_file():
        return SingleTextAdapter(p, target_chars=target_chars or SingleTextAdapter.DEFAULT_TARGET_CHARS).notes()
    raise FileNotFoundError(f"source {path} is not a directory or file")
