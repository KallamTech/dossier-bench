"""LLM judge: one pinned model, temperature 0, JSON output. Cost tracked separately."""

from __future__ import annotations

import json
import re
from typing import Any


SYSTEM = (
    "You are a strict grader for a document-analysis AI system. You compare an ACTUAL output "
    "against EXPECTED facts. Judge only on content, never on style. Be literal: a fact is "
    "satisfied only if the actual text states it or an unambiguous equivalent (numbers, dates "
    "and names must match; '0.5% per week' == 'half a percent weekly'). A fact is contradicted "
    "if the actual text asserts something incompatible with it. Answer with JSON only."
)

SCHEMA = (
    '{"satisfied": [<fact strings satisfied>], "missing": [<facts not present>], '
    '"contradicted": [<facts contradicted>], "forbidden_present": [<forbidden claims that appear>], '
    '"reasoning": "<one short paragraph>"}'
)


class Judge:
    system = SYSTEM

    def __init__(self, cfg, run=None):
        from openai import OpenAI

        self.model = cfg.require("judge", "model")
        self.client = OpenAI(base_url=cfg.require("judge", "base_url"), api_key=cfg.get("judge", "api_key") or "sk-none")
        price = cfg.get("judge", "price_per_m", default={}) or {}
        self.p_in = float(price.get("input", 0))
        self.p_out = float(price.get("output", 0))
        self.run = run
        self.calls = 0
        self.total_cost = 0.0

    def _call(self, user: str, tag: str) -> tuple[dict, float]:
        msgs = [{"role": "system", "content": self.system}, {"role": "user", "content": user}]
        try:
            resp = self.client.chat.completions.create(model=self.model, temperature=0, response_format={"type": "json_object"}, messages=msgs)
        except Exception as e:  # noqa: BLE001  (local Ollama builds without json mode)
            if "response_format" not in str(e) and "format" not in str(e).lower():
                raise
            resp = self.client.chat.completions.create(model=self.model, temperature=0, messages=msgs)
        text = resp.choices[0].message.content or "{}"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            data = json.loads(m.group(0)) if m else {}
        u = resp.usage
        cost = ((u.prompt_tokens or 0) * self.p_in + (u.completion_tokens or 0) * self.p_out) / 1_000_000 if u else 0.0
        self.calls += 1
        self.total_cost += cost
        if self.run is not None:
            self.run.write_raw(f"judge/{self.calls:04d}-{tag}.json", {"prompt": user, "response": data, "usage": u.model_dump() if u else None, "cost": cost})
        return data, round(cost, 6)

    def facts(
        self,
        actual: str,
        must_facts: list[str],
        must_not: list[str] | None = None,
        context: str = "",
        tag: str = "facts",
    ) -> tuple[float, dict[str, Any], float]:
        """Score = satisfied/len(must) minus penalties: 0.5 per contradiction, 0.5 per forbidden claim (floored at 0)."""
        must_not = must_not or []
        if not must_facts and not must_not:
            return 1.0, {"note": "no facts to check"}, 0.0
        user = (
            f"CONTEXT: {context}\n\n"
            f"EXPECTED FACTS (each must be present):\n" + "\n".join(f"- {f}" for f in must_facts) + "\n\n"
            + (f"FORBIDDEN CLAIMS (must NOT be asserted):\n" + "\n".join(f"- {f}" for f in must_not) + "\n\n" if must_not else "")
            + f"ACTUAL OUTPUT:\n<<<\n{(actual or '').strip()[:20000]}\n>>>\n\n"
            f"Return JSON exactly of the form {SCHEMA}. Copy fact strings verbatim into the lists."
        )
        data, cost = self._call(user, tag)
        sat = [f for f in data.get("satisfied", []) if f in must_facts]
        contra = [f for f in data.get("contradicted", []) if f in must_facts]
        forb = [f for f in data.get("forbidden_present", []) if f in must_not]
        base = len(sat) / len(must_facts) if must_facts else 1.0
        score = max(0.0, base - 0.5 * len(contra) - 0.5 * len(forb))
        return round(score, 3), {
            "satisfied": sat,
            "missing": [f for f in must_facts if f not in sat],
            "contradicted": contra,
            "forbidden_present": forb,
            "reasoning": data.get("reasoning", ""),
        }, cost

    def same_document(self, expected_title: str, actual_title: str, tag: str = "title", others: list[str] | None = None, label: str = "title") -> tuple[float, dict, float]:
        """Would someone working this case recognise ACTUAL as the document EXPECTED describes?
        Scored against the other documents in the case: 1.0 = unmistakable, 0.5 = could be confused
        with one of OTHERS, 0.0 = wrong kind of document or unusable. Wording and omitted details do not matter."""
        others_txt = "\n".join(f"- {o}" for o in (others or []) if o != expected_title)
        user = (
            f"A case file contains these other documents:\n{others_txt or '- (none)'}\n\n"
            f"EXPECTED (what the document is): {expected_title}\n"
            f"ACTUAL ({label} produced by the system): {actual_title}\n\n"
            "Would someone working the case, reading ACTUAL, know it refers to the EXPECTED document and not to one "
            "of the others? Omitted party names, dates or version labels do NOT lower the score unless they make it "
            "confusable with another listed document. Different wording is fine.\n"
            'Return JSON {"score": 1.0 | 0.5 | 0.0, "reasoning": "..."}: 1.0 = unmistakably that document, '
            "0.5 = could be confused with another listed document, 0.0 = describes a different kind of document or is unusable."
        )
        data, cost = self._call(user, tag)
        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(1.0, score)), {"reasoning": data.get("reasoning", "")}, cost

    def rubric(self, actual: str, reference: str, rubric: str, tag: str = "rubric") -> tuple[float, dict, float]:
        user = (
            "Grade ACTUAL against the RUBRIC, using REFERENCE as a guide to what a full-marks answer contains. "
            f"RUBRIC: {rubric}\nREFERENCE: {reference}\nACTUAL:\n<<<\n{(actual or '')[:20000]}\n>>>\n"
            'Return JSON {"score": <0.0-1.0 in steps of 0.1>, "reasoning": "..."}.'
        )
        data, cost = self._call(user, tag)
        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(1.0, round(score, 2))), {"reasoning": data.get("reasoning", "")}, cost
