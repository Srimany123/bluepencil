"""Professional review report: one structure (model) rendered as Markdown here and as PDF in the page.

The report says what to change and where, with the exact passage. Scoring internals (profile, strictness,
weights, engine version) stay out of it; the review ID alone reproduces the settings in Blue Pencil.
"""
import os
import time

SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "note": 3}
VLABEL = {"accept": "Accept", "minor": "Minor revision", "major": "Major revision", "reject": "Reject"}
LOC_REQUIRED, LOC_IMPROVE, MAX_IMPROVE, MAX_EDITS = 4, 2, 8, 200


def _where(f):
    parts = []
    if f.get("sec"):
        parts.append(str(f["sec"]))
    if f.get("page"):
        parts.append("p. %s" % f["page"])
    if f.get("para"):
        parts.append("¶ %s" % f["para"])
    return " · ".join(parts) or "Whole document"


def _snippet(f, width=170):
    """(before, highlight, after) around the flagged span, trimmed to about `width` characters."""
    x = (f.get("x") or "").replace("\n", " ").strip()
    if not x:
        return None
    hs, he = f.get("hs") or 0, f.get("he") or 0
    if not he > hs:
        return ["", "", x[:width].rstrip() + ("…" if len(x) > width else "")]
    pre, hit, post = x[:hs], x[hs:he], x[he:]
    room = max(30, (width - len(hit)) // 2)
    if len(pre) > room:
        pre = "…" + pre[-room:].lstrip()
    if len(post) > room:
        post = post[:room].rstrip() + "…"
    return [pre, hit, post]


def _rule_item(rep, brief, nloc):
    r = next((x for x in rep["rules"] if x["id"] == brief["id"]), None)
    live = [f for f in (r or {}).get("findings", []) if not f.get("ok")]
    live.sort(key=lambda f: (SEV_RANK.get(f["sev"], 9), f.get("b") if f.get("b") is not None else 10 ** 9, f.get("as") or 0))
    sev = live[0]["sev"] if live else brief["sev"]
    locs = [{"where": _where(f), "quote": _snippet(f), "issue": f["msg"], "action": f.get("fix") or ""} for f in live[:nloc]]
    total = (r or {}).get("n", len(live))
    return {"rule": brief["id"], "title": brief["name"], "severity": sev, "problem": brief["detail"], "fix": brief["fix"],
            "locations": locs, "more": max(0, total - len(locs)), "count": total}


def in_report(kind, rank, rid, dismissed=(), included=()):
    """Default: every required change, and the top MAX_IMPROVE improvements. The reviewer can switch any of them."""
    if rid in included:
        return True
    if rid in dismissed:
        return False
    return kind == "required" or rank < MAX_IMPROVE


def model(rep, rejected=(), decision=None, dismissed=(), included=(), now=None):
    rejected, dismissed, included = set(rejected or ()), set(dismissed or ()), set(included or ())
    title = os.path.splitext(rep["doc"]["name"])[0] or rep["doc"]["name"]
    dec = decision if decision and decision.get("v") in VLABEL else None
    required = [_rule_item(rep, b, LOC_REQUIRED) for i, b in enumerate(rep["required"]) if in_report("required", i, b["id"], dismissed, included)]
    improve = [_rule_item(rep, b, LOC_IMPROVE) for i, b in enumerate(rep["suggested"]) if in_report("suggested", i, b["id"], dismissed, included)]
    rv = rep.get("revisions") or {}
    edits = []
    for c in rv.get("changes", []):
        if c["id"] in rejected:
            continue
        meta = (rv.get("meta") or {}).get(str(c["b"])) or {}
        t = (rv.get("texts") or {}).get(str(c["b"]), "")
        pre, post = t[max(0, c["a"] - 28):c["a"]], t[c["z"]:c["z"] + 28]
        if c["a"] > 28:
            pre = "…" + pre.split(" ", 1)[-1] if " " in pre else "…" + pre
        if c["z"] + 28 < len(t):
            post = (post.rsplit(" ", 1)[0] if " " in post else post) + "…"
        edits.append({"where": _where(meta), "pre": pre.replace("\n", " "), "old": c["old"], "new": c["new"],
                      "post": post.replace("\n", " "), "why": c["name"]})
    cats = [c for c in rep["categories"] if c["score"] is not None and c["w"] > 0]
    by_score = sorted(cats, key=lambda c: c["score"])
    crit = sum(1 for x in required if x["severity"] == "critical")
    # the headline names the verdict and the size of the job
    if required:
        head = "%d change%s required before this is ready%s." % (
            len(required), "" if len(required) == 1 else "s", ", %d of them critical" % crit if crit else "")
    elif improve:
        head = "No required changes; %d improvement%s recommended." % (len(improve), "" if len(improve) == 1 else "s")
    else:
        head = "Ready as it stands."
    summary = []
    if required:
        summary.append("Fix first: " + "; ".join(x["title"] for x in required[:3]) + ".")
    weak = [c for c in by_score if c["score"] < 80][:2]
    if weak:
        summary.append("Weakest areas: " + ", ".join("%s (%d/100)" % (c["name"], round(c["score"])) for c in weak) + ".")
    strong = [c for c in reversed(by_score) if c["score"] >= 90][:2]
    if strong:
        summary.append("Strongest areas: " + ", ".join("%s (%d/100)" % (c["name"], round(c["score"])) for c in strong) + ".")
    if edits:
        summary.append("%d exact edit%s are ready to apply (listed at the end)." % (len(edits), "" if len(edits) == 1 else "s"))
    nacc = len(rep["settings"].get("accepted") or ())
    if nacc:
        summary.append("%d finding%s checked by the reviewer and accepted as fine; they are not counted." % (nacc, "" if nacc == 1 else "s"))
    st = rep.get("stats") or {}
    facts = [x for x in ["%s words" % format(st["words"], ",") if st.get("words") else None,
                         "%s pages" % st["pages"] if st.get("pages") else None,
                         "%s references" % st["references"] if st.get("references") else None] if x]
    return {
        "title": title, "file": rep["doc"]["name"], "facts": facts,
        "date": time.strftime("%d %B %Y", time.localtime(now or time.time())).lstrip("0"),
        "review_id": rep["fingerprint"],
        "verdict": {"key": dec["v"] if dec else rep["verdict"], "label": VLABEL[dec["v"]] if dec else rep["verdict_label"],
                    "by_reviewer": bool(dec), "note": (dec or {}).get("note") or "",
                    "engine_label": rep["verdict_label"] if dec and dec["v"] != rep["verdict"] else None},
        "score": round(rep["overall"]),
        "headline": head, "summary": summary,
        "required": required, "improve": improve, "more_improve": 0,
        "edits": edits[:MAX_EDITS], "more_edits": max(0, rv.get("total", 0) - len(rv.get("changes", []))) + max(0, len(edits) - MAX_EDITS),
        "scores": [{"area": c["name"], "score": round(c["score"])} for c in cats],
        "strengths": [x["name"] for x in rep["strengths"]][:8],
    }


def _q(quote):
    if not quote:
        return ""
    pre, hit, post = quote
    md = lambda t: t.replace("*", "\\*").replace("_", "\\_")
    return " — “%s%s%s”" % (md(pre), "**%s**" % md(hit) if hit else "", md(post))


def _wrap(t, mark):
    """Mark the words of t, keeping its surrounding spaces outside the marks (Markdown needs that)."""
    core = t.strip()
    if not core:
        return t
    i = t.index(core)
    return "%s%s%s%s%s" % (t[:i], mark, core, mark, t[i + len(core):])


def to_markdown(m):
    L = ["# Manuscript review: %s" % m["title"], ""]
    v = m["verdict"]
    L.append("**%s** · **%d / 100** · %s · Review ID `%s`" % (v["label"], m["score"], m["date"], m["review_id"]))
    if v["by_reviewer"]:
        L.append("")
        L.append("*Reviewer's decision%s.*%s" % (" (automated recommendation: %s)" % v["engine_label"] if v["engine_label"] else "",
                                                  " " + v["note"] if v["note"] else ""))
    L += ["", "> " + m["headline"], ""]
    if m["summary"]:
        L += ["## Summary", ""] + ["- " + s for s in m["summary"]] + [""]

    def section(title, items, more=0):
        if not items:
            return
        L.extend(["## " + title, ""])
        for i, x in enumerate(items, 1):
            L.append("### %d. %s · %s" % (i, x["title"], x["severity"]))
            L.append("")
            L.append("**Problem.** %s  " % x["problem"])
            L.append("**Fix.** %s" % x["fix"])
            L.append("")
            for loc in x["locations"]:
                act = " → %s" % loc["action"] if loc["action"] else ""
                L.append("- **%s**: %s%s%s" % (loc["where"], loc["issue"], _q(loc["quote"]), act))
            if x["more"]:
                L.append("- …and %d more place%s." % (x["more"], "" if x["more"] == 1 else "s"))
            L.append("")
        if more:
            L.extend(["*%d smaller improvements are not listed.*" % more, ""])
    section("Required changes", m["required"])
    section("Recommended improvements", m["improve"], m["more_improve"])
    if m["edits"]:
        L += ["## Ready-to-apply edits (%d)" % (len(m["edits"]) + m["more_edits"]), "", "| Where | Original | Revised | Why |", "|---|---|---|---|"]
        cell = lambda t: (t or "").replace("|", "\\|").replace("*", "\\*").replace("\n", " ")
        ctx = lambda e, mid: "%s%s%s" % (cell(e["pre"]), mid, cell(e["post"]))
        for e in m["edits"]:
            old = _wrap(cell(e["old"]), "~~") if e["old"].strip() else "␣"
            new = _wrap(cell(e["new"]), "**")
            L.append("| %s | %s | %s | %s |" % (cell(e["where"]), ctx(e, old), ctx(e, new), cell(e["why"])))
        if m["more_edits"]:
            L.append("")
            L.append("*…and %d more in the revised document.*" % m["more_edits"])
        L.append("")
    if m["scores"]:
        L += ["## Scores by area", "", "| Area | Score |", "|---|---:|"] + ["| %s | %d |" % (s["area"], s["score"]) for s in m["scores"]] + [""]
    if m["strengths"]:
        L += ["## Checks passed cleanly", "", ", ".join(m["strengths"]) + ".", ""]
    return "\n".join(L).rstrip() + "\n"
