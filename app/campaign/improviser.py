"""Improvise campaign content with a local LLM."""

import logging

from campaign.pack import Beat, CampaignPack, CastMember, Scene

log = logging.getLogger(__name__)


class ImproviserError(RuntimeError):
    """Raised by __call__ only. Never raised by generate_scene."""


class LLMImproviser:
    """Improvise campaign content with a local LLM."""

    def __init__(self, pack: CampaignPack, llm=None, max_recent=8, max_words=45,
                 max_beats=12):
        self.pack = pack
        self.llm = llm
        self.max_recent = max_recent
        self.max_words = max_words
        self.max_beats = max_beats
        self.scene = None
        self.carry = {}
        self.loop = 1
        self.recent = []

    def update_context(self, scene=None, carry=None, loop=None) -> None:
        """Update the improviser's context."""
        if scene is not None:
            self.scene = scene
        if carry is not None:
            self.carry = carry
        if loop is not None:
            self.loop = loop

    def observe(self, speaker_name: str, text: str) -> None:
        """Add a line to the rolling transcript window."""
        if not text or not text.strip():
            return
        self.recent.append(f"{speaker_name}: {text}")
        if len(self.recent) > self.max_recent:
            self.recent = self.recent[-self.max_recent:]

    def __call__(self, beat: Beat, cast_member: CastMember) -> str:
        """Improvise a line for a beat."""
        if self.llm is None:
            raise ImproviserError("no LLM available")

        # Build system prompt
        system_parts = [cast_member.name]
        if cast_member.archetype:
            system_parts.append(cast_member.archetype)
        if cast_member.system_prompt:
            system_parts.append(cast_member.system_prompt)
        system_prompt = " ".join(system_parts)

        # Add standing instruction
        system_prompt += (
            "\n\nYou reply with exactly one spoken line, in character, "
            "with no name label, no quotation marks and no stage directions."
        )

        # Build user prompt
        user_parts = []
        if self.scene and self.scene.title:
            user_parts.append(f"Scene title: {self.scene.title}")

        # Add lore notes
        if self.scene and self.scene.lore:
            for stem in self.scene.lore:
                note = self.pack.lore.get(stem)
                if note is not None:
                    user_parts.append(note)

        # Add transcript
        if self.recent:
            user_parts.append("Transcript:\n" + "\n".join(self.recent))

        # Add memory section
        if self.loop > 1 or self.carry:
            user_parts.append(f"Loop {self.loop}")
            carry_items = []
            for key, value in self.carry.items():
                carry_items.append(f"{key}: {value}")
            user_parts.append("Carry:\n" + "\n".join(carry_items))

        # Add beat intent
        user_parts.append(f"Intent: {beat.text or ''}")

        # Add word budget
        user_parts.append(f"Word budget: {self.max_words}")

        user_prompt = "\n\n".join(user_parts)

        log.debug("improvising for %s in scene %s", cast_member.id,
                  self.scene.id if self.scene else "None")

        try:
            reply = self.llm.complete(system_prompt, [{"role": "user", "content": user_prompt}])
        except Exception as exc:
            raise ImproviserError("LLM call failed") from exc

        # Sanitise the reply
        text = self._sanitise(reply, [cast_member.name, cast_member.id])

        log.debug("sanitised %d chars to %d", len(reply), len(text))

        return text

    def _sanitise(self, text: str, labels: list[str] | None = None) -> str:
        """Sanitise a line of text."""
        # Step 1: If the reply is not a str, raise ImproviserError
        if not isinstance(text, str):
            raise ImproviserError("LLM returned non-string")

        # Step 2: Strip leading and trailing whitespace
        text = text.strip()

        # Step 3: Strip a leading speaker label
        if labels:
            label, sep, rest = text.partition(":")
            if sep and label.strip().lower() in {n.lower() for n in labels if n}:
                text = rest.lstrip()

        # Step 4: Delete stage directions
        # First unwrap markdown emphasis
        text = text.replace("**", " ").replace("__", " ")
        # Then remove stage directions
        result = []
        in_direction = False
        for i, char in enumerate(text):
            if char == "*" and not in_direction:
                # Check if this is a stage direction (i.e., starts with * and ends with *)
                # Look ahead to see if there's a matching *
                j = i + 1
                while j < len(text) and text[j] != "*":
                    j += 1
                if j < len(text) and text[j] == "*":
                    # This is a stage direction, skip it
                    in_direction = True
                    continue
            elif char == "*" and in_direction:
                in_direction = False
                continue
            elif not in_direction:
                result.append(char)
        text = "".join(result)

        # Step 5: Collapse all whitespace
        words = text.split()
        text = " ".join(words)

        # Step 6: Strip ONE wrapping pair of quotes
        if text.startswith(("'", '"', '“', '”')) and text.endswith(("'", '"', '“', '”')):
            if (text.startswith("'") and text.endswith("'")) or \
               (text.startswith('"') and text.endswith('"')) or \
               (text.startswith('“') and text.endswith('”')):
                text = text[1:-1]

        # Step 7: Cap the length
        words = text.split()
        if len(words) > self.max_words:
            words = words[:self.max_words]
            # Ensure terminal punctuation
            last = words[-1]
            if not last.endswith((".", "!", "?", "…")):
                last = last.rstrip(",;:-")
                words[-1] = last + "."
            text = " ".join(words)

        # Step 8: If the result is now empty or whitespace-only, raise ImproviserError
        if not text.strip():
            raise ImproviserError("sanitised text is empty")

        return text

    def generate_scene(self, scene: Scene) -> list[Beat]:
        """Generate a whole ambient scene."""
        if self.llm is None:
            log.warning("generate_scene: no LLM available")
            return []

        if not scene.prompt:
            log.warning("generate_scene: scene has no prompt")
            return []

        # Build user prompt
        user_parts = [scene.prompt]

        # Add cast roster
        cast_roster = []
        for member_id, member in self.pack.cast.items():
            cast_roster.append(f"{member_id}: {member.name}")
        user_parts.append("Cast:\n" + "\n".join(cast_roster))

        # Add lore notes
        if scene.lore:
            for stem in scene.lore:
                note = self.pack.lore.get(stem)
                if note is not None:
                    user_parts.append(note)

        # Add transcript
        if self.recent:
            user_parts.append("Transcript:\n" + "\n".join(self.recent))

        # Add memory section
        if self.loop > 1 or self.carry:
            user_parts.append(f"Loop {self.loop}")
            carry_items = []
            for key, value in self.carry.items():
                carry_items.append(f"{key}: {value}")
            user_parts.append("Carry:\n" + "\n".join(carry_items))

        # Add instruction
        user_parts.append(
            "Reply with one line per beat, formatted as '<speaker_id>: <what they say>'"
        )

        user_prompt = "\n\n".join(user_parts)

        try:
            reply = self.llm.complete("You generate ambient scenes.",
                                      [{"role": "user", "content": user_prompt}])
        except Exception:
            log.warning("generate_scene: LLM call failed")
            return []

        if not isinstance(reply, str) or not reply.strip():
            log.warning("generate_scene: unusable reply")
            return []

        beats = []
        for line in reply.splitlines():
            if len(beats) >= self.max_beats:
                break
            line = line.strip()
            if not line:
                continue

            prefix, _, rest = line.partition(":")
            if _ and prefix.strip() in self.pack.cast:
                kind, speaker, raw = "dialogue", prefix.strip(), rest
            else:
                kind, speaker, raw = "narration", self.pack.gm_id, line

            try:
                text = self._sanitise(raw)          # no label argument here
            except ImproviserError:
                continue                            # contentless line, skip it
            if not text:
                continue

            beats.append(Beat(kind=kind, speaker=speaker, text=text,
                              texts=[text], improv=False,
                              key=f"{scene.id}#gen{len(beats)}"))

        log.info("generated %d beats", len(beats))
        return beats
