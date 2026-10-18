"""Write OCR and answer-book recordings for the synthetic bundle, from the generator's own truth.

Run after build.py, in the api Poetry environment (it uses core `split_pages`, `embedded_words`,
the provenance atoms and the recording stores, so recordings match what the pipeline computes):

    cd api && poetry run python ../evals/generator/recordings.py ../evals/<pack path> \
        --fixtures tests/fixtures/ai/pipeline

Writes, under OUT_DIR/bundles/synthetic-mrs-a/recordings/ (a FixtureStore root):
  ocr/<file>-p<nnnn>.json         T-07a: one per scanned page, keyed by the single-page PDF's bytes
                                  (`RecordedOcr.request(page, "layout")`); none for born-digital pages
  answer/t07b-<file>-p<nnnn>.json T-07b: keyed by `page_subject` of the page's words (OCR or text layer)
  answer/t07c-<file>-p<nnnn>-<k>.json  T-07c: keyed by `reference_subject(page subject, word_ids)`
  manifest.json                   per page: number, text source, flags; the planted cases; references

Words on a scanned page are the page's text before it was scanned (layout/<file>-source.pdf) plus
the words drawn on its image (layout/<file>.json: handwriting, card labels), in reading order, with
IDs `p1w<n>`. Confidence is made up but fixed (a hash of page and word): lower for poor scans, faxes,
handwriting, pages expected to be hard and the pages nobody can read. Answers are the expected facts,
each tied to the shortest run of words holding its value (or, failing that, its quote).

Planted: one page answers with a value not in its words, then correctly on the retry; one answers
badly every time; one cites crossed-out words. --fixtures copies about twenty pages and their
recordings for the offline tests. Prints counts only, never page text (rule 6).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(API))

import yaml  # noqa: E402

from app.core.ai import provenance  # noqa: E402
from app.core.ai.answers import KIND as ANSWER, page_subject, reference_subject  # noqa: E402
from app.core.ai.ocr import RecordedOcr, word_id  # noqa: E402
from app.core.ai.recordings import FixtureStore  # noqa: E402
from app.core.pdf import embedded_words, split_pages  # noqa: E402

BUNDLE = "bundles/synthetic-mrs-a"
PAGE_W, PAGE_H = 595.2756 / 72, 841.8898 / 72
RETRY_AT, BAD_TWICE_AT = 113, 701                 # planted retry and wrong-twice pages
AVAILABILITY = {"sent_to_gp": "sent_to_clinician"}      # generator label -> T-07b kind
FIELD_ALIASES = {"clinician": "clinician_name"}


def _unit(*parts) -> float:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _box(poly):
    xs, ys = poly[0::2], poly[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def reading_order(words: list[dict]) -> list[dict]:
    """Lines by vertical centre (a word joins a line when its centre is within half a line height),
    then left to right."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (_box(w["polygon"])[1] + _box(w["polygon"])[3]) / 2):
        x0, y0, x1, y1 = _box(w["polygon"])
        yc, h = (y0 + y1) / 2, max(y1 - y0, 0.05)
        if rows:
            r0 = rows[-1]
            ryc = sum((_box(v["polygon"])[1] + _box(v["polygon"])[3]) / 2 for v in r0) / len(r0)
            if abs(yc - ryc) <= h / 2:
                r0.append(w)
                continue
        rows.append([w])
    return [w for r in rows for w in sorted(r, key=lambda w: _box(w["polygon"])[0])]


def confidence(page: int, n: int, w: dict, info: dict) -> float:
    u = _unit(page, n)
    if info["unreadable"]:
        c = 0.15 + 0.3 * u
    elif w["handwritten"]:
        c = (0.3 + 0.35 * u) if info["low_confidence"] else (0.62 + 0.3 * u)
    else:
        c = {"poor": 0.82 + 0.15 * u, "fax": 0.78 + 0.18 * u}.get(info["quality"], 0.95 + 0.049 * u)
    if w["struck"]:
        c -= 0.15
    return round(max(0.05, min(c, 0.999)), 3)


def ocr_words(page: int, source_pdf: bytes, drawn: list[dict], info: dict) -> list[dict]:
    typed = [{"text": w.text, "polygon": list(w.polygon), "handwritten": False, "struck": False}
             for w in embedded_words(source_pdf).words]
    drawn = [{"text": w["text"], "polygon": w["poly"], "handwritten": w["handwritten"], "struck": w["struck"]}
             for w in drawn]
    out = []
    for n, w in enumerate(reading_order(typed + drawn), start=1):
        out.append({"id": word_id(1, n), "text": w["text"], "polygon": [round(v, 4) for v in w["polygon"]],
                    "confidence": confidence(page, n, w, info), "handwritten": w["handwritten"],
                    "struck": w["struck"]})
    return out


def find_run(words: list[tuple[str, str]], text: str) -> list[str] | None:
    """The shortest unbroken run of words whose atoms hold `text`'s atoms in order."""
    needle = provenance.atoms(text)
    if not needle:
        return None
    flat, owner = [], []
    for i, (_, t) in enumerate(words):
        for a in provenance.atoms(t):
            flat.append(a)
            owner.append(i)
    for s in range(len(flat) - len(needle) + 1):
        if flat[s:s + len(needle)] == needle:
            return [words[i][0] for i in range(owner[s], owner[s + len(needle) - 1] + 1)]
    return None


class Stats(dict):
    def bump(self, key):
        self[key] = self.get(key, 0) + 1


def t07b_output(row: dict, words: list[tuple[str, str]], kinds: dict, stats: Stats, source: str | None) -> dict:
    """The ideal T-07b answer; `source` is the specialty pack's source key for the page."""
    facts = []
    for f in row.get("facts", []):
        value, ids = f["value"], find_run(words, f["value"])
        if ids is None:
            value, ids = f["quote"], find_run(words, f["quote"])
            stats.bump("fact_value_from_quote" if ids else "fact_not_on_page")
        if ids is None:
            continue
        allowed = kinds.get(f["kind"], {})
        fields = {FIELD_ALIASES.get(k, k): v for k, v in f.get("fields", {}).items()}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if f.get("event_date") and "date" in allowed:
            fields["date"] = f["event_date"]
        facts.append({"kind": f["kind"], "clinician_role": f.get("fields", {}).get("clinician_role"),
                      "fields": fields, "value": value, "word_ids": ids})
    refs, avail = [], []
    for r in row.get("references_out", []):
        ids = find_run(words, r["quote"])
        if ids:
            refs.append({"described_document": r["described_document"], "date": r["date"], "word_ids": ids})
        else:
            stats.bump("reference_not_on_page")
    for a in row.get("availability", []):
        ids = find_run(words, a["quote"])
        if ids:
            avail.append({"kind": AVAILABILITY.get(a["kind"], a["kind"]), "date": a["date"], "word_ids": ids})
        else:
            stats.bump("availability_not_on_page")
    return {"source_type": source, "printed_dates": list(row.get("printed_dates", [])),
            "facts": facts, "references_out": refs, "availability_evidence": avail,
            "actions_requested_of_clinician": []}


def response(output: dict, n_words: int) -> dict:
    return {"output": output, "input_tokens": 200 + 3 * n_words,
            "output_tokens": len(json.dumps(output)) // 4 + 1}


def spoil(output: dict, words: dict[str, str]) -> dict | None:
    """The same answer with one number changed so it is no longer in the words it cites."""
    for i, f in enumerate(output["facts"]):
        for pos, ch in enumerate(f["value"]):
            if ch.isdigit():
                bad = f["value"][:pos] + str((int(ch) + 3) % 10) + f["value"][pos + 1:]
                if not provenance.supported(bad, f["word_ids"], words):
                    facts = [dict(x) for x in output["facts"]]
                    facts[i]["value"] = bad
                    return {**output, "facts": facts}
    return None


def struck_run(words: list[dict]) -> list[str]:
    best, cur = [], []
    for w in words:
        cur = cur + [w["id"]] if w["struck"] else []
        best = cur if len(cur) > len(best) else best
    return best


def matched_page(ref: dict, rows: list[dict], missing: list[dict]) -> int | None:
    if any(m["page"] == ref["page"] and m["described_document"] == ref["described_document"] for m in missing):
        return None
    keys = [k for k in (w.strip("(),").lower() for w in ref["described_document"].split())
            if len(k) > 3 and k not in {"report", "letter", "result", "results"}]
    best = None
    for row in rows:
        if row["page"] == ref["page"] or not any(d >= ref["date"] for d in row.get("printed_dates", [])):
            continue
        text = " ".join([row["doc_id"]] + [f["value"] + " " + json.dumps(f.get("fields", {})) for f in row["facts"]])
        score = sum(k in text.lower() for k in keys)
        if score and (best is None or score > best[0]):
            best = (score, row["page"])
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--fixtures", help="copy a small subset of pages and their recordings here")
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    bundle, exp = out / BUNDLE, out / "expected"
    rec_root = bundle / "recordings"
    shutil.rmtree(rec_root, ignore_errors=True)
    store = FixtureStore(rec_root)
    specialty = yaml.safe_load((API.parent / "packs/specialty/general-practice/facts.yaml").read_text())
    kinds = {k["key"]: k.get("fields", {}) for k in specialty["fact_kinds"]}

    rows = json.loads((exp / "pages.json").read_text())
    unreadable = set(json.loads((exp / "unreadable.json").read_text()))
    references = json.loads((exp / "references.json").read_text())
    missing = json.loads((exp / "missing.json").read_text())
    appended = json.loads((exp / "appended.json").read_text())
    source_keys = {r["page"]: r["source_type"] for r in json.loads((exp / "sources.json").read_text())}
    stats = Stats()
    manifest = {"pages": [], "planted": {}, "references": []}
    page_data: dict[int, dict] = {}

    for name, expected in (("records", rows), ("appended-records", appended)):
        pages = split_pages((bundle / f"{name}.pdf").read_bytes())
        sources = split_pages((bundle / "layout" / f"{name}-source.pdf").read_bytes())
        drawn = json.loads((bundle / "layout" / f"{name}.json").read_text())
        by_number = {r["page"]: r for r in expected}
        first = 1 if name == "records" else len(rows) + 1
        for i, pdf in enumerate(pages):
            number = first + i
            row = by_number.get(number, {"page": number, "facts": []})
            text_layer = embedded_words(pdf).words
            source = row.get("text_source") or ("embedded" if text_layer and not drawn[i]["words"] else "ocr")
            info = {"page": number, "file": name, "index": i, "text_source": source,
                    "typed_header_scanned_body": bool(row.get("typed_header_scanned_body")),
                    "unreadable": number in unreadable, "low_confidence": bool(row.get("expected_low_confidence")),
                    "quality": row.get("quality", "scan"), "duplicate_of_page": row.get("duplicate_of_page")}
            if source == "ocr":
                words = ocr_words(number, sources[i], drawn[i]["words"], info)
                store.record(RecordedOcr.kind, RecordedOcr.request(pdf, "layout"),
                             {"pages": [{"number": 1, "width": PAGE_W, "height": PAGE_H, "words": words}]},
                             name=f"{name}-p{number:04d}")
                stats.bump("ocr_pages")
            else:
                words = [{"id": w.id, "text": w.text, "struck": False} for w in text_layer]
            pairs = [(w["id"], w["text"]) for w in words]
            subject = page_subject(pairs)
            output = t07b_output(row, pairs, kinds, stats, source_keys.get(number) if name == "records" else None)
            stats["facts"] = stats.get("facts", 0) + len(output["facts"])
            page_data[number] = {"info": info, "pdf": pdf, "words": words, "pairs": pairs, "subject": subject,
                                 "output": output}
            manifest["pages"].append({k: info[k] for k in ("page", "file", "index", "text_source",
                                                           "typed_header_scanned_body", "unreadable",
                                                           "low_confidence")})

    # planted cases: a bad value then a good retry, a bad value every time, crossed-out words cited
    plain = [n for n, d in page_data.items() if d["info"]["text_source"] == "embedded" and d["info"]["file"] ==
             "records" and not d["output"]["references_out"] and not d["info"]["duplicate_of_page"]]
    spoiled = [(n, s) for n in plain if (s := spoil(page_data[n]["output"], dict(page_data[n]["pairs"])))]
    # pinned like build.py's TARGETS, so regenerating the bundle never moves them: each is the first
    # spoilable page from its target (slice A's tests name these pages)
    (retry, bad1), (twice, bad2) = (next((n, s) for n, s in spoiled if n >= at) for at in (RETRY_AT, BAD_TWICE_AT))
    struck = next(n for n, d in page_data.items() if d["info"]["text_source"] == "ocr" and
                  not d["info"]["unreadable"] and len(struck_run(d["words"])) >= 2)
    ids = struck_run(page_data[struck]["words"])
    text = " ".join(t for i, t in page_data[struck]["pairs"] if i in ids)
    page_data[struck]["output"]["facts"].append({"kind": "consultation", "clinician_role": None, "fields": {},
                                                 "value": text, "word_ids": ids})
    manifest["planted"] = {"retry": retry, "bad_twice": twice, "struck": struck}
    for n, d in page_data.items():
        good = response(d["output"], len(d["pairs"]))
        answers = {retry: [response(bad1, len(d["pairs"])), good],
                   twice: [response(bad2, len(d["pairs"]))]}.get(n, [good])
        store.record(ANSWER, {"task_id": "T-07b", "subject": d["subject"]}, {"responses": answers},
                     name=f"t07b-{d['info']['file']}-p{n:04d}")

    # T-07c: each reference found in another page, or missing
    count: dict[int, int] = {}
    for ref in references:
        d = page_data[ref["page"]]
        ids = find_run(d["pairs"], ref["quote"])
        if ids is None:
            stats.bump("reference_not_on_page")
            continue
        count[ref["page"]] = count.get(ref["page"], 0) + 1
        k = count[ref["page"]]
        match = matched_page(ref, rows, missing)
        out_ref = {"reference_id": f"p{ref['page']}-r{k}", "matched_page": match,
                   "confidence": 0.9 if match else 0.8}
        store.record(ANSWER, {"task_id": "T-07c", "subject": reference_subject(d["subject"], ids)},
                     {"responses": [response(out_ref, len(ids))]}, name=f"t07c-records-p{ref['page']:04d}-{k}")
        manifest["references"].append({"page": ref["page"], "word_ids": ids, "matched_page": match})
    (rec_root / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print("recordings", json.dumps(stats, sort_keys=True), "planted", json.dumps(manifest["planted"]),
          "references", json.dumps([(r["page"], r["matched_page"]) for r in manifest["references"]]))

    if args.fixtures:
        write_fixtures(Path(args.fixtures).resolve(), page_data, manifest, rec_root, rows)


def write_fixtures(root: Path, page_data: dict, manifest: dict, rec_root: Path, rows: list[dict]):
    by = {r["page"]: r for r in rows}
    planted = manifest["planted"]
    want = [1, 93, *{r["page"] for r in manifest["references"]}, planted["retry"], planted["bad_twice"],
            planted["struck"]]
    want.append(next(n for n, d in page_data.items() if d["info"]["unreadable"]))
    for pick in (lambda r: r["doc_id"].startswith("lg-"), lambda r: r["quality"] == "fax",
                 lambda r: r["quality"] == "poor", lambda r: r["expected_low_confidence"] and not r["struck_out"]):
        want.append(next(r["page"] for r in rows if pick(r)))
    dup = next(r for r in rows if r["duplicate_of_page"] and by[r["duplicate_of_page"]]["text_source"] == "embedded")
    want += [dup["page"], dup["duplicate_of_page"]]
    want += [r["page"] for r in rows if r["text_source"] == "embedded" and r["facts"]][::25]
    chosen: list[int] = []
    for n in want:
        if n not in chosen and len(chosen) < 20:
            chosen.append(n)
    shutil.rmtree(root, ignore_errors=True)
    (root / "pages").mkdir(parents=True)
    keep = set(chosen)
    for p in sorted(rec_root.glob("*/*.json")):
        stem = p.stem
        if any(f"-p{n:04d}" in stem and stem.startswith(("records", "t07b-records", "t07c-records")) for n in keep):
            (root / p.parent.name).mkdir(exist_ok=True)
            shutil.copy(p, root / p.parent.name / p.name)
    pages = []
    for entry in manifest["pages"]:
        if entry["page"] in keep and entry["file"] == "records":
            f = f"pages/p{entry['page']:04d}.pdf"
            (root / f).write_bytes(page_data[entry["page"]]["pdf"])
            pages.append({**{k: v for k, v in entry.items() if k not in ("file", "index")}, "file": f})
    sub = {"pages": pages, "planted": planted,
           "references": [r for r in manifest["references"] if r["page"] in keep]}
    (root / "manifest.json").write_text(json.dumps(sub, indent=1) + "\n")
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    print("fixtures", len(pages), "pages", size, "bytes", json.dumps(sorted(keep)))


if __name__ == "__main__":
    main()
