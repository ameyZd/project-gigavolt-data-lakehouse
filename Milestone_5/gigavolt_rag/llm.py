"""LLM clients over plain HTTPS (``requests``): Anthropic, OpenAI, Ollama, plus an extractive fallback.

Using the REST APIs directly keeps dependencies small and makes error handling explicit:
* one retry on timeouts / HTTP 429 / 5xx with a short back-off;
* if the API rejects an optional field (e.g. effort/thinking/temperature) the request is
  retried once without the optional fields;
* any remaining failure is returned as ``LLMResponse.error`` so the pipeline can fall back
  to a fully cited extractive answer instead of crashing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@dataclass
class LLMResponse:
    text: str = ""
    provider: str = ""
    model: str = ""
    elapsed_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    attempts: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


class LLMClient:
    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self.provider = (provider or settings.llm_provider).lower()
        self._session = None

    @property
    def model(self) -> str:
        return {
            "anthropic": self.settings.anthropic_model,
            "openai": self.settings.openai_model,
            "ollama": self.settings.ollama_model,
        }.get(self.provider, "extractive")

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()  # keep-alive: reuses the TLS connection between questions
        return self._session

    def available(self) -> tuple[bool, str]:
        if self.provider == "anthropic" and not self.settings.anthropic_api_key:
            return False, "ANTHROPIC_API_KEY is not set"
        if self.provider == "openai" and not self.settings.openai_api_key:
            return False, "OPENAI_API_KEY is not set"
        if self.provider not in {"anthropic", "openai", "ollama", "extractive"}:
            return False, f"unknown LLM_PROVIDER '{self.provider}'"
        return True, "configured"

    # ------------------------------------------------------------------ public
    def generate(self, system: str, user: str) -> LLMResponse:
        response = LLMResponse(provider=self.provider, model=self.model)
        ok, reason = self.available()
        if self.provider == "extractive":
            response.error = "extractive mode selected (no LLM call)"
            return response
        if not ok:
            response.error = reason
            return response
        started = time.perf_counter()
        try:
            if self.provider == "anthropic":
                self._anthropic(system, user, response)
            elif self.provider == "openai":
                self._openai(system, user, response)
            else:
                self._ollama(system, user, response)
        except Exception as error:  # network errors, JSON errors, ...
            response.error = f"{error.__class__.__name__}: {error}"
        response.elapsed_ms = (time.perf_counter() - started) * 1000
        if not response.error and not response.text.strip():
            response.error = "LLM returned an empty answer"
        return response

    # ------------------------------------------------------------------ transport
    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any], optional: tuple[str, ...],
              response: LLMResponse) -> dict[str, Any]:
        """POST with one retry for transient errors and one retry without optional fields on HTTP 400."""
        import requests

        payload = dict(body)
        stripped_optional = False
        last_error = ""
        for attempt in range(1, 4):
            response.attempts = attempt
            try:
                http = self.session.post(url, headers=headers, json=payload, timeout=self.settings.llm_timeout_s)
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = f"{error.__class__.__name__}: {error}"
                time.sleep(0.4)
                continue
            if http.status_code == 200:
                return http.json()
            detail = http.text[:300]
            if http.status_code == 400 and not stripped_optional and any(k in payload for k in optional):
                for key in optional:
                    payload.pop(key, None)
                stripped_optional = True
                response.notes.append(f"API rejected optional fields {optional}; retried without them ({detail[:120]})")
                continue
            if http.status_code in (429, 500, 502, 503, 504, 529) and attempt < 3:
                last_error = f"HTTP {http.status_code}: {detail}"
                time.sleep(0.6 * attempt)
                continue
            raise RuntimeError(f"HTTP {http.status_code}: {detail}")
        raise RuntimeError(last_error or "request failed")

    def _anthropic(self, system: str, user: str, response: LLMResponse) -> None:
        headers = {
            "x-api-key": self.settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.settings.anthropic_model,
            "max_tokens": self.settings.llm_max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            # Latency: low effort + no extended thinking -> short, fast answers.
            # No "temperature": current Claude models (Haiku 5.5 included) reject non-default
            # sampling parameters with HTTP 400; determinism comes from the strict prompt instead.
            # Haiku 5.5 accepts thinking "disabled" only at effort high or below (low is fine).
            "output_config": {"effort": self.settings.anthropic_effort},
            "thinking": {"type": "disabled"},
        }
        data = self._post(ANTHROPIC_URL, headers, body, ("thinking", "output_config"), response)
        response.text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage", {})
        response.input_tokens, response.output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        response.model = data.get("model", response.model)

    def _openai(self, system: str, user: str, response: LLMResponse) -> None:
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": self.settings.openai_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": self.settings.llm_max_tokens,
            "temperature": 0,
        }
        data = self._post(OPENAI_URL, headers, body, ("temperature",), response)
        response.text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        response.input_tokens, response.output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        response.model = data.get("model", response.model)

    def _ollama(self, system: str, user: str, response: LLMResponse) -> None:
        body = {
            "model": self.settings.ollama_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0, "num_predict": self.settings.llm_max_tokens},
        }
        data = self._post(self.settings.ollama_url.rstrip("/") + "/api/chat", {}, body, (), response)
        response.text = data.get("message", {}).get("content", "")
        response.input_tokens, response.output_tokens = data.get("prompt_eval_count"), data.get("eval_count")


def extractive_answer(question: str, items) -> str:
    """Deterministic, 100%-cited fallback used when no LLM is configured or the LLM call fails.

    Takes the top records round-robin across the three sources so every database is represented.
    """
    if not items:
        return ("Answer: The retrieved Gigavolt data does not contain information for this question.\n"
                "Key findings:\n- No records matched in MariaDB, MongoDB or ChromaDB.\nGaps: Everything.")
    absences = [i for i in items if i.kind == "absence"]
    evidence = [i for i in items if i.kind != "absence"]
    queues = {p: [i for i in evidence if i.citation_id.startswith(p)] for p in ("S", "D", "V")}
    picked = []
    while len(picked) < 6 and any(queues.values()):
        for prefix in ("D", "S", "V"):
            if queues[prefix] and len(picked) < 6:
                picked.append(queues[prefix].pop(0))

    def excerpt(item) -> str:
        text = " ".join(item.text.split())
        text = text if len(text) <= 260 else text[:257].rsplit(" ", 1)[0] + " ..."
        return text.replace(". ", "; ").rstrip(".")

    lines = ["Answer: Extractive mode (no LLM was called): the highest-ranked evidence from each database is quoted "
             f"below with its source tag [{(picked or items)[0].citation_id}]."]
    lines.append("Key findings:")
    lines += [f"- {excerpt(item)} [{item.citation_id}]." for item in picked]
    if absences:
        lines.append("Gaps: " + " ".join(f"{excerpt(a)} [{a.citation_id}]." for a in absences[:2]))
    else:
        lines.append("Gaps: None.")
    return "\n".join(lines)
