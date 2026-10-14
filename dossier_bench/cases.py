"""The case corpus: rendered documents (docs/) and ground truth (expected/) per case."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

_PKG = Path(__file__).resolve().parent


def cases_root() -> Path:
    """DOSSIER_CASES, else the copy bundled in the wheel, else the repo checkout."""
    for p in (os.environ.get("DOSSIER_CASES"), _PKG / "_cases", _PKG.parent / "cases"):
        if p and Path(p).is_dir():
            return Path(p)
    raise SystemExit("case corpus not found; set DOSSIER_CASES to a checkout's cases/ directory")


def available() -> list[str]:
    return sorted(p.name for p in cases_root().iterdir() if (p / "expected" / "manifest.yaml").exists())


class Expected:
    def __init__(self, case_dir: Path):
        e = case_dir / "expected"
        self.dir = e
        self.manifest = yaml.safe_load((e / "manifest.yaml").read_text())
        self.documents = yaml.safe_load((e / "documents.yaml").read_text())["documents"]
        self.chronology = yaml.safe_load((e / "chronology.yaml").read_text())["events"]
        fam = yaml.safe_load((e / "families.yaml").read_text())
        self.families, self.copies = fam["families"], fam["copies"]
        self.graph = yaml.safe_load((e / "graph.yaml").read_text())
        self.search = yaml.safe_load((e / "search.yaml").read_text())["queries"]
        self.case_version = self.manifest["case_version"]
        self.by_key = {d["key"]: d for d in self.documents}

    def text(self, key: str) -> str | None:
        p = self.dir / "text" / f"{key}.txt"
        return p.read_text() if p.exists() else None


@dataclass
class Case:
    name: str
    dir: Path

    @classmethod
    def load(cls, name: str) -> "Case":
        p = cases_root() / name
        if not (p / "expected" / "manifest.yaml").exists():
            raise SystemExit(f"unknown case {name!r}; available: {available()}")
        return cls(name, p)

    @cached_property
    def expected(self) -> Expected:
        return Expected(self.dir)

    @property
    def docs_dir(self) -> Path:
        return self.dir / "docs"

    @property
    def files(self) -> list[Path]:
        """What a user would upload, in manifest order. Email attachments arrive inside their .eml."""
        return [self.docs_dir / f for f in self.expected.manifest["upload"]]

    @property
    def filename_to_key(self) -> dict[str, str]:
        return self.expected.manifest["filename_to_key"]

    @property
    def version(self) -> int:
        return int(self.expected.case_version)
