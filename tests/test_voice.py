import shutil
import time

import pytest

from dossier_bench.adapter import Adapter, VoiceRun, VoiceTurnResult
from dossier_bench.audio import pcm_chunks
from dossier_bench.cases import Case
from dossier_bench.report import render_markdown
from dossier_bench.config import Config
from dossier_bench.results import Run
from dossier_bench.voice import VoiceRunner, load_voice_scenarios, score_voice_turn


def test_scenarios_resolve_with_audio():
    for name in ("harbor-dispute", "brightwater-acquisition", "kite-launch"):
        case = Case.load(name)
        scs = load_voice_scenarios(case.dir)
        assert scs
        for sc in scs:
            for vt in VoiceRunner(Adapter(), case, None, None).turns_for(sc):
                assert vt.audio.exists() and vt.duration_s > 0
    barge = next(s for s in load_voice_scenarios(Case.load("harbor-dispute").dir) if s.id == "voice-barge-in")
    assert barge.barge_in == [None, 3]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_pcm_chunks_are_paced():
    clip = Case.load("kite-launch").audio.turns[("voice-launch-date", 1)]
    t0 = time.monotonic()
    chunks = list(pcm_chunks(clip.path, rate=16_000, chunk_ms=500, realtime=False))
    assert time.monotonic() - t0 < clip.duration_s / 2
    assert abs(sum(len(c) for c in chunks) / 32_000 - clip.duration_s) < 0.2


def test_score_turn_latency_and_barge_in():
    prev = VoiceTurnResult(reply_text="long answer", speech_end_s=0.0, first_audio_s=0.5, reply_end_s=4.2, barge_in_s=3.5)
    res = VoiceTurnResult(reply_text="The cap is 10% of the contract price.", user_transcript="sorry stop there just tell me the cap", speech_end_s=5.0, first_audio_s=5.6, reply_end_s=8.0)
    expect = {"answer": {"must_facts": ["10%"]}, "max_first_audio_s": 1.0}
    q, passed, det, _ = score_voice_turn(expect, res, prev, 3, "Sorry, stop there. Just tell me the cap.", "en", None, "t")
    assert det["first_audio_s"] == 0.6 and det["turn_latency_s"] == 3.0 and det["stopped_after_s"] == 0.7
    assert det["subscores"] == {"terminal": 1.0, "answer_phrases": 1.0, "first_audio_budget": 1.0, "barge_in": 1.0}
    assert q == 1.0 and passed and det["heard_wer"] == 0.0
    slow_stop = VoiceTurnResult(reply_text="x", speech_end_s=0, first_audio_s=1, reply_end_s=9, barge_in_s=3.5)
    _, _, det2, _ = score_voice_turn({}, res, slow_stop, 3, "q", "en", None, "t")
    assert det2["subscores"]["barge_in"] == 0.0


class FakeVoice(Adapter):
    name = "fake-voice"

    def voice_session(self, case, turns):
        out, clock = [], 0.0
        for vt in turns:
            clock += vt.duration_s
            out.append(VoiceTurnResult(reply_text="It is capped at 10% of the contract price.", user_transcript=vt.text, sources=["contract-v1"], speech_end_s=clock, first_audio_s=clock + 0.4, reply_end_s=clock + 3))
        if len(out) > 1:
            out[0].barge_in_s, out[0].reply_end_s = out[0].first_audio_s + 3, out[0].first_audio_s + 3.3
        return VoiceRun(out, cost_usd=0.01)


def test_runner_and_report(tmp_path):
    Run.root = tmp_path
    case = Case.load("harbor-dispute")
    run = Run.new(case.name, None)
    items = VoiceRunner(FakeVoice(), case, run, None).run_all(load_voice_scenarios(case.dir))
    assert [i.item for i in items] == ["voice-ld-rate#t1", "voice-power-supply#t1", "voice-barge-in#t1", "voice-barge-in#t2"]
    barge = items[-1]
    assert barge.detail["subscores"]["barge_in"] == 1.0 and barge.passed
    assert sum(i.cost_usd or 0 for i in items) == 0.03
    md = render_markdown(run, Config())
    assert "## Voice" in md and "| 4 | 0.40s |" in md
