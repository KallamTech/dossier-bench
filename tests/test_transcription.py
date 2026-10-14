import hashlib

import pytest

from dossier_bench.adapter import Adapter, Transcript
from dossier_bench.cases import Case, available
from dossier_bench.results import Run
from dossier_bench.transcription import TranscriptionRunner, cer, entity_accuracy, language_code, normalize, score_clip, wer


@pytest.mark.parametrize("name", available())
def test_audio_manifest_matches_files(name):
    audio = Case.load(name).audio
    assert audio.exists and audio.clips and audio.turns
    for c in audio.clips:
        assert c.path.exists() and 0 < c.duration_s <= 60
        assert c.variant in ("clean", "noise") and c.language in ("en", "es")
    for c in audio.clips + list(audio.turns.values()):
        want = next(x["sha256"] for x in _manifest(name) if x["file"] == str(c.path.relative_to(Case.load(name).dir)))
        assert hashlib.sha256(c.path.read_bytes()).hexdigest() == want


def _manifest(name):
    import yaml

    d = yaml.safe_load((Case.load(name).dir / "expected" / "audio.yaml").read_text())
    return d["clips"] + d["voice_turns"]


@pytest.mark.parametrize("name", available())
def test_entities_appear_in_reference(name):
    for c in Case.load(name).audio.clips:
        acc, det = entity_accuracy(c.entities, c.text, c.language)
        assert acc == 1.0, [e for e in det if not e["found"]]


def test_normalisation():
    assert normalize("The price is €1,288,000.") == "the price is 1288000"
    assert normalize("1,288,000 euros") == "1288000"
    assert normalize("del 0,5 % semanal", "es") == "del 0.5 por ciento semanal"
    assert normalize("by the 7th of February, 2025") == "by the 7 february 2025"
    assert normalize("clause 6.4, CO-3") == "clause 6.4 co 3"
    assert normalize("25 per cent") == normalize("25%") == "25 percent"
    assert normalize("£2,400, 000 and Kite Planner 3 .0") == "2400000 and kite planner 3.0"
    assert normalize("unas 180 000 libras", "es") == "unas 180000"


def test_wer_cer_and_language():
    assert wer("a b c d", "a x c d")[:2] == (0.25, 1)
    assert wer("a b", "a b")[0] == 0.0
    assert cer("abcd", "abcf")[0] == 0.25
    assert language_code("Spanish") == "es" and language_code("en-GB") == "en" and language_code(None) is None


def test_score_clip_weights_entities():
    clip = next(c for c in Case.load("harbor-dispute").audio.clips if c.id == "memo-power")
    q, passed, det = score_clip(clip, Transcript(clip.text, language="english", latency_s=2.0))
    assert q == 1.0 and passed and det["wer"] == 0.0 and det["rtf"] > 0
    wrong = clip.text.replace("€1,288,000", "€1,280,000").replace("Priya Nair", "Prionair")
    q2, passed2, det2 = score_clip(clip, Transcript(wrong))
    assert det2["missing"] == ["Priya Nair", "€1,288,000"] and not passed2 and q2 < 1
    # an amount weighs twice a name
    assert det2["entity_accuracy"] == round(1 - 3 / sum({"name": 1, "amount": 2, "date": 1.5, "ref": 1.5}[e["kind"]] for e in det2["entities"]), 4)


class Perfect(Adapter):
    def transcribe(self, audio, language):
        clip = next(c for c in Case.load("kite-launch").audio.clips if c.path == audio)
        return Transcript(clip.text, language=clip.language)


class Broken(Adapter):
    def transcribe(self, audio, language):
        raise RuntimeError("boom")


def test_runner(tmp_path):
    Run.root = tmp_path
    run = Run.new("kite-launch", None)
    items = TranscriptionRunner(Perfect(), Case.load("kite-launch"), run).run_all(variants=["clean"])
    assert len(items) == 3 and all(i.quality == 1.0 and i.passed for i in items)
    assert {i.group for i in items} == {"en-clean", "es-clean"}
    bad = TranscriptionRunner(Broken(), Case.load("kite-launch"), Run.new("kite-launch", None)).run_all(clips=["budget"])
    assert [(i.item, i.quality, i.passed) for i in bad] == [("budget", 0.0, False), ("budget.noise", 0.0, False)]
    assert Perfect().supports("transcription") and not Perfect().supports("voice") and not Adapter().supports("transcription")
