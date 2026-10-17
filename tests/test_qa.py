from dossier_bench.adapter import Adapter, Answer
from dossier_bench.cases import Case
from dossier_bench.qa import AgentRunner, score_answer
from dossier_bench.results import Run
from dossier_bench.scenario import Scenario, Turn


def test_content_citations_and_budgets():
    expect = {
        "contains": ["0.5%", "10%"], "not_contains": ["PINEAPPLE"], "citations": {"must_include_docs": ["contract-v1"]},
        "max_latency_s": 10, "max_cost_usd": 0.5, "answer": {"must_facts": ["0.5%", "capped at 10%"]},
    }
    ans = Answer("The rate is 0.5% per week, capped at 10%.", sources=["contract-v1"], latency_s=3.0, cost_usd=0.02)
    subs, det, jc = score_answer(expect, ans, None, question="q", tag="t")
    assert set(subs) == {"terminal", "contains", "not_contains", "citations", "latency_budget", "cost_budget"}
    assert all(v == 1.0 for v in subs.values()) and jc == 0.0
    assert det["cited"] == ["contract-v1"]


def test_failures_score_down():
    expect = {"citations": {"must_include_docs": ["termination"]}, "not_contains": ["PINEAPPLE"], "max_latency_s": 1}
    subs, det, _ = score_answer(expect, Answer("PINEAPPLE", terminal="error", error="boom", latency_s=5), None, question="q", tag="t")
    assert subs == {"terminal": 0.0, "not_contains": 0.0, "citations": 0.0, "latency_budget": 0.0}
    assert det["error"] == "boom" and det["missing_citations"] == ["termination"]


class Echo(Adapter):
    def __init__(self):
        self.seen = []

    def ask(self, case, question, history):
        self.seen.append(len(history))
        return Answer(f"echo {question}", sources=["contract-v1"])


def test_runner_threads_history(tmp_path):
    Run.root = tmp_path
    run = Run.new("harbor-dispute", None)
    sc = Scenario("s", "g", [Turn("a", {"contains": ["echo a"]}), Turn("b", {"contains": ["echo b"]})])
    ad = Echo()
    items = AgentRunner(ad, Case.load("harbor-dispute"), run, None).run_all([sc])
    assert [i.item for i in items] == ["s#t1", "s#t2"] and all(i.quality == 1.0 for i in items)
    assert ad.seen == [0, 2]
