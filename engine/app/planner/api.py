"""Optional LLM-backed strategy planner.

The app may use a single LLM role today (strategy selection / orchestration),
while the execution layer can be pointed at a different DeepSeek model by
setting environment variables. This keeps the deterministic fallback intact and
allows a Pro model to handle orchestration and a Flash model to execute the
faster, lower-latency work.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

from .base import Strategy, validate_strategy

logger = logging.getLogger("villaai.planner")

_RUN_LLM_USAGE: dict[str, dict[str, int]] = defaultdict(
    lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
    }
)


def _normalize_usage(payload: Any) -> dict[str, int]:
    """Extract usage counts from a provider payload."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }

    prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    completion_tokens = int(
        usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    )
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(total_tokens),
        "calls": 1,
    }


def record_run_llm_usage(run_id: str | None, payload: Any) -> None:
    """Accumulate LLM token usage for a single run/task."""
    if not run_id:
        return
    usage = _normalize_usage(payload)
    stats = _RUN_LLM_USAGE[run_id]
    stats["prompt_tokens"] += usage["prompt_tokens"]
    stats["completion_tokens"] += usage["completion_tokens"]
    stats["total_tokens"] += usage["total_tokens"]
    stats["calls"] += usage["calls"]


def get_run_llm_usage(run_id: str | None) -> dict[str, int]:
    """Return the accumulated token usage for a run, if any."""
    if not run_id:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    return dict(
        _RUN_LLM_USAGE.get(
            run_id,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
            },
        )
    )


def clear_run_llm_usage(run_id: str | None) -> None:
    """Clear accumulated usage once a run has reported its final totals."""
    if run_id:
        _RUN_LLM_USAGE.pop(run_id, None)


def _log_usage(
    provider: str,
    model_name: str,
    payload: Any,
    *,
    run_id: str | None = None,
) -> None:
    """Log the selected model and token usage for an LLM response."""
    usage = None
    if isinstance(payload, dict):
        usage = payload.get("usage")

    if not isinstance(usage, dict):
        logger.info(
            "LLM call provider=%s model=%s usage=unavailable",
            provider,
            model_name,
        )
        return

    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion_tokens = usage.get(
        "completion_tokens", usage.get("output_tokens", 0)
    )
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    if run_id:
        record_run_llm_usage(run_id, payload)

    logger.info(
        "LLM call provider=%s model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
        provider,
        model_name,
        prompt_tokens,
        completion_tokens,
        total_tokens,
    )


def _deepseek_messages(prompt: str, image_url: str | None = None) -> list[dict[str, Any]]:
    """Build a DeepSeek message payload. Use multimodal content when an image is supplied."""
    if image_url:
        return [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }]
    return [{"role": "user", "content": prompt}]


_DEEPSEEK_DEFAULTS = {
    "orchestrator": os.environ.get("DEEPSEEK_ORCHESTRATOR_MODEL")
    or os.environ.get("DEEPSEEK_MODEL")
    or "deepseek-v4-pro",
    "execution": os.environ.get("DEEPSEEK_EXECUTION_MODEL")
    or os.environ.get("DEEPSEEK_MODEL")
    or "deepseek-flash",
}


def _deepseek_model_for(role: str) -> str:
    """Return the configured DeepSeek model for the named role."""
    key = f"DEEPSEEK_{role.upper()}_MODEL"
    return os.environ.get(key) or _DEEPSEEK_DEFAULTS.get(role, "deepseek-chat")


_PROMPT = """You are an MEP preliminary-design strategist for Villa AI.
Choose a routing STRATEGY (not coordinates) for the {system} system.
Return ONLY one JSON object, no prose and no markdown fences, with exactly:
{{
  "system": "{system}",
  "trunk": one of ["perimeter","central","wet_core","radial","branch"],
  "stack_location": one of ["wet_core","exterior_wall","utility","shaft"],
  "z_band": [lo, hi] in metres (soft scoring hint, lo <= hi),
  "max_bends": integer 0..64,
  "prefer_short_runs": number in [0,1],
  "params": object of optional routing hints
}}
Real observations (read-only): {observations}
Country: {country}. Preliminary design only; never claim code compliance.
"""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from text and validate it."""
    if not text or not text.strip():
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


class ApiPlanner:
    """Structurally implements the frozen ``Planner`` Protocol (duck typing)."""

    def _call_deepseek(
        self,
        prompt: str,
        *,
        role: str = "orchestrator",
        image_url: str | None = None,
        run_id: str | None = None,
    ) -> str:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("httpx not installed") from exc

        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")

        model_name = _deepseek_model_for(role)
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        payload = {
            "model": model_name,
            "messages": _deepseek_messages(prompt, image_url=image_url),
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        r = httpx.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()

        response_json = r.json()
        _log_usage("deepseek", model_name, response_json, run_id=run_id)
        return self._extract_response_text(response_json)

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        """Read the text content from a DeepSeek response payload."""
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("DeepSeek returned no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
        return str(payload)

    def choose_strategy(
        self,
        system: Any,
        model: Any,
        observations: dict[str, Any] | None,
        image_url: str | None = None,
        *,
        run_id: str | None = None,
    ) -> "Strategy | None":
        sysval = getattr(system, "value", str(system))
        country = getattr(getattr(model, "country", None), "value", "ID")
        prompt = _PROMPT.format(
            system=sysval,
            country=country,
            observations=json.dumps(observations or {}, default=str)[:2000],
        )

        if os.environ.get("DEEPSEEK_API_KEY"):
            try:
                text = self._call_deepseek(
                    prompt,
                    role="orchestrator",
                    image_url=image_url,
                    run_id=run_id,
                )
                parsed = _extract_json_object(text)
                if parsed is not None:
                    return validate_strategy(parsed)
            except Exception:
                pass

        return None


__all__ = [
    "ApiPlanner",
    "record_run_llm_usage",
    "get_run_llm_usage",
    "clear_run_llm_usage",
]
