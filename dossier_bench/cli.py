"""dossier-bench CLI. See README for the flow."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from rich.console import Console

from dossier_bench import __version__
from dossier_bench.adapter import Adapter
from dossier_bench.cases import Case, available
from dossier_bench.config import Config
from dossier_bench.results import Run, RunLock

console = Console()


def load_adapter(cfg: Config, override: str | None = None):
    spec = override or cfg.require("adapter", "class")
    mod, _, cls = spec.partition(":")
    if not cls:
        raise SystemExit(f"adapter {spec!r}: expected module:Class")
    return getattr(importlib.import_module(mod), cls)(**(cfg.get("adapter", "options", default={}) or {}))


def _judge(cfg: Config, run: Run, enabled: bool):
    if not enabled:
        return None
    if not cfg.get("judge", "model"):
        console.print("[yellow]judge.model not set; judge-based scorers fall back to deterministic approximations or skip[/yellow]")
        return None
    from dossier_bench.judge import Judge

    return Judge(cfg, run)


def _setup(cfg: Config) -> None:
    Run.root = Path(cfg["runs_dir"])


def _resolve_run(args) -> Run:
    if getattr(args, "run", None):
        st = json.loads((Run.root / args.run / "state.json").read_text())
        return Run(args.run, st["case"], st.get("case_id"))
    r = Run.latest()
    if r is None:
        console.print("[red]no runs yet; start with `dossier-bench run`[/red]")
        sys.exit(2)
    return r


def _write_report(cfg: Config, run: Run, baseline: str | None) -> int:
    from dossier_bench.report import compare, load_baseline, render_markdown

    base = load_baseline(baseline, run.case, Path(cfg["baselines_dir"])) if baseline else None
    md = render_markdown(run, cfg, base, baseline)
    (run.dir / "report.md").write_text(md)
    _, violations = compare(run, base, cfg)
    console.print(md)
    console.print(f"report: {run.dir / 'report.md'}")
    return 1 if violations else 0


def cmd_run(args) -> int:
    cfg = Config.load(args.config)
    _setup(cfg)
    case = Case.load(args.case)
    adapter = load_adapter(cfg, args.adapter)
    run = Run.new(case.name, None, args.label)
    run.state["case_version"] = case.version
    run.meta.update({"adapter": getattr(adapter, "name", type(adapter).__name__), "dossier_bench": __version__, "config": cfg.snapshot()})
    judge = _judge(cfg, run, not args.no_judge)
    suites = {"ingest", "agent"} if args.suite == "all" else {args.suite}
    with RunLock(run):
        if "ingest" in suites:
            from dossier_bench.ingest import GROUPS, IngestScorer

            only = [g.strip() for g in args.only.split(",")] if args.only else None
            bad = [g for g in (only or []) if g not in GROUPS]
            if bad:
                raise SystemExit(f"unknown groups {bad}; choose from {GROUPS}")
            console.log(f"ingesting {len(case.files)} files with {run.meta['adapter']}…")
            result = adapter.ingest(case)
            search = None
            if type(adapter).search is not Adapter.search and (only is None or "search" in only):
                search = lambda q, k: adapter.search(case, q, k)  # noqa: E731
            for item in IngestScorer(result, case.expected, judge, search, console).run(only):
                run.add(item)
            run.save()
        if "agent" in suites:
            from dossier_bench.qa import AgentRunner
            from dossier_bench.scenario import load_scenarios, select

            scs = select(load_scenarios(case.dir), args.scenario.split(",") if args.scenario else None, args.tag.split(",") if args.tag else None, args.group.split(",") if args.group else None)
            console.log(f"{len(scs)} scenario(s)")
            AgentRunner(adapter, case, run, judge, console).run_all(scs, repeat=int(args.repeat))
            run.save()
    return _write_report(cfg, run, args.baseline)


def cmd_report(args) -> int:
    cfg = Config.load(args.config)
    _setup(cfg)
    return _write_report(cfg, _resolve_run(args), args.baseline)


def cmd_compare(args) -> int:
    rc = cmd_report(args)
    console.print("[green]no regressions[/green]" if rc == 0 else "[red]regressions found[/red]")
    return rc


def cmd_baseline_save(args) -> int:
    from dossier_bench.report import save_baseline

    cfg = Config.load(args.config)
    _setup(cfg)
    console.print(f"saved {save_baseline(_resolve_run(args), args.label, Path(cfg['baselines_dir']))}")
    return 0


def cmd_cases(args) -> int:
    for name in available():
        c = Case.load(name)
        console.print(f"{name:<28} v{c.version}  {len(c.files)} files  {c.expected.manifest.get('case_name', '')}")
    return 0


def cmd_scenarios(args) -> int:
    from dossier_bench.scenario import load_scenarios

    for s in load_scenarios(Case.load(args.case).dir):
        console.print(f"{s.id:<34} group={s.group:<18} tags={','.join(s.tags):<30} turns={len(s.turns)}")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="dossier-bench")
    p.add_argument("--config", help="path to dossier-bench.yaml")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="ingest a case through the adapter and/or run its agent scenarios, then score")
    s.add_argument("--case", default="harbor-dispute")
    s.add_argument("--suite", choices=["all", "ingest", "agent"], default="all")
    s.add_argument("--adapter", help="module:Class, overrides adapter.class")
    s.add_argument("--only", help="comma list of ingestion groups")
    s.add_argument("--scenario"); s.add_argument("--tag"); s.add_argument("--group"); s.add_argument("--repeat", default="1")
    s.add_argument("--no-judge", action="store_true"); s.add_argument("--label"); s.add_argument("--baseline")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("report"); s.add_argument("--run"); s.add_argument("--baseline"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("compare"); s.add_argument("--run"); s.add_argument("--baseline", required=True); s.set_defaults(fn=cmd_compare)
    b = sub.add_parser("baseline").add_subparsers(dest="sub", required=True)
    s = b.add_parser("save"); s.add_argument("--run"); s.add_argument("--label", required=True); s.set_defaults(fn=cmd_baseline_save)
    s = sub.add_parser("cases"); s.set_defaults(fn=cmd_cases)
    s = sub.add_parser("scenarios"); s.add_argument("--case", default="harbor-dispute"); s.set_defaults(fn=cmd_scenarios)

    args = p.parse_args(argv)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
