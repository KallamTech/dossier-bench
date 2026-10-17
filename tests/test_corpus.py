"""Ground truth must be checkable against the corpus it ships with."""

import pytest

from dossier_bench import cases, scorers as S
from dossier_bench.scenario import load_scenarios

CASES = cases.available()


@pytest.mark.parametrize("name", CASES)
def test_must_phrases_are_in_reference_text(name):
    exp = cases.Expected(cases.Case.load(name).dir)
    bad = {}
    for d in exp.documents:
        if "copy_of" in d or not d.get("must_phrases"):
            continue
        text = exp.text(d["key"])
        assert text is not None, f"{d['key']} has must_phrases but no expected/text/{d['key']}.txt"
        _, missing = S.phrase_coverage(text, d["must_phrases"])
        if missing:
            bad[d["key"]] = missing
    assert not bad


@pytest.mark.parametrize("name", CASES)
def test_search_expectations_name_real_documents(name):
    exp = cases.Expected(cases.Case.load(name).dir)
    unknown = {k for q in exp.search for k in q["expect"]} - set(exp.by_key)
    assert not unknown


@pytest.mark.parametrize("name", CASES)
def test_scenario_citations_name_real_documents(name):
    case = cases.Case.load(name)
    exp = cases.Expected(case.dir)
    cited = {
        k
        for sc in load_scenarios(case.dir)
        for t in sc.turns
        for k in ((t.expect or {}).get("citations") or {}).get("must_include_docs", [])
    }
    assert not cited - set(exp.by_key)
