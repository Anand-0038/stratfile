"""Human-readable `description` field. Template by default; LLM polish is OPT-IN.

The LLM is NEVER in the decision loop (§17), and it is also NOT in the determinism
contract: `stratfile init` uses the rule-based template unless --llm-describe is
passed, because temperature=0 is not reproducible across OpenAI model updates.
The `description` field is excluded from the receipt content hash either way
(see canonical.HASH_EXCLUDED_FIELDS) — it has zero effect on signals/regimes/rules.
"""

from __future__ import annotations

import httpx

from .. import config
from .parser import StrategyAST


def _template(ast: StrategyAST) -> str:
    parts: list[str] = []
    if ast.buy_threshold:
        parts.append(
            f"DCA into {ast.primary} when Fear & Greed < {ast.buy_threshold.value} (extreme fear)"
        )
    if ast.rotate_threshold:
        parts.append(
            f"rotate to {ast.rotate_target} when > {ast.rotate_threshold.value} (extreme greed)"
        )
    parts.append("hold otherwise")
    desc = ", ".join(parts) + ". Generated deterministically by stratfile init."
    return desc[:1024]


def explain(ast: StrategyAST, seed: int = 42, use_llm: bool = False) -> str:
    template = _template(ast)
    api_key = config.openai_api_key()
    if not use_llm or not api_key:
        return template
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-4o-mini",
                "temperature": 0,
                "seed": seed,
                "max_tokens": 200,
                "messages": [
                    {
                        "role": "system",
                        "content": "Rewrite the trading strategy description in one clear plain-English "
                        "sentence. No markdown. Max 1000 characters. Do not change any numbers.",
                    },
                    {"role": "user", "content": template},
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text[:1024] if text else template
    except Exception:
        return template
