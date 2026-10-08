"""
llm_client.py
Provider-switchable LLM client for the agent loop. Reads `llm.provider` from
a worker's config and returns a client with a single `complete(system_prompt,
messages)` method — callers don't need to know whether they're talking to a
local Ollama instance or the Claude API.
"""
import json
import os

import anthropic
import httpx


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url, model, temperature, max_tokens):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, system_prompt, messages):
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                },
            },
            timeout=120,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # httpx's own message drops the response body, e.g. Ollama's
            # "model 'x' not found, try pulling it first" — surface it so
            # the caller's error (and the avatar's "frustrated" bubble) is
            # actually diagnosable instead of a bare "500 Internal Server Error".
            raise LLMError(
                f"Ollama request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        return response.json()["message"]["content"]


class ClaudeClient:
    def __init__(self, model, max_tokens):
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def complete(self, system_prompt, messages):
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=messages,
        )
        return "".join(block.text for block in response.content if block.type == "text")


class _Secret:
    """Holds a credential so that repr/str/vars() of its owner never echo it."""

    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def reveal(self):
        return self._value

    def __repr__(self):
        return "<secret>"

    __str__ = __repr__


class VLLMClient:
    """OpenAI-compatible `/v1/chat/completions` client for vLLM (ported from
    utilities/3LayersWeeklyGeneration/src/concurrent_llm.py OpenAICompatClient).

    vLLM started with `--reasoning-parser` returns the chain-of-thought in a
    separate field (`reasoning_content`; newer builds call it `reasoning`).
    `complete()` keeps the plain-string contract every caller relies on and
    returns content only. `complete_stream()` streams both and returns
    `(reasoning, content)`; InstrumentedLLMClient uses it to feed the
    Thinking pane (see `supports_native_reasoning`).

    The API key is held privately and never appears in repr, errors or logs.
    """

    supports_native_reasoning = True
    _REASONING_FIELDS = ("reasoning_content", "reasoning")

    def __init__(self, base_url, model, temperature, max_tokens, api_key=None,
                 timeout_s=600, extra_body=None, http_client=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.extra_body = dict(extra_body or {})
        self._api_key = _Secret(api_key) if api_key else None
        self._http = http_client or httpx.Client(timeout=timeout_s)

    def __repr__(self):
        return (f"VLLMClient(base_url={self.base_url!r}, model={self.model!r}, "
                f"api_key={'set' if self._api_key else 'unset'})")

    __str__ = __repr__

    @property
    def has_api_key(self):
        return self._api_key is not None

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key.reveal()}"
        return headers

    def _body(self, system_prompt, messages, stream, max_tokens, reasoning_budget=None):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + list(messages or []),
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": stream,
        }
        body.update(self.extra_body)
        if reasoning_budget is not None:
            # vLLM >= 0.30 with --reasoning-config: reasoning is force-closed
            # after this many tokens and the model goes on to the answer.
            body["thinking_token_budget"] = int(reasoning_budget)
        return body

    def _reasoning_of(self, obj):
        for field in self._REASONING_FIELDS:
            value = obj.get(field)
            if value:
                return value
        return ""

    def _wrap_http_error(self, exc):
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                body = exc.response.text
            except httpx.ResponseNotRead:
                exc.response.read()
                body = exc.response.text
            return LLMError(f"vLLM request failed: {exc.response.status_code} {body}")
        if isinstance(exc, httpx.TimeoutException):
            return LLMError(f"vLLM request to model {self.model} timed out after {self.timeout_s}s")
        return LLMError(f"vLLM request to {self.base_url} failed: {exc}")

    def complete(self, system_prompt, messages, max_tokens=None, reasoning_budget=None):
        try:
            response = self._http.post(
                f"{self.base_url}/v1/chat/completions",
                json=self._body(system_prompt, messages, False, max_tokens, reasoning_budget),
                headers=self._headers(), timeout=self.timeout_s,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc
        try:
            choice = response.json()["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("vLLM response did not contain choices[0].message.content") from exc
        if not content:
            raise LLMError(f"vLLM returned empty content "
                           f"(finish_reason={choice.get('finish_reason')!r})")
        return content

    def complete_stream(self, system_prompt, messages, on_reasoning=None,
                        on_content=None, max_tokens=None, reasoning_budget=None):
        """Stream one completion. Calls `on_reasoning(chunk)` / `on_content(chunk)`
        per delta and returns `(reasoning, content)`. Raises LLMError when the
        stream ends with no content (e.g. the whole budget went to reasoning)."""
        reasoning_parts, content_parts = [], []
        finish_reason = None
        try:
            with self._http.stream(
                "POST", f"{self.base_url}/v1/chat/completions",
                json=self._body(system_prompt, messages, True, max_tokens, reasoning_budget),
                headers=self._headers(), timeout=self.timeout_s,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    choice = (event.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    piece = self._reasoning_of(delta)
                    if piece:
                        reasoning_parts.append(piece)
                        if on_reasoning:
                            on_reasoning(piece)
                    piece = delta.get("content") or ""
                    if piece:
                        content_parts.append(piece)
                        if on_content:
                            on_content(piece)
        except httpx.HTTPError as exc:
            raise self._wrap_http_error(exc) from exc

        reasoning, content = "".join(reasoning_parts), "".join(content_parts)
        if not content:
            raise LLMError(f"vLLM stream ended with empty content "
                           f"(finish_reason={finish_reason!r}, "
                           f"reasoning_chars={len(reasoning)})")
        return reasoning, content


def build_llm_client(config):
    llm_config = config.get("llm", {})
    provider = os.environ.get("LLM_PROVIDER") or llm_config.get("provider", "ollama")
    max_tokens = llm_config.get("max_tokens", 1024)

    if provider == "claude":
        model = llm_config.get("model", "")
        if not model.startswith("claude-"):
            model = "claude-opus-4-8"
        return ClaudeClient(model, max_tokens)

    if provider == "vllm":
        # The key comes from the env var NAMED in config (never the value),
        # so committed configs and compose files carry no secret.
        key_env = llm_config.get("api_key_env", "VLLM_API_KEY")
        return VLLMClient(
            base_url=os.environ.get("LLM_BASE_URL") or llm_config.get("base_url", "http://localhost:8092"),
            model=os.environ.get("LLM_MODEL") or llm_config.get("model", "default"),
            temperature=llm_config.get("temperature", 0.7),
            max_tokens=max_tokens,
            api_key=os.environ.get(key_env) or None,
            timeout_s=llm_config.get("timeout_s", 600),
            extra_body=llm_config.get("extra_body"),
        )

    if provider != "ollama":
        raise LLMError(f"unknown llm.provider: {provider!r} (expected 'ollama', 'claude' or 'vllm')")

    base_url = os.environ.get("LLM_BASE_URL") or llm_config.get("base_url", "http://localhost:11434")
    model = llm_config.get("model", "mistral")
    temperature = llm_config.get("temperature", 0.7)
    return OllamaClient(base_url, model, temperature, max_tokens)
