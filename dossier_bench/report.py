"""results.json → report.md; compare against a baseline; save baselines."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from dossier_bench.results import Item, Run
from dossier_bench.scorers import pct_increase


def _fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "–"
    if kind == "q":
        return f"{v:.2f}"
    if kind == "s":
        return f"{v:.1f}s"
    if kind == "$":
        return f"${v:.4f}" if v < 0.1 else f"${v:.3f}"
    return str(v)


def _delta(new: float | None, base: float | None, kind: str) -> str:
    if new is None or base is None:
        return ""
    d = new - base
    if kind == "q":
        return f" ({d:+.2f})" if abs(d) >= 0.005 else " (=)"
    p = pct_increase(new, base)
    return f" ({p:+.0f}%)" if p is not None and abs(p) >= 1 else " (=)"


def aggregate(items: list[Item]) -> dict[str, dict[str, Any]]:
    """Per group: mean quality, latency (agent: sum of turns; ingestion: max, i.e. wall-clock),
    cost summed over per-item rows (the ingestion 'case.total' roll-up is excluded to avoid double counting)."""
    groups: dict[str, list[Item]] = defaultdict(list)
    for i in items:
        if i.skipped:
            continue
        groups[f"{i.suite}/{i.group}"].append(i)
    out = {}
    for g, its in sorted(groups.items()):
        qs = [i.quality for i in its if i.quality is not None]
        per_item = [i for i in its if i.item != "case.total"] or its
        ls = [i.latency_s for i in per_item if i.latency_s is not None]
        cs = [i.cost_usd for i in per_item if i.cost_usd is not None]
        ps = [i.passed for i in its if i.passed is not None]
        out[g] = {
            "n": len(its),
            "quality": round(statistics.mean(qs), 3) if qs else None,
            "latency_s": round(max(ls) if its[0].suite == "ingestion" else sum(ls), 1) if ls else None,
            "cost_usd": round(sum(cs), 4) if cs else None,
            "pass_rate": round(sum(1 for p in ps if p) / len(ps), 3) if ps else None,
            "judge_cost_usd": round(sum(i.judge_cost_usd for i in its), 4),
        }
    return out


def load_baseline(path: str | Path, case: str | None = None, baselines: Path = Path("baselines")) -> dict[str, Item]:
    """`path` is a file, or a label saved by `baseline save` for this case."""
    p = Path(path)
    if not p.exists() and case:
        p = baselines / case / f"{path}.json"
    if not p.exists():
        have = sorted(x.stem for x in (baselines / case).glob("*.json")) if case and (baselines / case).exists() else []
        raise SystemExit(f"baseline {path!r} not found; saved labels for {case}: {have}")
    data = json.loads(p.read_text())
    return {Item(**i).key(): Item(**i) for i in data.get("items", [])}


def compare(run: Run, baseline: dict[str, Item] | None, cfg) -> tuple[list[dict], list[dict]]:
    """Returns (rows, violations). A violation is a threshold breach vs baseline or an absolute quality floor miss."""
    th = cfg["thresholds"]
    rows, violations = [], []
    for i in sorted(run.items, key=lambda x: x.key()):
        b = baseline.get(i.key()) if baseline else None
        row = {"key": i.key(), "item": i, "base": b, "flags": []}
        if i.skipped:
            rows.append(row)
            continue
        if i.quality is not None and i.quality < th["quality"]["min_abs"]:
            row["flags"].append(f"quality {i.quality:.2f} < floor {th['quality']['min_abs']}")
        if b is not None:
            if i.quality is not None and b.quality is not None and b.quality - i.quality > th["quality"]["max_drop"]:
                row["flags"].append(f"quality dropped {b.quality:.2f}→{i.quality:.2f}")
            p = pct_increase(i.latency_s, b.latency_s)
            if p is not None and p > th["latency"]["max_increase_pct"]:
                row["flags"].append(f"latency +{p:.0f}%")
            p = pct_increase(i.cost_usd, b.cost_usd)
            if p is not None and p > th["cost"]["max_increase_pct"]:
                row["flags"].append(f"cost +{p:.0f}%")
        if row["flags"]:
            violations.append(row)
        rows.append(row)
    return rows, violations


def render_markdown(run: Run, cfg, baseline: dict[str, Item] | None = None, baseline_name: str | None = None) -> str:
    rows, violations = compare(run, baseline, cfg)
    agg = aggregate(run.items)
    base_agg = aggregate(list(baseline.values())) if baseline else {}
    out = [f"# Benchmark report — {run.run_id}", ""]
    m = run.meta
    out.append(f"Case `{run.case}` v{run.state.get('case_version', '?')} · case id `{run.state.get('case_id')}` · started {m.get('started_at', '')[:19]}")
    if m.get("adapter"):
        out.append(f"System: `{m['adapter']}`")
    repos = m.get("repos") or {}
    if repos:
        out.append("Repos: " + ", ".join(f"{k} `{v}`" for k, v in repos.items() if v))
    if baseline_name:
        out.append(f"Baseline: `{baseline_name}`")
    out.append("")
    out.append("## Summary by group")
    out.append("")
    out.append("| group | n | quality | latency | cost | pass | judge $ |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for g, a in agg.items():
        b = base_agg.get(g, {})
        out.append(
            f"| {g} | {a['n']} | {_fmt(a['quality'], 'q')}{_delta(a['quality'], b.get('quality'), 'q')} | "
            f"{_fmt(a['latency_s'], 's')}{_delta(a['latency_s'], b.get('latency_s'), 's')} | "
            f"{_fmt(a['cost_usd'], '$')}{_delta(a['cost_usd'], b.get('cost_usd'), '$')} | "
            f"{_fmt(a['pass_rate'], 'q')} | {_fmt(a['judge_cost_usd'], '$')} |"
        )
    out.append("")
    if violations:
        out.append(f"## Regressions / floor misses ({len(violations)})")
        out.append("")
        for r in violations:
            out.append(f"- `{r['key']}`: " + "; ".join(r["flags"]))
        out.append("")
    out.append("## Items")
    out.append("")
    out.append("| item | quality | latency | cost | pass | detail |")
    out.append("|---|---:|---:|---:|:--:|---|")
    for r in rows:
        i, b = r["item"], r["base"]
        if i.skipped:
            out.append(f"| `{r['key']}` | – | – | – | skip | {i.skipped} |")
            continue
        det = _short_detail(i.detail)
        flag = " ⚠" if r["flags"] else ""
        out.append(
            f"| `{r['key']}`{flag} | {_fmt(i.quality, 'q')}{_delta(i.quality, b.quality if b else None, 'q')} | "
            f"{_fmt(i.latency_s, 's')}{_delta(i.latency_s, b.latency_s if b else None, 's')} | "
            f"{_fmt(i.cost_usd, '$')}{_delta(i.cost_usd, b.cost_usd if b else None, '$')} | "
            f"{'✓' if i.passed else ('✗' if i.passed is False else '–')} | {det} |"
        )
    skipped = [i for i in run.items if i.skipped]
    if skipped:
        out.append("")
        out.append(f"{len(skipped)} item(s) skipped.")
    return "\n".join(out) + "\n"


def _short_detail(d: dict) -> str:
    keys = ["why", "missing", "missing_phrases", "contradicted", "forbidden_present", "error", "actual", "tools", "missing_tools", "forbidden_tools", "reasoning", "injection"]
    parts = []
    for k in keys:
        if k in d and d[k] not in (None, [], "", {}):
            v = d[k]
            s = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
            parts.append(f"{k}: {s[:140]}")
        if len(parts) >= 3:
            break
    return "; ".join(parts).replace("|", "\\|").replace("\n", " ")


def save_baseline(run: Run, label: str, baselines: Path = Path("baselines")) -> Path:
    d = baselines / run.case
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{label}.json"
    p.write_text((run.dir / "results.json").read_text())
    return p
