"""Scenario files must load and reference real document keys."""

import re

import pytest

from dossier_bench.cases import Case, available
from dossier_bench.scenario import load_scenarios, select


@pytest.mark.parametrize("name", available())
def test_scenarios_are_well_formed(name):
    case = Case.load(name)
    scs = load_scenarios(case.dir)
    assert len(scs) >= 10
    keys = set(case.filename_to_key.values())
    for sc in scs:
        assert re.fullmatch(r"[a-z0-9-]+", sc.id), sc.id
        assert sc.turns, sc.id
        for t in sc.turns:
            assert t.content.strip()
            assert t.expect, (sc.id, "turn has no expectation")
            for k in (t.expect.get("citations") or {}).get("must_include_docs", []):
                assert k in keys, (sc.id, k)


def test_select_filters():
    scs = load_scenarios(Case.load("harbor-dispute").dir)
    assert all("core" in s.tags for s in select(scs, tags=["core"]))
    assert [s.id for s in select(scs, ids=[scs[0].id])] == [scs[0].id]


@pytest.mark.parametrize("name", available())
def test_case_files_exist(name):
    case = Case.load(name)
    missing = [p.name for p in case.files if not p.exists()]
    assert not missing
    assert set(case.filename_to_key.values()) >= {d["key"] for d in case.expected.documents}
