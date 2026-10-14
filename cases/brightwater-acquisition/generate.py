"""Render cases/brightwater-acquisition/docs/ and expected/ from bible.yaml and source/.
Run from the repo root: `uv run python cases/brightwater-acquisition/generate.py [--force]`.
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
    d = bible["deal"]
    if key == "data-room-index":
        rows = [["Project Larch — data room index (as at 6 March 2025)"], ["Ref", "Folder", "Document", "Date uploaded"]]
        rows += [[r["ref"], r["folder"], r["title"], str(r["date"])] for r in bible["data_room_index"]]
        qa = [["Q&A log"], ["No", "Asked", "Question", "Answer", "Answered"]]
        qa += [[q["q"], str(q["date"]), q["question"], q["answer"], str(q["answered"])] for q in bible["qa_log"]]
        return [{"title": "Index", "rows": rows, "widths": [8, 28, 60, 16], "bold_rows": [0, 1]}, {"title": "Q&A log", "rows": qa, "widths": [6, 12, 70, 80, 12], "bold_rows": [0, 1]}]
    if key == "oss-audit":
        rows = [["Open-source licence audit — Brightwater platform — Lattice Compliance Ltd — completed 22 August 2025"], ["Component", "Version", "Licence", "Usage", "Exposure", "Remediation"]]
        rows += [[c["component"], c["version"], c["licence"], c["usage"], c["exposure"], c["remediation"]] for c in bible["oss_components"]]
        agpl = [c["component"] for c in bible["oss_components"] if c["licence"].startswith("AGPL")]
        rows += [[], ["Copyleft (AGPL-3.0) components in the hosted service", len(agpl), ", ".join(agpl)]]
        return [{"title": "Components", "rows": rows, "widths": [24, 10, 22, 40, 36, 60], "bold_rows": [0, 1]}]
    if key == "arr-statement":
        rows = [["Draft ARR statement at 30 September 2025 — Schedule 7 (earn-out) — prepared by Castellane Holdings Ltd, 3 October 2025"], ["Customer", "ARR at 31 Dec 2024 (locked box)", "ARR at 30 Sep 2025 (Castellane draft)", "ARR at 30 Sep 2025 (Sellers' alternative)", "Note"]]
        rows += [[c["customer"], c["dec24"], c["sep25"], c["sellers"], c["note"]] for c in bible["arr_customers"]]
        t = lambda k: sum(c[k] for c in bible["arr_customers"])  # noqa: E731
        rows += [["Total", t("dec24"), t("sep25"), t("sellers"), ""], [], ["Earn-out floor (Schedule 7)", d["earnout_arr_floor_gbp"]], ["Earn-out target", d["earnout_arr_target_gbp"]], ["Earn-out on Castellane draft", 0], ["Earn-out on Sellers' alternative", d["earnout_max_gbp"]]]
        return [{"title": "ARR", "rows": rows, "widths": [48, 24, 30, 30, 70], "bold_rows": [0, 1]}]
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
            rows = [["Brightwater Analytics Ltd — capitalisation table at 31 December 2024"], ["Holder", "Class", "Shares / options", "Fully diluted %", "Pre-completion"]]
            rows += [[r["holder"], r["class"], r["shares"], r["pct"], "yes" if r["pre"] else "no"] for r in bible["cap_table"]]
            rows += [["Total issued ordinary shares", "", sum(r["shares"] for r in bible["cap_table"] if r["class"] == "Ordinary"), 100.0, ""], ["Note", "All ordinary shares were transferred to Castellane Holdings Ltd at completion on 30 April 2025; the EMI options were cashed out under the drag-along.", "", "", ""]]
            texts[key] = render_csv(rows, out)
            docs_meta.append(meta(d, [d["file"]], None, sha256=sha256(out)))
        elif fmt == "xlsx-spec":
            texts[key] = render_xlsx_spec(sheets_for(key, bible), out)
            canonicalize(out)
            docs_meta.append(meta(d, [d["file"]], None, sha256=sha256(out)))
        elif fmt == "eml-html":
            spec = yaml.safe_load((SRC / d["source"]).read_text())
            logo = "NORTHGATE" if "northgate" in d["source"] else "LOGO"
            render_eml(out, subject=spec["subject"], from_addr=addr(bible, spec["from"]["ref"]), to=[addr(bible, t["ref"]) for t in spec["to"]], cc=[addr(bible, t["ref"]) for t in spec.get("cc", [])], date_hdr=spec["date"], message_id=spec["message_id"], text=spec["text"], html=spec["html"], inline_images={"ngr-logo": logo_png(logo)}, boundary=f"==bench-{key}==")
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
