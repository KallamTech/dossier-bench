"""Voice suite: realtime spoken conversations (agent/voice/*.yaml) through `adapter.voice_session`.

- id: voice-ld-rate
  from: qa-ld-rate                 # reuse a text scenario's turns + expectations
  voice: en-gb-f
  say: ["..."]                     # optional per-turn override of what the voice reads
- id: voice-barge-in
  voice: en-us-m
  turns:
    - content: "Walk me through the expert report."
    - content: "Stop. Just the cap."
      barge_in_after_s: 3          # speak over the previous reply, 3 s after its first audio
      expect: { answer: { must_facts: [...] }, max_first_audio_s: 2 }

Each reply is scored like an agent turn (on its transcript), plus time to first audio, turn latency and, for
barge-in turns, how fast the interrupted reply stopped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml
from rich.console import Console

from dossier_bench.adapter import Adapter, Answer, VoiceRun, VoiceTurn, VoiceTurnResult
from dossier_bench.cases import Case
from dossier_bench.judge import Judge
from dossier_bench.qa import PASS, score_answer
from dossier_bench.results import Item, Run
from dossier_bench.scenario import EXPECT_KEYS, Turn, load_scenarios
from dossier_bench.transcription import wer

VOICE_EXPECT_KEYS = EXPECT_KEYS | {"max_first_audio_s", "max_stop_s"}
DEFAULT_MAX_STOP_S = 1.0


@dataclass
class VoiceScenario:
    id: str
    group: str
    voice: str
    turns: list[Turn]
    barge_in: list[float | None]
    tags: list[str] = field(default_factory=list)


def load_voice_scenarios(case_dir) -> list[VoiceScenario]:
    texts = {s.id: s for s in load_scenarios(case_dir)}
    out = []
    for p in sorted((case_dir / "agent" / "voice").glob("*.yaml")):
        for d in yaml.safe_load(p.read_text()) or []:
            if d.get("from"):
                src = texts.get(d["from"])
                if src is None:
                    raise SystemExit(f"{p.name}: {d['id']} reuses unknown scenario {d['from']!r}")
                turns, barge = list(src.turns), [None] * len(src.turns)
            else:
                turns, barge = [], []
                for t in d["turns"]:
                    bad = set(t.get("expect") or {}) - VOICE_EXPECT_KEYS
                    if bad:
                        raise SystemExit(f"{p.name}: {d['id']} has unknown expect keys {sorted(bad)}")
                    turns.append(Turn(t["content"], t.get("expect") or {}, t.get("timeout_s")))
                    barge.append(t.get("barge_in_after_s"))
            out.append(VoiceScenario(d["id"], d.get("group", "voice"), d["voice"], turns, barge, d.get("tags", [])))
    return out


def _span(a: float | None, b: float | None) -> float | None:
    return round(b - a, 3) if a is not None and b is not None else None


def score_voice_turn(expect: dict, res: VoiceTurnResult, prev: VoiceTurnResult | None, barge_in_after_s: float | None, spoken: str, language: str, judge: Judge | None, tag: str) -> tuple[float | None, bool, dict, float]:
    ttfa = _span(res.speech_end_s, res.first_audio_s)
    turn_latency = _span(res.speech_end_s, res.reply_end_s)
    ans = Answer(res.reply_text or "", sources=res.sources, latency_s=turn_latency, terminal=res.terminal, error=res.error)
    subs, det, jcost = score_answer({k: v for k, v in expect.items() if k not in ("max_first_audio_s", "max_stop_s")}, ans, judge, question=spoken, tag=tag)
    det.update({"first_audio_s": ttfa, "turn_latency_s": turn_latency, "content_preview": (res.reply_text or "")[:400]})
    if expect.get("max_first_audio_s") is not None and ttfa is not None:
        subs["first_audio_budget"] = 1.0 if ttfa <= float(expect["max_first_audio_s"]) else 0.0
    if barge_in_after_s is not None:
        stop = _span(prev.barge_in_s, prev.reply_end_s) if prev else None
        det["stopped_after_s"] = stop
        subs["barge_in"] = 1.0 if stop is not None and stop <= float(expect.get("max_stop_s", DEFAULT_MAX_STOP_S)) else 0.0
    if res.user_transcript is not None:
        det["heard"] = res.user_transcript
        det["heard_wer"] = wer(spoken, res.user_transcript, language)[0]
    det["subscores"] = subs
    q = round(sum(subs.values()) / len(subs), 3) if subs else None
    return q, (q is not None and q >= PASS and res.terminal == expect.get("terminal", "done")), det, jcost


class VoiceRunner:
    def __init__(self, adapter: Adapter, case: Case, run: Run, judge: Judge | None, console: Console | None = None):
        self.adapter, self.case, self.run, self.judge = adapter, case, run, judge
        self.console = console or Console()

    def turns_for(self, sc: VoiceScenario) -> list[VoiceTurn]:
        out = []
        for ti, turn in enumerate(sc.turns, start=1):
            a = self.case.audio.turns.get((sc.id, ti))
            if a is None:
                raise SystemExit(f"no audio for {sc.id} turn {ti}; run scripts/gen_audio.py")
            out.append(VoiceTurn(a.path, turn.content, a.language, a.duration_s, sc.barge_in[ti - 1]))
        return out

    def run_scenario(self, sc: VoiceScenario) -> list[Item]:
        turns = self.turns_for(sc)
        self.console.log(f"[bold]{sc.id}[/bold] ({len(turns)} spoken turn(s))")
        try:
            vr = self.adapter.voice_session(self.case, turns)
        except Exception as e:  # noqa: BLE001
            vr = VoiceRun([VoiceTurnResult(terminal="error", error=str(e)[:500]) for _ in turns])
        items = []
        for ti, (turn, vt) in enumerate(zip(sc.turns, turns), start=1):
            res = vr.turns[ti - 1] if ti <= len(vr.turns) else VoiceTurnResult(terminal="error", error="no result for this turn")
            prev = vr.turns[ti - 2] if ti >= 2 and ti - 2 < len(vr.turns) else None
            q, passed, det, jcost = score_voice_turn(turn.expect or {}, res, prev, vt.barge_in_after_s, turn.content, vt.language, self.judge, f"{sc.id}-t{ti}")
            # session cost lands on the first turn so group sums stay right
            cost = vr.cost_usd if ti == 1 else None
            items.append(self.run.add(Item("voice", sc.group, f"{sc.id}#t{ti}", quality=q, latency_s=det.get("turn_latency_s"), cost_usd=cost, passed=passed, detail=det, judge_cost_usd=jcost)))
            self.console.log(f"  t{ti} q={q} first_audio={det.get('first_audio_s')}s turn={det.get('turn_latency_s')}s")
        self.run.write_raw(f"voice/{sc.id}.json", {"turns": [t.__dict__ for t in vr.turns], "cost_usd": vr.cost_usd, "usage": vr.usage})
        self.run.save()
        return items

    def run_all(self, scenarios: list[VoiceScenario]) -> list[Item]:
        return [i for sc in scenarios for i in self.run_scenario(sc)]
