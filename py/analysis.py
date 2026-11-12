"""Document-level analysis: paragraph metrics, thought flow, heat map, rewrites, readability indices.

Everything here is deterministic arithmetic on the text. "Interest" is a proxy, not a
judgement of taste: it rises with new ideas introduced, sentence-length variety,
specific detail (numbers, names, citations, dialogue) and active voice, and falls
when a paragraph is harder to read than the document's own norm.
"""
import math
import re
from collections import Counter, defaultdict

import engine as E
import lexicon as LX
from engine import F, M, WORD_RE, fmt, per1k

SEVW = {"critical": 5.0, "major": 3.0, "minor": 1.0, "note": 0.3}
LINK_START = re.compile(
    r"^[“\"'‘(]?(This|That|These|Those|It|They|He|She|We|I|Its|Their|His|Her|Our|Such|Both|Each|Here|There|Then|However|Moreover|"
    r"Furthermore|Additionally|In addition|Also|Thus|Therefore|Hence|Consequently|Nevertheless|Nonetheless|Similarly|Likewise|"
    r"In contrast|By contrast|On the other hand|For example|For instance|First|Second|Third|Finally|Next|Later|Meanwhile|Afterwards|"
    r"Instead|Indeed|Overall|In summary|In conclusion|As a result|Accordingly|Yet|But|And|So|Again|Another|The same|Despite|"
    r"Although|While|Building on|Following|Given|To|In turn|Taken together|Together|Beyond|Unlike|Like)\b")


def content(words):
    out = []
    for w in words:
        lw = w.lower()
        if len(lw) < 4 or lw in LX.STOP or not lw.isalpha():
            continue
        if lw.endswith("ies") and len(lw) > 5:
            lw = lw[:-3] + "y"
        elif lw.endswith("s") and not lw.endswith("ss") and len(lw) > 4:
            lw = lw[:-1]
        out.append(lw)
    return out


def _z(vals):
    xs = [v for v in vals if v is not None]
    if not xs:
        return [0.0] * len(vals)
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
    return [0.0 if v is None else (v - m) / sd for v in vals]


def flow(doc):
    """Per-paragraph metrics, cached on the Doc."""
    if getattr(doc, "_flow", None) is not None:
        return doc._flow
    rows, seen, prev, prev_sec = [], set(), None, object()
    for n, b in enumerate(doc.prose):
        ss = [doc.sents[k] for k in doc.sents_by_block[b]]
        words = [w for x in ss for w in x["w"]]
        cs = set(content(words))
        nov = len(cs - seen) / len(cs) if cs else 0.0
        seen |= cs
        coh = (len(cs & prev) / len(cs | prev)) if (prev and cs) else None
        t = doc.blocks[b]["t"]
        lens = [x["n"] for x in ss]
        asl = sum(lens) / len(lens) if lens else 0.0
        sd = math.sqrt(sum((l - asl) ** 2 for l in lens) / len(lens)) if lens else 0.0
        syl = sum(E.syllables(w) for w in words)
        grade = E.fk_grade(len(words), len(ss), syl) if words and ss else 0.0
        nums = len(re.findall(r"(?<![\w.])\d[\d,.]*\b", t))
        props = sum(1 for x in ss for j, w in enumerate(x["w"]) if j > 0 and w[0].isupper())
        cites = len(doc.cite_spans.get(b, []))
        dlg = sum(len(q) for q in re.findall(r"“[^”]*”|\"[^\"]*\"", t)) / len(t) if t else 0.0
        wc = len(words)
        spec = 100.0 * (nums + props + cites) / wc if wc else 0.0
        pas = sum(1 for x in ss if E.PASSIVE_RE.search(x["t"])) / len(ss) if ss else 0.0
        rows.append({"b": b, "n": n, "w": wc, "s": len(ss), "asl": round(asl, 1), "sd": round(sd, 1), "grade": round(grade, 1),
                     "nov": round(nov, 3), "coh": None if coh is None else round(coh, 3), "same": doc.sec_of[b] == prev_sec,
                     "spec": round(spec, 2), "dlg": round(dlg, 3), "pas": round(pas, 3), "link": bool(LINK_START.match(t)),
                     "cs": cs, "first": set(content(ss[0]["w"])) if ss else set(),
                     "rest": set(content([w for x in ss[1:] for w in x["w"]]))})
        prev, prev_sec = cs, doc.sec_of[b]
    # interest proxy
    elig = [r for r in rows if r["w"] >= 15]
    if elig:
        grades = sorted(r["grade"] for r in elig)
        med = grades[len(grades) // 2]
        zn = _z([r["nov"] for r in elig])
        zs = _z([r["sd"] for r in elig])
        zp = _z([r["spec"] + 40 * r["dlg"] for r in elig])
        zc = _z([-max(0.0, r["grade"] - med) for r in elig])
        za = _z([1 - r["pas"] for r in elig])
        raw = [0.35 * a + 0.2 * b_ + 0.2 * c + 0.15 * d + 0.1 * e for a, b_, c, d, e in zip(zn, zs, zp, zc, za)]
        order = sorted(range(len(raw)), key=lambda i: (raw[i], elig[i]["n"]))
        for rank, i in enumerate(order):
            elig[i]["int"] = round(100.0 * rank / max(1, len(order) - 1), 1)
    for r in rows:
        r.setdefault("int", None)
    doc._flow = rows
    return rows


# ------------------------------------------------------------------ flow rules
def m_flw01(doc, rc, s):
    rows = flow(doc)
    pairs = [r for r in rows if r["coh"] is not None and r["same"] and r["w"] >= 20]
    if len(pairs) < 4:
        return None
    lim = rc["params"]["min_overlap"] / 100.0
    bad = [r for r in pairs if r["coh"] < lim and not r["link"]]
    fs = [F(doc, "Shares %.0f%% of its key words with the paragraph before and has no linking word." % (100 * r["coh"]), r["b"], 0, 0,
            fix="Open with a sentence that connects to the previous point.") for r in bad]
    return M(100.0 * len(bad) / len(pairs), fs, "%d of %d paragraph transitions look abrupt." % (len(bad), len(pairs)))


def m_flw02(doc, rc, s):
    tot = bad = 0
    fs = []
    for b in doc.prose:
        ks = doc.sents_by_block[b]
        for a, z in zip(ks, ks[1:]):
            A, Z = set(content(doc.sents[a]["w"])), set(content(doc.sents[z]["w"]))
            if len(A) < 2 or len(Z) < 2:
                continue
            tot += 1
            if not (A & Z) and not LINK_START.match(doc.sents[z]["t"]):
                bad += 1
                fs.append(F(doc, "Shares no key word or link with the previous sentence.", sent=z,
                            fix="Start with something the reader already knows (old → new information)."))
    if tot < 10:
        return None
    return M(100.0 * bad / tot, fs, "%d of %d sentence-to-sentence steps have no visible link." % (bad, tot))


def m_flw03(doc, rc, s):
    rows = [r for r in flow(doc) if r["s"] >= 4 and r["rest"]]
    if len(rows) < 3:
        return None
    bad = []
    for r in rows:
        share = len(r["first"] & r["rest"]) / len(r["first"]) if r["first"] else 0
        if share < 0.15:
            bad.append(r)
    fs = [F(doc, "The first sentence shares little with the rest of the paragraph.", r["b"], 0, 0,
            fix="Open with a topic sentence that states what the paragraph is about.") for r in bad]
    return M(100.0 * len(bad) / len(rows), fs, "%d of %d long paragraphs lack a clear topic sentence." % (len(bad), len(rows)))


def m_flw04(doc, rc, s):
    rows = [r for r in flow(doc) if r["w"] >= 40 and len(r["cs"]) >= 8]
    if len(rows) < 4:
        return None
    df = Counter(w for r in rows for w in r["cs"])
    idx = defaultdict(list)
    for i, r in enumerate(rows):
        for w in r["cs"]:
            if df[w] <= 6:
                idx[w].append(i)
    fs, seen = [], set()
    for i, r in enumerate(rows):
        cand = Counter(j for w in r["cs"] if df[w] <= 6 for j in idx[w] if j < i - 1)
        for j, _ in sorted(cand.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
            o = rows[j]
            jac = len(r["cs"] & o["cs"]) / len(r["cs"] | o["cs"])
            if jac >= rc["params"]["min_similarity"] / 100.0 and (j, i) not in seen:
                seen.add((j, i))
                fs.append(F(doc, "Covers much the same ground as ¶ %s (%.0f%% shared vocabulary)." % (doc.ordinal[o["b"]], 100 * jac), r["b"], 0, 0,
                            fix="Merge with the earlier paragraph or make the new angle explicit."))
                break
    return M(len(fs), fs, "%d paragraph(s) circle back to earlier material." % len(fs))


def m_flw05(doc, rc, s):
    rows = [r for r in flow(doc) if r["int"] is not None]
    if len(rows) < 8:
        return None
    lim, run = rc["params"]["min_run"], []
    fs = []
    def close():
        if len(run) >= lim:
            fs.append(F(doc, "%d paragraphs in a row in the bottom quarter for interest (¶ %s–%s)." % (len(run), doc.ordinal[run[0]["b"]], doc.ordinal[run[-1]["b"]]),
                        run[0]["b"], 0, 0, fix="Add an example, a number, a question or a change of pace in this stretch."))
    for r in rows:
        if r["int"] < 25:
            run.append(r)
        else:
            close()
            run = []
    close()
    return M(len(fs), fs, "%d lull(s) of %d+ low-interest paragraphs." % (len(fs), lim))


MEASURES = {"FLW-01": m_flw01, "FLW-02": m_flw02, "FLW-03": m_flw03, "FLW-04": m_flw04, "FLW-05": m_flw05}


# ------------------------------------------------------------------ heat map & sequence
def heat(doc, rules_full, s):
    """rules_full: list of (rule_dict, full findings list) for scored rules."""
    rows = flow(doc)
    per_b = defaultdict(lambda: {"pen": 0.0, "n": 0, "cats": Counter()})
    for r, fs in rules_full:
        for f in fs:
            b = f.get("b")
            if b is None:
                continue
            d = per_b[b]
            d["pen"] += SEVW.get(f["sev"], 1.0)
            d["n"] += 1
            d["cats"][r["cat"]] += 1
    paras = []
    for r in rows:
        d = per_b.get(r["b"], {"pen": 0.0, "n": 0, "cats": Counter()})
        dens = 100.0 * d["pen"] / max(r["w"], 20)
        q = 100.0 * math.exp(-dens / 8.0)
        paras.append({"b": r["b"], "para": doc.ordinal[r["b"]], "sec": doc.sec_of[r["b"]], "page": doc.blocks[r["b"]].get("p"),
                      "w": r["w"], "q": round(q, 1), "n": d["n"], "int": r["int"], "coh": r["coh"], "nov": r["nov"],
                      "grade": r["grade"], "asl": r["asl"], "cats": dict(d["cats"]),
                      "x": doc.blocks[r["b"]]["t"][:160]})
    # bin long documents so the browser draws at most ~600 cells
    binned = paras
    if len(paras) > 600:
        k = math.ceil(len(paras) / 600)
        binned = []
        for i in range(0, len(paras), k):
            g = paras[i:i + k]
            w = sum(p["w"] for p in g) or 1
            def avg(key):
                vs = [(p[key], p["w"]) for p in g if p[key] is not None]
                return round(sum(v * ww for v, ww in vs) / sum(ww for _, ww in vs), 2) if vs else None
            cats = Counter()
            for p in g:
                cats.update(p["cats"])
            binned.append({"b": g[0]["b"], "para": g[0]["para"], "para_end": g[-1]["para"], "sec": g[0]["sec"], "page": g[0]["page"],
                           "w": w, "q": avg("q"), "n": sum(p["n"] for p in g), "int": avg("int"), "coh": avg("coh"),
                           "nov": avg("nov"), "grade": avg("grade"), "asl": avg("asl"), "cats": dict(cats), "x": g[0]["x"]})
    # section x category matrix
    secs = []
    for x in doc.sections:
        if x["level"] > 2 or x["total"] == 0:
            continue
        secs.append(x)
    secs = secs[:120]
    matrix = []
    bounds = []
    for j, x in enumerate(secs):
        end = doc.n
        for y in doc.sections:
            if y["i"] > x["i"] and y["level"] <= x["level"]:
                end = y["i"]
                break
        bounds.append((x, end))
    for x, end in bounds:
        cats, pen, n = Counter(), 0.0, 0
        for b in range(x["i"] + 1, end):
            d = per_b.get(b)
            if d:
                cats.update(d["cats"])
                pen += d["pen"]
                n += d["n"]
        ints = [p["int"] for p in paras if x["i"] < p["b"] < end and p["int"] is not None]
        cohs = [p["coh"] for p in paras if x["i"] < p["b"] < end and p["coh"] is not None]
        w = max(x["total"], 1)
        matrix.append({"title": x["title"], "level": x["level"], "b": x["i"], "w": x["total"], "n": n,
                       "q": round(100.0 * math.exp(-(100.0 * pen / max(w, 20)) / 8.0), 1),
                       "cats": {c: round(1000.0 * v / w, 2) for c, v in cats.items()},
                       "int": round(sum(ints) / len(ints), 1) if ints else None,
                       "coh": round(sum(cohs) / len(cohs), 3) if cohs else None})
    # links between consecutive sections (shared vocabulary) for the sequence view
    seq = []
    vocab = []
    for x, end in bounds:
        v = set()
        for r in rows:
            if x["i"] < r["b"] < end:
                v |= r["cs"]
        vocab.append(v)
    for i in range(1, len(vocab)):
        a, z = vocab[i - 1], vocab[i]
        seq.append(round(len(a & z) / len(a | z), 3) if a and z else None)
    peaks = sorted([p for p in paras if p["int"] is not None], key=lambda p: (-p["int"], p["para"]))[:5]
    lulls = sorted([p for p in paras if p["int"] is not None and p["w"] >= 30], key=lambda p: (p["int"], p["para"]))[:5]
    best = sorted(paras, key=lambda p: (-p["q"], -p["w"], p["para"]))[:5]
    worst = sorted(paras, key=lambda p: (p["q"], p["para"]))[:5]
    return {"paras": binned, "binned": len(binned) != len(paras), "sections": matrix, "seq": seq,
            "peaks": peaks, "lulls": lulls, "best": best, "worst": worst}


# ------------------------------------------------------------------ rewrites
DELETABLE = {"very", "really", "extremely", "truly", "basically", "actually", "literally", "totally", "absolutely",
             "definitely", "incredibly"}
CONTR = {"can't": "cannot", "won't": "will not", "shan't": "shall not", "it's": "it is", "that's": "that is", "there's": "there is",
         "here's": "here is", "what's": "what is", "who's": "who is", "let's": "let us", "he's": "he is", "she's": "she is",
         "where's": "where is", "i'm": "I am"}
MOJI = {"â€™": "’", "â€œ": "“", "â€\x9d": "”", "â€”": "—", "â€“": "–", "â€˜": "‘", "â€¦": "…", "Ã©": "é", "Ã¨": "è", "Ã¶": "ö",
        "Ã¼": "ü", "Ã¤": "ä", "Ã±": "ñ", "Ã§": "ç", "ï¬\x81": "fi", "ï¬\x82": "fl"}
NUM_W = {str(i): w for i, w in enumerate("zero one two three four five six seven eight nine".split())}
QUOTED = re.compile(r"“([^”]+)”")
GENERIC = {"GRM-01", "GRM-02", "GRM-05", "GRM-09", "GRM-10", "GRM-14", "MEC-16", "CON-02", "CON-03", "EVD-08", "MEC-09"}
LEXICAL = {"STY-01", "STY-12", "STY-13", "STY-14", "STY-18"}


def _case(orig, rep):
    if orig[:1].isupper() and rep[:1].islower():
        return rep[0].upper() + rep[1:]
    return rep


def _rep(rid, f):
    x = f.get("x")
    if not x or f.get("as") is None:
        return None
    old = x[f["hs"]:f["he"]]
    if not old:
        return None
    fix = f.get("fix") or ""
    if rid in GENERIC:
        qs = QUOTED.findall(fix)
        if len(qs) != 1 or not re.match(r"^(Use|Write|Join|Spell|Report)", fix):
            return None
        rep = qs[0]
        if "…" in rep or "/" in rep:
            return None
        ow, rw = old.split(), rep.split()
        squash = lambda t: re.sub(r"[\s-]", "", t.lower())
        if squash(old) == squash(rep) or len(ow) == 1 or (ow and rw and (ow[0].lower() == rw[0].lower() or ow[-1].lower() == rw[-1].lower())):
            return _case(old, rep)
        return None
    if rid == "GRM-03":
        qs = QUOTED.findall(fix)
        if len(qs) != 1 or "/" in qs[0]:
            return None
        rep, ow = qs[0], old.split()
        if len(rep.split()) == 1 and len(ow) >= 2:
            return " ".join(ow[:-1] + [rep])
        return _case(old, rep)
    if rid in LEXICAL:
        m = re.match(r"^Use: (.+)$", fix)
        if m and m.group(1).strip() == "(delete)":
            return ""
        if not m or re.search(r"[/()]| or ", m.group(1)):
            return None
        return _case(old, m.group(1))
    if rid == "STY-02":
        nxt = x[f["he"]:f["he"] + 14].lower()
        if re.match(r"\s+(than|much|many|few|little|well|same|first|last|end|beginning|least|best|top|bottom|as|a\b|an\b|enough|soon|likely)", nxt):
            return None
        return "" if old.lower() in DELETABLE else None
    if rid == "STY-08":
        lo = old.lower().replace("’", "'")
        if lo in CONTR:
            return _case(old, CONTR[lo])
        m = re.match(r"^(\w+)n't$", lo)
        if m:
            return _case(old, m.group(1) + " not")
        m = re.match(r"^(\w+)'(re|ve|ll|m)$", lo)
        if m:
            return _case(old, m.group(1) + " " + {"re": "are", "ve": "have", "ll": "will", "m": "am"}[m.group(2)])
        return None
    if rid == "STY-09":
        return "."
    if rid == "MEC-01":
        return old.split()[0]
    if rid == "MEC-02":
        if old.strip() == "":
            return " "
        if old[:1].isspace():
            return old.strip()
        return old + " "
    if rid == "MEC-04":
        return old[0].upper() + old[1:]
    if rid == "MEC-05":
        return "…" if old.startswith("..") else ("?" if "?" in old else old[0])
    if rid == "MEC-06":
        m = re.match(r"^Did you mean: ([a-z'-]+)", fix)
        return m.group(1) if m else None
    if rid == "MEC-10":
        return "—" if "-" in old else None
    if rid == "MEC-12":
        return old.lower()
    if rid == "GRM-08":
        return "I"
    if rid == "GRM-11":
        return old + ","
    if rid == "GRM-12":
        return "that"
    if rid == "GRM-13":
        m = re.match(r"^, (\w+) (\w+)$", old)
        return "; %s, %s" % (m.group(1), m.group(2)) if m else None
    if rid == "INT-06":
        return MOJI.get(old)
    if rid == "INT-07":
        return ""
    if rid == "CON-11":
        m = re.match(r"^([1-9])(\s+\w+)$", old)
        if m and "words" in fix:
            return NUM_W[m.group(1)] + m.group(2)
        rev = {v: k for k, v in NUM_W.items()}
        m = re.match(r"^(\w+)(\s+\w+)$", old)
        if m and "digit" in fix and m.group(1).lower() in rev:
            return rev[m.group(1).lower()] + m.group(2)
    return None


def rewrites(doc, rules_full, catalog_by_id):
    changes = []
    for r, fs in rules_full:
        meta = catalog_by_id[r["id"]]
        for f in fs:
            rep = _rep(r["id"], f)
            if rep is None or f.get("b") is None:
                continue
            a, z = f["as"], f["ae"]
            old = doc.blocks[f["b"]]["t"][a:z]
            if rep == old:
                continue
            if rep == "":  # deletion: also take one neighbouring space
                t = doc.blocks[f["b"]]["t"]
                if z < len(t) and t[z] == " ":
                    z += 1
                elif a > 0 and t[a - 1] == " ":
                    a -= 1
                old = t[a:z]
            changes.append({"id": "%s:%d:%d" % (r["id"], f["b"], f["as"]), "fa": f["as"], "rule": r["id"], "name": r["name"], "cat": r["cat"],
                            "sev": f["sev"], "b": f["b"], "a": a, "z": z, "old": old, "new": rep,
                            "msg": f["msg"], "reason": f.get("fix") or meta["fix"], "why": meta["why"]})
    # resolve overlaps deterministically: earlier start wins, then higher severity, then rule id
    rank = {"critical": 0, "major": 1, "minor": 2, "note": 3}
    changes.sort(key=lambda c: (c["b"], c["a"], rank.get(c["sev"], 4), c["rule"]))
    out, last = [], {}
    for c in changes:
        if c["a"] < last.get(c["b"], -1):
            continue
        out.append(c)
        last[c["b"]] = max(c["z"], c["a"] + 1)
    return out


def apply_changes(text, changes):
    out, pos = [], 0
    for c in sorted(changes, key=lambda c: c["a"]):
        if c["a"] < pos:
            continue
        out.append(text[pos:c["a"]])
        out.append(c["new"])
        pos = c["z"]
    out.append(text[pos:])
    return re.sub(r"  +", " ", "".join(out))


def revised_document(doc, changes, rejected=()):
    rej = set(rejected)
    byb = defaultdict(list)
    for c in changes:
        if c["id"] not in rej:
            byb[c["b"]].append(c)
    lines = []
    for i, b in enumerate(doc.blocks):
        t = apply_changes(b["t"], byb[i]) if i in byb else b["t"]
        if b["k"] == "title":
            lines.append("# " + t)
        elif b["k"] == "h":
            lines.append("#" * min(6, (b["l"] or 1) + 1) + " " + t)
        else:
            lines.append(t)
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ readability & lexical metrics
def readability(doc):
    words = [w for x in doc.sents for w in x["w"]]
    W, S = len(words), len(doc.sents)
    if W < 30 or not S:
        return {}
    syl = [E.syllables(w) for w in words]
    poly = sum(1 for n in syl if n >= 3)
    chars = sum(len(w) for w in words)
    long_ = sum(1 for w in words if len(w) > 6)
    fk = E.fk_grade(W, S, sum(syl))
    fre = 206.835 - 1.015 * W / S - 84.6 * sum(syl) / W
    fog = 0.4 * (W / S + 100.0 * poly / W)
    smog = 1.043 * math.sqrt(poly * 30.0 / S) + 3.1291
    L, Sx = 100.0 * chars / W, 100.0 * S / W
    cli = 0.0588 * L - 0.296 * Sx - 15.8
    ari = 4.71 * chars / W + 0.5 * W / S - 21.43
    lix = W / S + 100.0 * long_ / W
    rix = long_ / S
    low = [w.lower() for w in words]
    types = Counter(low)
    win = 100
    mattr = (sum(len(set(low[i:i + win])) / win for i in range(0, max(1, len(low) - win + 1), 25)) /
             len(range(0, max(1, len(low) - win + 1), 25))) if len(low) >= win else len(types) / len(low)
    hapax = sum(1 for c in types.values() if c == 1)
    dlg = sum(len(q) for b in doc.prose for q in re.findall(r"“[^”]*”|\"[^\"]*\"", doc.blocks[b]["t"]))
    chars_all = sum(len(doc.blocks[b]["t"]) for b in doc.prose) or 1
    sents_per_para = S / max(1, len(doc.prose))
    passive = sum(1 for x in doc.sents if E.PASSIVE_RE.search(x["t"]))
    return {
        "Flesch-Kincaid grade": round(fk, 1), "Flesch reading ease": round(fre, 1), "Gunning fog": round(fog, 1),
        "SMOG": round(smog, 1), "Coleman-Liau": round(cli, 1), "Automated Readability Index": round(ari, 1),
        "LIX": round(lix, 1), "RIX": round(rix, 2),
        "Words per sentence": round(W / S, 1), "Syllables per word": round(sum(syl) / W, 2), "Characters per word": round(chars / W, 2),
        "Complex words (3+ syllables)": "%.1f%%" % (100.0 * poly / W), "Long words (7+ letters)": "%.1f%%" % (100.0 * long_ / W),
        "Sentences per paragraph": round(sents_per_para, 1), "Passive sentences": "%.1f%%" % (100.0 * passive / S),
        "Distinct words": len(types), "Lexical diversity (MATTR-100)": round(mattr, 3),
        "Words used once (hapax)": "%.1f%%" % (100.0 * hapax / max(1, len(types))), "Dialogue share": "%.1f%%" % (100.0 * dlg / chars_all),
        "Reading time": "%d min" % round(W / 238.0), "Speaking time": "%d min" % round(W / 150.0),
    }
