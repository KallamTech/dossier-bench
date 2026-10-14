"""Agent scenarios: cases/<case>/agent/scenarios/*.yaml

- id: qa-ld-rate
  group: qa-grounded                   # report group (default: file stem)
  tags: [qa, core]
  turns:
    - content: "What is the liquidated damages rate?"
      timeout_s: 300
      expect:
        answer: { must_facts: [...], must_not_claim: [...] }   # LLM judge; phrase match without one
        answer_judge: { reference: "...", rubric: "..." }     # graded 0..1 against a rubric
        contains: ["0.5%"]                                     # literal phrases
        not_contains: ["PINEAPPLE"]
        citations: { must_include_docs: [contract-v1], min: 1 }
        max_latency_s: 60
        max_cost_usd: 0.25
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

EXPECT_KEYS = {"terminal", "answer", "answer_judge", "contains", "not_contains", "citations", "max_latency_s", "max_cost_usd"}


@dataclass
class Turn:
    content: str
    expect: dict = field(default_factory=dict)
    timeout_s: float | None = None


@dataclass
class Scenario:
    id: str
    group: str
    turns: list[Turn]
    tags: list[str] = field(default_factory=list)
    description: str = ""
    path: Path | None = None


def load_scenarios(case_dir: Path) -> list[Scenario]:
    out: list[Scenario] = []
    for p in sorted((case_dir / "agent" / "scenarios").glob("*.yaml")):
        docs = yaml.safe_load(p.read_text())
        if not docs:
            continue
        for d in (docs if isinstance(docs, list) else [docs]):
            turns = []
            for t in d["turns"]:
                unknown = set(t) - set(Turn.__dataclass_fields__)
                if unknown:
                    raise SystemExit(f"{p.name}: scenario {d['id']} has unknown turn keys {sorted(unknown)}")
                bad = set(t.get("expect") or {}) - EXPECT_KEYS
                if bad:
                    raise SystemExit(f"{p.name}: scenario {d['id']} has unknown expect keys {sorted(bad)}")
                turns.append(Turn(**t))
            out.append(Scenario(id=d["id"], group=d.get("group", p.stem), turns=turns, tags=d.get("tags", []), description=d.get("description", ""), path=p))
    ids = [s.id for s in out]
    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        raise SystemExit(f"duplicate scenario ids: {sorted(dups)}")
    return out


def select(scenarios: list[Scenario], ids: list[str] | None = None, tags: list[str] | None = None, groups: list[str] | None = None) -> list[Scenario]:
    sel = scenarios
    if ids:
        sel = [s for s in sel if s.id in ids]
    if tags:
        sel = [s for s in sel if set(tags) & set(s.tags)]
    if groups:
        sel = [s for s in sel if s.group in groups]
    return sorted(sel, key=lambda s: (s.group, s.id))
