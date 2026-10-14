"""Render cases/kite-launch/docs/ and expected/ from bible.yaml and source/.
Run from the repo root: `uv run python cases/kite-launch/generate.py [--force]`.
Deterministic: identical inputs give identical bytes (see dossier_bench.render)."""

from __future__ import annotations

import argparse
import shutil
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from dossier_bench.render import (
    blocks_to_text,
    canonicalize,
    logo_png,
    parse_md,
    render_csv,
    render_docx,
    render_eml,
    render_eml_thread,
    render_pdf,
    render_scanned_pdf,
    render_sign_photo,
    render_xlsx_spec,
    sha256,
)

HERE = Path(__file__).resolve().parent
SRC = HERE / "source"
DOCS = HERE / "docs"
EXPECTED = HERE / "expected"


def person(bible: dict, ref: str) -> dict:
    return next(p for p in bible["people"] if p["key"] == ref)


def addr(bible: dict, ref: str) -> str:
    p = person(bible, ref)
    return f'"{p["name"]}" <{p["email"]}>'


def org_name(bible: dict, ref: str) -> dict:
    for o in bible["organizations"]:
        if o["key"] == ref:
            return {"key": ref, "name": o["name"], "aliases": o.get("aliases", [])}
    p = person(bible, ref)
    return {"key": ref, "name": p["name"], "aliases": [p["name"].split()[-1]]}


def gbp(n: int) -> str:
    return f"£{n:,}"


def sheets_for(key: str, bible: dict) -> list[dict]:
    if key == "roadmap":
        rows = [["Project Kite — roadmap (updated 10 October 2025)"], ["Feature", "Owner", "Status", "Due", "Note"]]
        rows += [[r["feature"], r["owner"], r["status"], str(r["due"]), r["note"]] for r in bible["roadmap"]]
        risks = [["Risk register"], ["Risk", "Likelihood", "Impact", "Owner", "Mitigation"]]
        risks += [[r["risk"], r["likelihood"], r["impact"], r["owner"], r["mitigation"]] for r in bible["risks"]]
        return [{"title": "Roadmap", "rows": rows, "widths": [34, 18, 12, 12, 50], "bold_rows": [0, 1]}, {"title": "Risks", "rows": risks, "widths": [44, 12, 10, 18, 60], "bold_rows": [0, 1]}]
    if key == "beta-survey":
        rows = [["Kite 3.0 beta survey — 'How likely are you to recommend Kite 3.0?' (0-10) — closed 19 September 2025"], ["Customer", "Score", "Comment"]]
        rows += [[r["customer"], r["score"], r["comment"]] for r in bible["beta_survey"]]
        scores = [r["score"] for r in bible["beta_survey"]]
        prom = sum(1 for s in scores if s >= 9); det = sum(1 for s in scores if s <= 6); n = len(scores)
        nps = int(100 * (prom - det) / n)  # 41 for 7 promoters / 2 detractors of 12, as the status update reports
        rows += [[], ["Responses", n], ["Promoters (9-10)", prom], ["Passives (7-8)", n - prom - det], ["Detractors (0-6)", det], ["NPS", nps]]
        return [{"title": "Survey", "rows": rows, "widths": [26, 8, 60], "bold_rows": [0, 1]}]
    raise SystemExit(f"no sheet spec for {key}")


def write_expected(bible: dict, docs_meta: list[dict], texts: dict[str, str]) -> None:
    EXPECTED.mkdir(exist_ok=True)
    (EXPECTED / "text").mkdir(exist_ok=True)
    for key, text in texts.items():
        (EXPECTED / "text" / f"{key}.txt").write_text(text)
    dump = lambda name, obj: (EXPECTED / name).write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True, width=120))  # noqa: E731
    v = bible["case_version"]
    dump("documents.yaml", {"case_version": v, "documents": docs_meta})
    dump("chronology.yaml", {"case_version": v, "events": [{"key": e["key"], "date": e["date"], "text": e["text"], "docs": e["docs"]} for e in bible["events"]]})
    dump("families.yaml", {"case_version": v, "families": bible["families"], "copies": bible["copies"]})
    dump("graph.yaml", {"case_version": v, "entities": [{"type": e["type"], **org_name(bible, e["ref"])} for e in bible["graph"]["entities"]], "relations": [{"from": org_name(bible, r["from"])["name"], "to": org_name(bible, r["to"])["name"], "kind": r["kind"]} for r in bible["graph"]["relations"]]})
    dump("search.yaml", {"case_version": v, "queries": bible["search"]})


def render_agent_attachments() -> None:
    out_dir = HERE / "agent" / "attachments"
    out_dir.mkdir(parents=True, exist_ok=True)
    for md in sorted((SRC / "agent-attachments").glob("*.md")):
        out = out_dir / f"{md.stem}.pdf"
        render_pdf(parse_md(md.read_text(), SRC), out)
        canonicalize(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    bible = yaml.safe_load((HERE / "bible.yaml").read_text())
    if DOCS.exists() and any(DOCS.iterdir()) and not args.force:
        print("docs/ already populated; pass --force to regenerate (and bump case_version).", file=sys.stderr)
        return 2
    if DOCS.exists():
        shutil.rmtree(DOCS)
    DOCS.mkdir()

    docs_meta: list[dict] = []
    texts: dict[str, str] = {}

    def meta(d: dict, files: list[str], page_count: int | None, **extra) -> dict:
        m = {"key": d["key"], "files": files, "title": d.get("title"), "date": d.get("date"), "date_tolerance": d.get("date_tolerance", "exact"), "types": d.get("types", []), "language": d.get("language"), "page_count": page_count, "must_phrases": d.get("must_phrases", []), "must_facts": d.get("must_facts", []), "family": d.get("family")}
        for k in ("ocr_similarity_min", "injection_marker", "attached_to", "vision_phrases"):
            if d.get(k) is not None:
                m[k] = d[k]
        m.update(extra)
        return m

    for d in bible["documents"]:
        key, fmt = d["key"], d["format"]
        out = DOCS / d["file"]
        if fmt in ("pdf", "docx", "pdf-scan"):
            blocks = parse_md((SRC / d["source"]).read_text(), SRC)
            texts[key] = blocks_to_text(blocks)
            if fmt == "pdf":
                n = render_pdf(blocks, out)
            elif fmt == "docx":
                render_docx(blocks, out)
                n = None
            else:
                tmp = DOCS / (d["file"] + ".text.tmp")
                render_pdf(blocks, tmp, font_size=11.5)
                n = render_scanned_pdf(tmp, out)
                tmp.unlink()
            canonicalize(out)
            docs_meta.append(meta(d, [d["file"]], n, sha256=sha256(out)))
        elif fmt == "csv":
            rows = [["Project Kite — budget tracker (as at 10 October 2025) — owner Tom Berglund"], ["Line", "Planned GBP", "Actual GBP", "Variance GBP", "Note"]]
            rows += [[b["line"], b["planned"], b["actual"], b["actual"] - b["planned"], b["note"]] for b in bible["budget"]]
            tp = sum(b["planned"] for b in bible["budget"]); ta = sum(b["actual"] for b in bible["budget"])
            rows += [["Total", tp, ta, ta - tp, "Spent to date"]]
            texts[key] = render_csv(rows, out)
            docs_meta.append(meta(d, [d["file"]], None, sha256=sha256(out)))
        elif fmt == "txt":
            blocks = parse_md((SRC / d["source"]).read_text(), SRC)
            texts[key] = blocks_to_text(blocks)
            out.write_text(texts[key])
            docs_meta.append(meta(d, [d["file"]], None, sha256=sha256(out)))
        elif fmt == "xlsx-spec":
            texts[key] = render_xlsx_spec(sheets_for(key, bible), out)
            canonicalize(out)
            docs_meta.append(meta(d, [d["file"]], None, sha256=sha256(out)))
        elif fmt == "eml-html":
            spec = yaml.safe_load((SRC / d["source"]).read_text())
            logo = "HOLLOWAY"
            render_eml(out, subject=spec["subject"], from_addr=addr(bible, spec["from"]["ref"]), to=[addr(bible, t["ref"]) for t in spec["to"]], cc=[addr(bible, t["ref"]) for t in spec.get("cc", [])], date_hdr=spec["date"], message_id=spec["message_id"], text=spec["text"], html=spec["html"], inline_images={"hol-logo": logo_png(logo)}, boundary=f"==bench-{key}==")
            texts[key] = spec["text"]
            docs_meta.append(meta(d, [d["file"]], None, sha256=sha256(out), must_facts=spec.get("must_facts", [])))
        elif fmt == "eml-thread":
            emails = yaml.safe_load((SRC / d["source"]).read_text())
            msgs = render_eml_thread(emails["messages"], thread_subject=emails["thread_subject"], out_dir=DOCS, file_prefix=emails["file_prefix"], addr_of=lambda r: addr(bible, r), name_of=lambda r: person(bible, r)["name"])
            for (mkey, path, body), m in zip(msgs, emails["messages"]):
                texts[mkey] = body
                docs_meta.append({"key": mkey, "files": [path.name], "thread": key, "title": d["title"], "date": parsedate_to_datetime(m["date"]).date(), "date_tolerance": "exact", "types": d["types"], "language": d["language"], "page_count": None, "must_phrases": m.get("must_phrases", []), "must_facts": m.get("must_facts", []), "family": {"group": key, "role": "message"}, "attachments": m.get("attachments", []), "sha256": sha256(path)})
        elif fmt == "jpg-photo":
            render_sign_photo(out, [(ln, sz) for ln, sz in d["sign_lines"]], d["stamp"], fmt="JPEG", palette=d.get("palette", "warehouse"), seed=4)
            docs_meta.append(meta(d, [d["file"]], 1, sha256=sha256(out)))
        else:
            raise SystemExit(f"unknown format {fmt} for {key}")

    write_expected(bible, docs_meta, texts)
    render_agent_attachments()
    total_pages = sum(m.get("page_count") or 0 for m in docs_meta)
    manifest = {"case_version": bible["case_version"], "case_name": bible["case"]["name"], "upload": sorted(f for m in docs_meta for f in m["files"]), "pdf_image_pages": total_pages, "filename_to_key": {f: m["key"] for m in docs_meta for f in m["files"]}}
    (EXPECTED / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    print(f"rendered {len(manifest['upload'])} upload files; PDF/image pages: {total_pages}")
    for m in docs_meta:
        print(f"  {m['key']:<20} {m['files'][0]:<44} pages={m.get('page_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
