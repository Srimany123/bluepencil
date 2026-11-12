"""Browser bridge for Blue Pencil: runs the unchanged Pi engine inside Pyodide.

The Pi build shells out to pdftotext and antiword. In the browser, PDF text comes from pdf.js
(passed in as `pdf_text`), and old binary .doc files are declined with a clear message.
"""
import json
import time
import types
from collections import OrderedDict

import extract
import engine
import rules
import analysis
import report


class _NoProc(Exception):
    pass


_PDF = {"text": ""}


def _run(cmd, capture_output=True, timeout=None):
    if cmd and cmd[0] == "pdftotext":
        return types.SimpleNamespace(returncode=0, stdout=_PDF["text"].encode("utf8"), stderr=b"")
    raise FileNotFoundError(cmd[0] if cmd else "?")


extract.subprocess = types.SimpleNamespace(run=_run, TimeoutExpired=_NoProc)


def _no_doc(path):
    raise extract.ExtractError("Old Word 97-2003 (.doc) files can't be read in the browser version. "
                               "Open it in Word, LibreOffice or Google Docs, save it as .docx, and upload that.")


extract.from_doc = _no_doc

_cache = OrderedDict()   # doc_id -> engine.Doc


def _err(e):
    return json.dumps({"error": str(e)})


def extract_file(path, name, pdf_text=None):
    """-> JSON {blocks, meta} or {error}."""
    _PDF["text"] = pdf_text or ""
    try:
        t0 = time.time()
        blocks, meta = extract.extract(path, name)
        meta["extract_s"] = round(time.time() - t0, 2)
    except extract.ExtractError as e:
        return _err(e)
    except Exception as e:   # unexpected parser failure
        return _err("Could not read this file: %s" % e)
    return json.dumps({"blocks": blocks, "meta": meta}, ensure_ascii=False)


def load(doc_id, rec_json):
    if doc_id in _cache:
        _cache.move_to_end(doc_id)
        return _cache[doc_id]
    rec = json.loads(rec_json)
    d = engine.Doc(rec["blocks"], rec["meta"], rec["name"], rec["sha"])
    _cache[doc_id] = d
    while len(_cache) > 4:
        _cache.popitem(last=False)
    return d


def cached(doc_id):
    return doc_id in _cache


def summary(doc_id, rec_json, with_warnings=False):
    d = load(doc_id, rec_json)
    warnings = []
    if with_warnings:
        if d.meta.get("ext") == ".pdf":
            warnings.append("PDF text is reconstructed from the page layout; double spaces aren't checked and headings are inferred.")
        if d.meta.get("approximate"):
            warnings.append("LaTeX is converted approximately (macros removed, \\cite keys counted as citations).")
        if d.meta.get("ext") in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv"):
            warnings.append("Spreadsheet: cells with 12+ words are reviewed as paragraphs; shorter cells are treated as table rows.")
        if d.words < 150:
            warnings.append("Only %d words of body text were found, so many rules won't apply." % d.words)
    return json.dumps({
        "doc_id": doc_id, "name": d.name, "format": d.meta.get("format"), "pages": d.meta.get("pages"),
        "words": d.words, "sentences": len(d.sents), "paragraphs": len(d.prose), "headings": len(d.headings),
        "references": len(d.refs), "cite_style": d.cite_style, "present": sorted(d.present),
        "outline": engine.outline(d), "suggested_profile": engine.suggest_profile(d), "warnings": warnings,
        "tracked_changes": d.meta.get("tracked_changes"), "comments": d.meta.get("comments"),
    }, ensure_ascii=False)


def review(doc_id, rec_json, settings_json, budget_s=1.5):
    """Runs for at most ~budget_s, like the Pi's 20 s request window, so the page can show progress."""
    d = load(doc_id, rec_json)
    t0 = time.time()
    try:
        rep = engine.review(d, json.loads(settings_json or "{}"), deadline=time.time() + budget_s)
    except engine.Pending as p:
        return json.dumps({"pending": True, "done": p.done, "total": p.total})
    rep["doc"]["doc_id"] = doc_id
    rep["elapsed_s"] = round(time.time() - t0, 2)
    rep["_words"] = d.words   # for the history list; removed before the report is stored
    return json.dumps(rep, ensure_ascii=False)


def _model(rep_json, manual_json):
    man = json.loads(manual_json or "null") or {}
    return report.model(json.loads(rep_json), man.get("rejected"), man.get("decision"), man.get("dismissed"), man.get("included"))


def report_model(rep_json, manual_json="null"):
    return json.dumps(_model(rep_json, manual_json), ensure_ascii=False)


def markdown(rep_json, manual_json="null"):
    return report.to_markdown(_model(rep_json, manual_json))


def revised(doc_id, rec_json, settings_json, reject_json):
    d = load(doc_id, rec_json)
    changes = engine.last_changes(d, json.loads(settings_json))
    return analysis.revised_document(d, changes, [str(x) for x in json.loads(reject_json)])


def merge_settings(settings_json):
    return json.dumps(rules.merge_settings(json.loads(settings_json or "{}")))


def warm():
    engine.dictionaries()
    return "ok"
