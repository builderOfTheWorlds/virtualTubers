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

  CorpusAdapter           — a sessionCorpus JSONL export (OB-12): one recorded
                            coding-agent session per line -> one
                            `kind='work_session'` note holding a condensed
                            digest. `load_source` picks it for `.jsonl` files.

More adapters can be added later (wiki export, transcript set) without
touching the pipeline — this module only grows, it does not change.
"""
import hashlib
import json
import logging
import pathlib
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

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


class CorpusAdapter:
    """A sessionCorpus JSONL export (OB-12): one recorded coding-agent session
    per line, shaped `{source_tool, host, project, session_id, started_at,
    ended_at, model, events: [{seq, ts, type, text, tool, input, output,
    error}]}` (sessionCorpus src/normaliser.py `export_view`).

    Each session with at least `min_events` events becomes ONE
    `SourceNote(kind='work_session')` whose text is a condensed digest — the
    user's asks, files touched, commands run, test outcomes and the final
    assistant summary — capped at `max_chars`. The export is already
    redacted and audited upstream; this adapter never logs content.

    `tag`, when given, keeps only records whose `tags` list (written by
    sessionCorpus export_corpus) contains it."""

    DEFAULT_MAX_CHARS = 3000
    KIND = "work_session"
    _ASK_CHARS, _MAX_ASKS = 240, 6
    _CMD_CHARS, _MAX_CMDS = 140, 8
    _MAX_FILES = 20
    _RESULT_CHARS = 700

    _EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit", "patch", "write_file"}
    _READ_TOOLS = {"Read", "read_file"}
    _SHELL_TOOLS = {"Bash", "PowerShell", "terminal"}
    _PATH_KEYS = ("file_path", "path", "notebook_path", "file")
    _TEST_CMD = re.compile(r"\bpytest\b|\bunittest\b|\b(?:npm|yarn|pnpm)\s+(?:run\s+)?test\b"
                           r"|\bgo\s+test\b|\bcargo\s+test\b|\btox\b")
    _COUNT = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped)\b")

    def __init__(self, path: str | pathlib.Path, *, min_events: int = 5,
                 tag: str | None = None, max_chars: int = DEFAULT_MAX_CHARS):
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"corpus export {p} does not exist")
        self.path = p
        self.min_events = max(0, int(min_events))
        self.tag = tag
        self.max_chars = max(500, int(max_chars))

    @staticmethod
    def _oneline(text, limit):
        flat = " ".join(str(text or "").split())
        return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"

    def _records(self):
        with self.path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    # Line number only — never the content.
                    log.warning("corpus_adapter.bad_line path=%s line=%d", self.path.name, lineno)
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("events"), list):
                    yield rec

    def _command(self, event):
        inp = event.get("input")
        if isinstance(inp, dict):
            return str(inp.get("command") or "")
        return str(inp or "") if event.get("tool") in self._SHELL_TOOLS else ""

    def _path_of(self, event):
        inp = event.get("input")
        if isinstance(inp, dict):
            for key in self._PATH_KEYS:
                if isinstance(inp.get(key), str) and inp[key].strip():
                    return inp[key].strip()
        return None

    def digest(self, rec: dict) -> str:
        """Condensed, length-capped text for one session record."""
        asks, cmds, edited, read = [], [], [], []
        test_runs, last_counts, final = 0, None, ""
        for ev in rec.get("events") or []:
            kind = ev.get("type")
            if kind == "user_message" and str(ev.get("text") or "").strip():
                asks.append(self._oneline(ev["text"], self._ASK_CHARS))
            elif kind == "assistant_text" and str(ev.get("text") or "").strip():
                final = ev["text"]
            elif kind == "tool_call":
                tool = ev.get("tool") or ""
                path = self._path_of(ev)
                if path and tool in self._EDIT_TOOLS and path not in edited:
                    edited.append(path)
                elif path and tool in self._READ_TOOLS and path not in read:
                    read.append(path)
                cmd = self._command(ev) if tool in self._SHELL_TOOLS else ""
                if cmd:
                    cmds.append(self._oneline(cmd, self._CMD_CHARS))
                    if self._TEST_CMD.search(cmd):
                        test_runs += 1
                        out = ev.get("output")
                        out = out if isinstance(out, str) else json.dumps(out or "")
                        counts = {}
                        for n, word in self._COUNT.findall(out):
                            counts[word.rstrip("s") if word.startswith("error") else word] = int(n)
                        if counts:
                            last_counts = counts
        lines = [f"Work session ({rec.get('source_tool') or 'unknown'}) on "
                 f"{rec.get('project') or 'unknown project'}, started {rec.get('started_at') or '?'}."]
        if asks:
            lines.append("Asks:")
            lines += [f"- {a}" for a in asks[:self._MAX_ASKS]]
            if len(asks) > self._MAX_ASKS:
                lines.append(f"- (+{len(asks) - self._MAX_ASKS} more)")
        if edited:
            lines.append("Files edited: " + ", ".join(edited[:self._MAX_FILES]))
        if read:
            lines.append("Files read: " + ", ".join(read[:self._MAX_FILES]))
        if cmds:
            lines.append("Commands:")
            lines += [f"- {c}" for c in cmds[:self._MAX_CMDS]]
            if len(cmds) > self._MAX_CMDS:
                lines.append(f"- (+{len(cmds) - self._MAX_CMDS} more)")
        if test_runs:
            outcome = ", ".join(f"{v} {k}" for k, v in sorted(last_counts.items())) \
                if last_counts else "counts not detected"
            lines.append(f"Tests: {test_runs} run(s); last run: {outcome}.")
        result = f"Result: {self._oneline(final, self._RESULT_CHARS)}" if final else ""
        # The result goes last but must survive the cap — trim the body instead.
        budget = self.max_chars - (len(result) + 1 if result else 0)
        body = "\n".join(lines)
        if len(body) > budget:
            body = body[:max(0, budget - 1)].rstrip() + "…"
        return body + ("\n" + result if result else "")

    def notes(self) -> list[SourceNote]:
        out, skipped = [], 0
        for rec in self._records():
            if self.tag is not None and self.tag not in (rec.get("tags") or []):
                skipped += 1
                continue
            if len(rec["events"]) < self.min_events:
                skipped += 1
                continue
            text = self.digest(rec)
            source_tool = str(rec.get("source_tool") or "session")
            session_id = str(rec.get("session_id") or f"line{len(out)}")
            first_ask = next((e.get("text") for e in rec["events"]
                              if e.get("type") == "user_message" and str(e.get("text") or "").strip()), "")
            out.append(SourceNote(
                id=_slugify(f"{source_tool}-{session_id}", maxlen=96),
                title=self._oneline(first_ask, 80) or str(rec.get("project") or session_id),
                text=text,
                kind=self.KIND,
                rel_path=f"{self.path.name}#{session_id}",
                hash=_hash(f"{source_tool}:{session_id}:{text}"),
            ))
        log.debug("corpus_adapter.notes path=%s notes=%d skipped=%d", self.path.name, len(out), skipped)
        return out


def load_source(path: str | pathlib.Path, *, kind=None, target_chars=None) -> list[SourceNote]:
    """Dispatch on the type of `path`. Directory -> ObsidianVaultAdapter;
    `.jsonl` file -> CorpusAdapter (a sessionCorpus export); any other
    single file -> SingleTextAdapter. A flat single-file .txt is the
    'source as a parameter' case in §6F.4 — different corpus, different
    pack, same pipeline."""
    p = pathlib.Path(path)
    if p.is_dir():
        return ObsidianVaultAdapter(p).notes()
    if p.is_file() and p.suffix.lower() == ".jsonl":
        return CorpusAdapter(p).notes()
    if p.is_file():
        return SingleTextAdapter(p, target_chars=target_chars or SingleTextAdapter.DEFAULT_TARGET_CHARS).notes()
    raise FileNotFoundError(f"source {path} is not a directory or file")
