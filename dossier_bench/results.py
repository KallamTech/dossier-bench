"""Result records + run folders. One Item per scored thing, same shape everywhere."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Item:
    suite: str                       # ingestion | agent
    group: str                       # documents, dates, summaries, ... | scenario group
    item: str                        # e.g. expert-report.summary_long, qa-grounded/notice-dates#t1
    quality: float | None = None     # 0..1
    latency_s: float | None = None
    cost_usd: float | None = None
    passed: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    judge_cost_usd: float = 0.0
    skipped: str | None = None       # reason, when not run

    def key(self) -> str:
        return f"{self.suite}/{self.group}/{self.item}"


class Run:
    """A run folder: <root>/<id>/{state.json,results.json,report.md,raw/}."""

    root: Path = Path("runs")

    def __init__(self, run_id: str, case: str, case_id: str | None = None, create: bool = False):
        self.run_id = run_id
        self.case = case
        self.dir = self.root / run_id
        self.raw = self.dir / "raw"
        if create:
            self.raw.mkdir(parents=True, exist_ok=True)
            (self.raw / "judge").mkdir(exist_ok=True)
        self.items: list[Item] = []
        self.meta: dict[str, Any] = {}
        self.state: dict[str, Any] = {"case": case, "case_id": case_id}
        if (self.dir / "state.json").exists():
            self.state.update(json.loads((self.dir / "state.json").read_text()))
        if (self.dir / "results.json").exists():
            data = json.loads((self.dir / "results.json").read_text())
            self.meta = data.get("meta", {})
            self.items = [Item(**i) for i in data.get("items", [])]

    def start_meta(self) -> dict[str, Any]:
        """Extra run metadata (versions of the system under test, ...). Override per system."""
        return {}

    @classmethod
    def new(cls, case: str, case_id: str | None, label: str | None = None) -> "Run":
        rid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        run = cls(rid, case, case_id, create=True)
        run.meta = {"run_id": rid, "case": case, "case_id": case_id, "started_at": datetime.now(timezone.utc).isoformat(), **run.start_meta()}
        run.save()
        return run

    @classmethod
    def latest(cls) -> "Run | None":
        if not cls.root.exists():
            return None
        dirs = sorted(p for p in cls.root.iterdir() if p.is_dir() and (p / "state.json").exists())
        if not dirs:
            return None
        st = json.loads((dirs[-1] / "state.json").read_text())
        return cls(dirs[-1].name, st["case"], st.get("case_id"))

    def add(self, item: Item) -> Item:
        # replace an item with the same key (re-runs of a suite within a run)
        self.items = [i for i in self.items if i.key() != item.key()] + [item]
        return item

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        (self.dir / "state.json").write_text(json.dumps(self.state, indent=2, default=str))
        (self.dir / "results.json").write_text(
            json.dumps({"meta": self.meta, "items": [asdict(i) for i in self.items]}, indent=2, default=str)
        )

    def write_raw(self, name: str, obj: Any) -> None:
        p = self.raw / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(obj, (str, bytes)):
            (p.write_bytes if isinstance(obj, bytes) else p.write_text)(obj)
        else:
            p.write_text(json.dumps(obj, indent=2, default=str))


class RunLock:
    """Two bench processes writing one run folder clobber each other's results.json (last writer wins)."""

    def __init__(self, run: "Run"):
        self.path = run.dir / ".lock"

    def __enter__(self):
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip() or 0)
                os.kill(pid, 0)
                raise SystemExit(f"run {self.path.parent.name} is in use by bench pid {pid}; wait for it or pass --label to start a new run")
            except (ProcessLookupError, ValueError, PermissionError):
                pass  # stale lock
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class Timer:
    def __init__(self):
        self.t0 = time.monotonic()

    def s(self) -> float:
        return round(time.monotonic() - self.t0, 3)
