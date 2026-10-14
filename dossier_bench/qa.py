"""Agent suite: ask each scenario's turns through the adapter, score every answer."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console

from dossier_bench import scorers as S
from dossier_bench.adapter import Adapter, Answer
from dossier_bench.cases import Case
from dossier_bench.judge import Judge
from dossier_bench.results import Item, Run
from dossier_bench.scenario import Scenario

PASS = 0.6


def score_answer(expect: dict, answer: Answer, judge: Judge | None, *, question: str, tag: str) -> tuple[dict[str, float], dict[str, Any], float]:
    """Sub-scores (each 0..1), detail and judge cost for one reply. Budgets must already be plain numbers."""
    subs: dict[str, float] = {}
    det: dict[str, Any] = {"terminal": answer.terminal}
    jcost = 0.0
    want_terminal = expect.get("terminal", "done")
    subs["terminal"] = 1.0 if answer.terminal == want_terminal else 0.0
    if answer.terminal != want_terminal:
        det["error"] = answer.error or f"terminal={answer.terminal}, expected {want_terminal}"
    content = answer.text or ""
    if expect.get("contains"):
        subs["contains"], det["missing"] = S.phrase_coverage(content, expect["contains"])
    if expect.get("not_contains"):
        present = [p for p in expect["not_contains"] if S.norm(p) in S.norm(content)]
        subs["not_contains"] = 1.0 if not present else 0.0
        det["forbidden_present"] = present
    a = expect.get("answer")
    judged = content + S.urls_note(answer.urls)
    if a and judge is not None:
        q, d, c = judge.facts(judged, a.get("must_facts", []), a.get("must_not_claim"), context=f"Agent reply to: {question[:300]}", tag=tag)
        subs["answer"] = q
        jcost += c
        det.update({k: d[k] for k in ("missing", "contradicted", "forbidden_present") if d.get(k)})
        det["reasoning"] = d.get("reasoning", "")
    elif a:
        subs["answer_phrases"], det["missing"] = S.phrase_coverage(content, a.get("must_facts", []))
    aj = expect.get("answer_judge")
    if aj and judge is not None:
        q, d, c = judge.rubric(judged, aj.get("reference", ""), aj.get("rubric", ""), tag=f"{tag}-rubric")
        subs["rubric"] = q
        jcost += c
        det["rubric_reasoning"] = d.get("reasoning", "")
    cit = expect.get("citations")
    if cit:
        cited = set(answer.sources)
        det["cited"] = sorted(cited)
        must = cit.get("must_include_docs", [])
        miss = [k for k in must if k not in cited]
        ok_min = len(cited) >= int(cit.get("min", 1 if must else 0))
        subs["citations"] = round((1 - len(miss) / max(1, len(must))) * (1.0 if ok_min else 0.5), 3) if (must or cit.get("min")) else 1.0
        det["missing_citations"] = miss
    ml = expect.get("max_latency_s")
    if ml is not None and answer.latency_s is not None:
        subs["latency_budget"] = 1.0 if answer.latency_s <= float(ml) else 0.0
    mc = expect.get("max_cost_usd")
    if mc is not None and answer.cost_usd is not None:
        subs["cost_budget"] = 1.0 if answer.cost_usd <= float(mc) else 0.0
    return subs, det, jcost


class AgentRunner:
    def __init__(self, adapter: Adapter, case: Case, run: Run, judge: Judge | None, console: Console | None = None):
        self.adapter, self.case, self.run, self.judge = adapter, case, run, judge
        self.console = console or Console()

    def run_scenario(self, sc: Scenario, rep: int = 0) -> list[Item]:
        label = sc.id + (f"#r{rep}" if rep else "")
        self.run.items = [i for i in self.run.items if not (i.suite == "agent" and (i.item == label or i.item.startswith(label + "#")))]
        history: list[dict[str, str]] = []
        items: list[Item] = []
        self.console.log(f"[bold]{label}[/bold]")
        for ti, turn in enumerate(sc.turns, start=1):
            t0 = time.monotonic()
            try:
                ans = self.adapter.ask(self.case, turn.content, list(history))
            except Exception as e:  # noqa: BLE001
                ans = Answer(text="", terminal="error", error=str(e)[:500])
            if ans.latency_s is None:
                ans.latency_s = round(time.monotonic() - t0, 3)
            subs, det, jcost = score_answer(turn.expect or {}, ans, self.judge, question=turn.content, tag=f"{sc.id}-t{ti}")
            det["subscores"] = subs
            det.update({"ttft_s": ans.ttft_s, "usage": ans.usage, "content_preview": (ans.text or "")[:400]})
            q = round(sum(subs.values()) / len(subs), 3) if subs else None
            passed = q is not None and q >= PASS and ans.terminal == (turn.expect or {}).get("terminal", "done")
            items.append(self.run.add(Item("agent", sc.group, f"{label}#t{ti}", quality=q, latency_s=ans.latency_s, cost_usd=ans.cost_usd, passed=passed, detail=det, judge_cost_usd=jcost)))
            self.run.write_raw(f"agent/{label}-t{ti}.json", {"turn": turn.content, "answer": ans.__dict__})
            self.console.log(f"  t{ti} q={q} {ans.latency_s}s ${ans.cost_usd}")
            self.run.save()
            if ans.terminal != "done":
                self.console.log(f"  [red]turn {ti} ended with {ans.terminal}: {ans.error}[/red]; skipping remaining turns")
                break
            history += [{"role": "user", "content": turn.content}, {"role": "assistant", "content": ans.text}]
        return items

    def run_all(self, scenarios: list[Scenario], repeat: int = 1) -> list[Item]:
        items: list[Item] = []
        for sc in scenarios:
            for rep in range(repeat):
                items.extend(self.run_scenario(sc, rep))
        return items
