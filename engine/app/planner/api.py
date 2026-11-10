"""Optional Anthropic-backed LLM planner (MODULE_CONTRACT.md §4, plan §6).

LAZY + OPTIONAL: ``anthropic`` is imported INSIDE the method. A missing key,
a missing package, or ANY error => return ``None`` so the caller falls back to
the deterministic planner. The LLM is asked for a JSON object only and the
result is parsed with ``validate_strategy`` -- an unvalidated value can never
reach the toolkit. No network call when the key is absent (the test/CI case).
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import Strategy, validate_strategy

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


class ApiPlanner:
    """Structurally implements the frozen ``Planner`` Protocol (duck typing)."""

    def choose_strategy(
        self,
        system: Any,
        model: Any,
        observations: dict[str, Any] | None,
    ) -> "Strategy | None":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        try:
            import anthropic  # lazy, optional dependency
        except Exception:
            return None
        try:
            sysval = getattr(system, "value", str(system))
            country = getattr(getattr(model, "country", None), "value", "ID")
            prompt = _PROMPT.format(
                system=sysval,
                country=country,
                observations=json.dumps(observations or {}, default=str)[:2000],
            )
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                getattr(b, "text", "") for b in (getattr(resp, "content", []) or [])
            )
            if not text.strip():
                return None
            # Tolerate ```json ... ``` fences or surrounding prose.
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end < start:
                return None
            return validate_strategy(json.loads(text[start : end + 1]))
        except Exception:
            return None


__all__ = ["ApiPlanner"]
