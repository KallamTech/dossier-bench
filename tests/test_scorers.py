from datetime import date

from dossier_bench import scorers as S


def test_phrase_coverage_normalises_numbers_and_accents():
    text = "El precio es 1.288.000 € y el plazo vence el 23/04/2025 en Valencia."
    cov, missing = S.phrase_coverage(text, ["1,288,000", "23/04/2025", "valencia", "not here"])
    assert cov == 0.75 and missing == ["not here"]


def test_date_score_levels():
    assert S.date_score(date(2025, 3, 24), (2025, 3, 24))[0] == 1.0
    assert S.date_score(date(2025, 3, 24), (2025, 3, 25))[0] == 0.5
    assert S.date_score(date(2025, 3, 24), (2025, 1, 1))[0] == 0.2
    assert S.date_score(date(2025, 3, 24), (None, None, None))[0] == 0.0
    assert S.date_score(None, (2025, 3, 24), "none")[0] == 1.0


def test_in_set_partial():
    assert S.in_set("contract", ["contract", "agreement"])[0] == 1.0
    assert S.in_set("supply contract", ["contract"])[0] == 0.7
    assert S.in_set("photo", ["contract"])[0] == 0.0


def test_set_f1_and_recall():
    f1, d = S.set_f1(["a", "b", "c"], ["A", "c", "x"])
    assert d["recall"] == round(2 / 3, 3) and d["precision"] == round(2 / 3, 3)
    assert d["missing"] == ["b"] and d["unexpected"] == ["x"]
    assert abs(f1 - 0.667) < 0.01


def test_name_match_aliases_and_fuzz():
    assert S.name_match("Tidewell Fabrication Ltd", "Tidewell Fabrication Limited")
    assert S.name_match("Tidewell Fabrication Ltd", "HFL", aliases=["HFL"])
    assert S.name_match("Lucía Ortega", "Lucia Ortega")
    assert not S.name_match("Lucía Ortega", "Marc Pujol")


def test_mrr():
    score, d = S.mrr(["contract-v1", "contract-v2"], ["tender", "contract-v2", "x"], k=3)
    assert score == 0.5 and d["hit_at_k"] == 0.5 and d["missing"] == ["contract-v1"]
    assert S.mrr(["a"], ["b", "c"], 2)[0] == 0.0


def test_text_similarity_tolerates_ocr_noise():
    a = "The slab between grid lines C4 and C7 shows a depression of 18 mm."
    b = "The slab between grid lines C4 and C7 sh0ws a depresion of 18 mm"
    assert S.text_similarity(a, b) > 0.9


def test_urls_note():
    note = S.urls_note(["https://a.example/x", "", "https://a.example/x"])
    assert note.count("https://a.example/x") == 1
    assert S.urls_note([]) == ""
