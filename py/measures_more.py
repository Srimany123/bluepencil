"""Measurements for the extended rules (rules_more.py)."""
import re
from collections import Counter, defaultdict

import engine as E
import lexicon as LX
from engine import F, M, WORD_RE, fmt, per1k, sent_at

I = re.I


# ------------------------------------------------------------------ helpers
def _f(doc, b, st, en, msg, fix=None, sev=None):
    si = sent_at(doc, b, st) if doc.blocks[b]["k"] == "para" else None
    if si is not None:
        return F(doc, msg, s=st, e=en, sent=si, fix=fix, sev=sev)
    return F(doc, msg, b, st, en, fix=fix, sev=sev)


def _blocks(doc, where):
    if where == "prose":
        return doc.prose
    if where == "text":
        return doc.textual
    if where == "refs":
        return [r["i"] for r in doc.refs]
    if where == "nonref":
        return [i for i in range(doc.n) if doc.blocks[i]["k"] != "ref"]
    return range(doc.n)


def Pt(rx, msg, fix=None, keep=None, flags=I):
    """A pattern item. msg/fix may be a string ({0} = match), a dict keyed by lower-case match, or a function."""
    return (re.compile(rx, flags) if isinstance(rx, str) else rx, msg, fix, keep)


def _txt(v, m):
    if v is None:
        return None
    if callable(v):
        return v(m)
    if isinstance(v, dict):
        return v.get(re.sub(r"\s+", " ", m.group(0).lower()), next(iter(v.values())) if False else None)
    return v.replace("{0}", m.group(0))


def _prefilter(items):
    parts = []
    for rx, msg, fix, keep in items:
        p = rx.pattern
        parts.append("(?i:%s)" % p if rx.flags & re.I else "(?:%s)" % p)
    try:
        return re.compile("|".join(parts))
    except re.error:
        return None


def patrule(items, label, unit="per1k", where="prose", min_words=50):
    pre = _prefilter(items)
    def run(doc, rc, s):
        if doc.words < min_words:
            return None
        fs = []
        for b in _blocks(doc, where):
            t = doc.blocks[b]["t"]
            if pre is not None and not pre.search(t):
                continue
            hits = []
            for rx, msg, fix, keep in items:
                for m in rx.finditer(t):
                    if keep and not keep(m, t, doc, b):
                        continue
                    hits.append((m.start(), m.end(), _txt(msg, m), _txt(fix, m)))
            for st, en, msg, fix in sorted(hits, key=lambda h: (h[0], h[1])):
                fs.append(_f(doc, b, st, en, msg, fix))
        v = per1k(len(fs), doc.words) if unit == "per1k" else len(fs)
        return M(v, fs, "%d %s." % (len(fs), label))
    return run


def lexrule(table, label, unit="per1k", where="prose", fixfmt="Use: %s", msgfmt="“%s”"):
    """Phrase table -> rule. table: dict phrase -> suggestion (or list of phrases)."""
    tbl = table if isinstance(table, dict) else {p: None for p in table}
    rx = E.phrase_re(tbl)
    def run(doc, rc, s):
        if doc.words < 50:
            return None
        fs = []
        for b in _blocks(doc, where):
            for m in rx.finditer(doc.blocks[b]["t"]):
                key = re.sub(r"\s+", " ", m.group(0).lower())
                sug = tbl.get(key)
                fs.append(_f(doc, b, m.start(), m.end(), msgfmt % m.group(0), (fixfmt % sug) if sug else None))
        c = Counter(re.sub(r"\s+", " ", f["x"][f["hs"]:f["he"]].lower()) for f in fs if f.get("x"))
        top = ", ".join("%s (%d)" % kv for kv in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
        v = per1k(len(fs), doc.words) if unit == "per1k" else len(fs)
        return M(v, fs, "%d %s%s." % (len(fs), label, (": " + top) if top else ""))
    return run


def mixrule(styles, label, min_total=3, where="prose"):
    """styles: list of (name, regex[, keep]). Value = share of the minority styles (%)."""
    comp = [(st[0], re.compile(st[1]) if isinstance(st[1], str) else st[1], st[2] if len(st) > 2 else None) for st in styles]
    def run(doc, rc, s):
        hits = {n: [] for n, _, _ in comp}
        for b in _blocks(doc, where):
            t = doc.blocks[b]["t"]
            taken = []
            for n, rx, keep in comp:
                for m in rx.finditer(t):
                    if keep and not keep(m, t, doc, b):
                        continue
                    if any(a < m.end() and m.start() < z for a, z in taken):
                        continue
                    taken.append((m.start(), m.end()))
                    hits[n].append((b, m.start(), m.end()))
        total = sum(len(v) for v in hits.values())
        used = {k: v for k, v in hits.items() if v}
        if total < min_total or not used:
            return None
        desc = ", ".join("%s %d" % (k, len(v)) for k, v in sorted(used.items(), key=lambda kv: (-len(kv[1]), kv[0])))
        if len(used) == 1:
            return M(0.0, [], "%s: consistent (%s)." % (label, desc))
        major = max(used, key=lambda k: (len(used[k]), k))
        fs = []
        for k, v in used.items():
            if k == major:
                continue
            for b, st, en in v[:40]:
                fs.append(_f(doc, b, st, en, "“%s” style here; the document mostly uses “%s”." % (k, major), "Use the “%s” style." % major))
        fs.sort(key=lambda f: (f.get("b", 0), f.get("hs", 0)))
        return M(100.0 * (total - len(used[major])) / total, fs, "%s: %s." % (label, desc))
    return run


def sentrule(test, label, unit="per1k", min_words=50):
    """test(sentence_dict) -> None or (msg, fix, start, end) with offsets relative to the sentence."""
    def run(doc, rc, s):
        if doc.words < min_words or not doc.sents:
            return None
        fs = []
        for k, x in enumerate(doc.sents):
            r = test(x, doc, rc)
            if r:
                msg, fix, a, z = r
                if a is None:
                    fs.append(F(doc, msg, sent=k, fix=fix))
                else:
                    fs.append(F(doc, msg, s=x["s"] + a, e=x["s"] + z, sent=k, fix=fix))
        v = per1k(len(fs), doc.words) if unit == "per1k" else (100.0 * len(fs) / len(doc.sents) if unit == "pct" else len(fs))
        return M(v, fs, "%d %s." % (len(fs), label))
    return run


def section_blocks(doc, canon, kinds=("para",)):
    for j, x in enumerate(doc.sections):
        if canon in x["canon"]:
            end = doc.n
            for y in doc.sections[j + 1:]:
                if y["level"] <= x["level"]:
                    end = y["i"]
                    break
            return [i for i in range(x["i"] + 1, end) if doc.blocks[i]["k"] in kinds], x
    return [], None


def labelled_para(doc, label_rx):
    for i in doc.textual:
        m = re.match(label_rx, doc.blocks[i]["t"], I)
        if m:
            return i, m.end()
    return None, None


HEAD_NUM_RE = re.compile(r"^(?:(?:chapter|part|section)\s+)?(\d+(?:\.\d+)*)\.?\s+\S", I)
FRONT_BACK = {"references", "acknowledgements", "keywords", "funding", "data availability", "competing interests", "ethics",
              "author contributions", "contents", "appendix", "abstract"}


# ------------------------------------------------------------------ structure
def m_str09(doc, rc, s):
    tb = next((i for i, b in enumerate(doc.blocks) if b["k"] == "title"), None)
    fs = []
    if tb is None:
        fs.append(F(doc, "No title found at the start of the document.", sev="major", fix="Put the title on the first line."))
        return M(len(fs), fs, "No title detected.")
    t = doc.blocks[tb]["t"].strip()
    n = len(WORD_RE.findall(t))
    mx = rc["params"]["max_words"]
    if n > mx:
        fs.append(F(doc, "Title is %d words (limit %d)." % (n, mx), tb, 0, len(t), fix="Cut to the essential topic and finding."))
    if n < 3:
        fs.append(F(doc, "Title is only %d word(s)." % n, tb, 0, len(t), fix="Make the title specific to this work."))
    if t.endswith("."):
        fs.append(F(doc, "Title ends with a full stop.", tb, len(t) - 1, len(t), fix="Remove the full stop."))
    letters = [c for c in t if c.isalpha()]
    if len(letters) > 8 and all(c.isupper() for c in letters):
        fs.append(F(doc, "Title is in all capitals.", tb, 0, len(t), fix="Use title case or sentence case."))
    ab = [a for a in re.findall(r"\b[A-Z]{2,}[a-z]?\b", t) if a not in ("I", "II", "III", "IV") and not (len(letters) > 8 and all(c.isupper() for c in letters))]
    if ab:
        fs.append(F(doc, "Title contains abbreviation(s): %s." % ", ".join(ab[:4]), tb, t.find(ab[0]), t.find(ab[0]) + len(ab[0]),
                    sev="note", fix="Spell abbreviations out in the title."))
    return M(len(fs), fs, "“%s” (%d words)." % (t[:90], n))


AIM_RE = re.compile(
    r"\b(aims? (?:to|at)|objectives? (?:is|are|of|was)|purpose of (?:this|the present|our)|goal of (?:this|the present|our)|"
    r"we (?:propose|present|introduce|investigate|examine|explore|study|test|evaluate|describe|report|ask|address|argue|hypothesi[sz]e|seek)|"
    r"(?:this|the present|our) (?:paper|study|article|work|chapter|report|research|review|thesis|essay) (?:aims|seeks|presents|examines|investigates|explores|proposes|describes|reports|asks|addresses|argues|introduces|evaluates|focuses|tests|considers)|"
    r"research questions?|hypothes[ie]s|in this (?:paper|study|article|chapter|work|report|essay))\b", I)


def m_str10(doc, rc, s):
    bl, sec = section_blocks(doc, "introduction")
    if not bl:
        bl = doc.prose[: max(3, len(doc.prose) // 4)]
    if not bl:
        return None
    for b in bl:
        m = AIM_RE.search(doc.blocks[b]["t"])
        if m:
            return M(100.0, [], "Aim stated: “%s…” (¶ %s)." % (m.group(0), doc.ordinal[b]))
    where = sec["i"] if sec else bl[0]
    return M(0.0, [F(doc, "No aim, question or hypothesis found in the %s." % ("introduction" if sec else "opening"), where, 0, 0)],
             "No explicit aim statement found.")


def m_str11(doc, rc, s):
    fs = []
    secs = doc.sections
    for j, x in enumerate(secs):
        kids = []
        for y in secs[j + 1:]:
            if y["level"] <= x["level"]:
                break
            kids.append(y)
        if not kids:
            continue
        top = min(k["level"] for k in kids)
        direct = [k for k in kids if k["level"] == top]
        if len(direct) == 1:
            fs.append(F(doc, "“%s” has a single subsection (“%s”)." % (x["title"], direct[0]["title"]), direct[0]["i"], 0, len(direct[0]["title"])))
    return M(len(fs), fs, "%d lone subsection(s)." % len(fs)) if secs else None


def m_str12(doc, rc, s):
    if not doc.headings:
        return None
    mx = rc["params"]["max_words"]
    fs = []
    for h in doc.headings:
        n = len(doc.blocks[h]["t"].split())
        if n > mx:
            fs.append(F(doc, "Heading of %d words." % n, h, 0, len(doc.blocks[h]["t"])))
    return M(len(fs), fs, "%d heading(s) over %d words." % (len(fs), mx))


def m_str13(doc, rc, s):
    bl, sec = section_blocks(doc, "introduction")
    if not sec or not doc.words:
        return None
    w = sum(doc.block_words.get(b, 0) for b in bl)
    return M(100.0 * w / doc.words, [], "Introduction is %s words, %.0f%% of the body." % (fmt(w), 100.0 * w / doc.words))


def _abstract_blocks(doc):
    b, off = labelled_para(doc, r"^abstract\s*[:.—–-]")
    if b is not None:
        return [b]
    bl, sec = section_blocks(doc, "abstract")
    return bl


def m_str14(doc, rc, s):
    bl = _abstract_blocks(doc)
    if not bl:
        return None
    fs = []
    for b in bl:
        if doc.cite_spans.get(b):
            a, z = doc.cite_spans[b][0]
            fs.append(F(doc, "The abstract cites references.", b, a, z, fix="Remove citations from the abstract."))
    if not any(re.search(r"\d", doc.blocks[b]["t"]) for b in bl):
        fs.append(F(doc, "The abstract reports no numbers, so probably no concrete result.", bl[0], 0, 0, sev="note",
                    fix="State the main finding with its size."))
    if len(bl) > 1:
        fs.append(F(doc, "The abstract has %d paragraphs." % len(bl), bl[1], 0, 0, sev="note", fix="Merge into one paragraph."))
    return M(len(fs), fs, "%d abstract issue(s)." % len(fs))


def m_str15(doc, rc, s):
    b, off = labelled_para(doc, r"^(?:key ?words|index terms)\s*[:.—–-]\s*")
    if b is None:
        bl, sec = section_blocks(doc, "keywords")
        if not bl:
            return None
        b, off = bl[0], 0
    t = doc.blocks[b]["t"][off:]
    kws = [k.strip() for k in re.split(r"[,;·•|]", t) if k.strip()]
    return M(len(kws), [], "%d keyword(s): %s." % (len(kws), ", ".join(kws[:10])))


def m_str16(doc, rc, s):
    bl, sec = section_blocks(doc, "conclusion")
    if not sec:
        return None
    fs = []
    for b in bl:
        for a, z in doc.cite_spans.get(b, []):
            fs.append(F(doc, "New citation in the conclusion.", b, a, z, fix="Move it to the discussion."))
        for m in E.XREF_RE.finditer(doc.blocks[b]["t"]):
            fs.append(F(doc, "Figure/table referenced in the conclusion.", b, m.start(), m.end(), fix="Present evidence before the conclusion."))
    return M(len(fs), fs, "%d new item(s) in “%s”." % (len(fs), sec["title"]))


def m_str17(doc, rc, s):
    hs = [x for x in doc.sections if not (set(x["canon"]) & FRONT_BACK)]
    if len(hs) < 3:
        return None
    nums = [(x, HEAD_NUM_RE.match(x["title"])) for x in hs]
    numbered = [(x, m) for x, m in nums if m]
    fs = []
    if numbered and len(numbered) < len(hs):
        unn = [x for x, m in nums if not m]
        if len(numbered) >= len(unn):
            for x in unn[:20]:
                fs.append(F(doc, "“%s” is unnumbered while most headings are numbered." % x["title"], x["i"], 0, len(x["title"])))
        else:
            for x, m in numbered[:20]:
                fs.append(F(doc, "“%s” is numbered while most headings are not." % x["title"], x["i"], 0, len(x["title"])))
    prev = None
    for x, m in numbered:
        cur = [int(p) for p in m.group(1).split(".")]
        if prev is not None:
            ok = False
            for d in range(min(len(prev), len(cur))):
                # same prefix, next number at depth d
                if cur[:d] == prev[:d] and cur[d] == prev[d] + 1 and len(cur) == d + 1 or (cur[:d] == prev[:d] and cur[d] == prev[d] + 1 and all(c == 1 for c in cur[d + 1:])):
                    ok = True
                    break
            if len(cur) == len(prev) + 1 and cur[:-1] == prev and cur[-1] == 1:
                ok = True
            if not ok:
                fs.append(F(doc, "Numbering goes from %s to %s." % (".".join(map(str, prev)), ".".join(map(str, cur))), x["i"], 0, len(x["title"]),
                            fix="Renumber in sequence."))
        prev = cur
    return M(len(fs), fs, "%d of %d body headings numbered; %d issue(s)." % (len(numbered), len(hs), len(fs)))


def m_str18(doc, rc, s):
    body = [x for x in doc.sections if not (set(x["canon"]) & FRONT_BACK)]
    if not body:
        return None
    top = min(x["level"] for x in body)
    ch = [x for x in body if x["level"] == top and x["total"] > 0]
    if len(ch) < 3:
        return None
    lo, hi = rc["params"]["min_words"], rc["params"]["max_words"]
    fs = []
    for x in ch:
        if x["total"] < lo:
            fs.append(F(doc, "“%s” is %s words (shortest allowed %s)." % (x["title"], fmt(x["total"]), fmt(lo)), x["i"], 0, len(x["title"])))
        elif x["total"] > hi:
            fs.append(F(doc, "“%s” is %s words (longest allowed %s)." % (x["title"], fmt(x["total"]), fmt(hi)), x["i"], 0, len(x["title"])))
    return M(100.0 * len(fs) / len(ch), fs, "%d of %d chapters outside %s–%s words." % (len(fs), len(ch), fmt(lo), fmt(hi)))


# ------------------------------------------------------------------ clarity
def _complex(w):
    lw = w.lower()
    if w[0].isupper() or "-" in lw:
        return False
    base = re.sub(r"(ed|es|ing)$", "", lw)
    return E.syllables(base) >= 3


def m_clr06(doc, rc, s):
    if doc.words < 100:
        return None
    n, c, fs, top = 0, 0, [], Counter()
    for k, x in enumerate(doc.sents):
        cx = [w for w in x["w"] if _complex(w)]
        n += len(x["w"])
        c += len(cx)
        top.update(w.lower() for w in cx)
        if len(cx) >= 6 and len(cx) >= 0.35 * len(x["w"]):
            fs.append(F(doc, "%d complex words in one sentence." % len(cx), sent=k, fix="Replace some with shorter words."))
    t = ", ".join("%s (%d)" % kv for kv in sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:6])
    return M(100.0 * c / n if n else 0, fs, "%.1f%% of words have 3+ syllables; most frequent: %s." % (100.0 * c / n if n else 0, t))


def _t_of(x, doc, rc):
    n = len(re.findall(r"\bof\b", x["t"], I))
    return ("%d uses of “of” in one sentence." % n, "Break the chain with a possessive or a verb.", None, None) if n >= 4 else None


def _t_paren(x, doc, rc):
    t = x["t"]
    depth = mx = 0
    for ch in t:
        if ch == "(":
            depth += 1
            mx = max(mx, depth)
        elif ch == ")":
            depth = max(0, depth - 1)
    n = t.count("(")
    if n >= 3 or mx >= 2:
        return ("%s in one sentence." % ("Nested brackets" if mx >= 2 else "%d parenthetical asides" % n), "Move asides into their own sentences.", None, None)
    return None


def _t_nums(x, doc, rc):
    n = len(re.findall(r"(?<![\w.])\d[\d,.]*\b", x["t"]))
    return ("%d numbers in one sentence." % n, "Put the numbers in a table.", None, None) if n >= 6 else None


VAGUE_THIS = re.compile(r"^[“\"']?(This|That|These|Those)\s+(is|was|are|were|shows?|showed|means|meant|suggests?|suggested|indicates?|indicated|implies|implied|demonstrates?|leads?|led|results?|resulted|allows?|allowed|makes?|made|can|could|will|would|may|might|has|have|had|seems?|appears?|helps?|causes?|caused|provides?|gives?|explains?|reflects?|highlights?|confirms?|supports?)\b")


def _t_vague(x, doc, rc):
    m = VAGUE_THIS.match(x["t"])
    return ("“%s” without a noun." % m.group(0), "Add what “%s” refers to." % m.group(1), m.start(1), m.end(2)) if m else None


NOMINAL_RE = re.compile(r"\b[a-z]{3,}(?:tion|sion|ment|ance|ence|ancy|ency|ity|ness|ization|isation)s?\b")
NOMINAL_COMMON = set("""government environment information university community experience evidence science sentence difference
audience moment comment element department apartment equipment statement treatment management development agreement
argument document instrument payment segment tournament question nation station position situation population education
attention relation addition condition section function direction mention action fiction nation city quality quantity
activity ability identity reality variety society majority minority priority security authority property facility
entity opportunity business illness distance balance performance importance silence confidence presence absence
influence reference sequence conference violence intelligence existence patience preference century""".split())


def m_clr11(doc, rc, s):
    if doc.words < 100:
        return None
    total, fs = 0, []
    for k, x in enumerate(doc.sents):
        hits = [m for m in NOMINAL_RE.finditer(x["t"]) if m.group(0).rstrip("s") not in NOMINAL_COMMON and m.group(0) not in NOMINAL_COMMON]
        total += len(hits)
        if len(hits) >= 4:
            fs.append(F(doc, "%d nominalizations: %s." % (len(hits), ", ".join(m.group(0) for m in hits[:6])), sent=k,
                        fix="Turn some of the nouns back into verbs."))
    return M(100.0 * total / doc.words, fs, "%d nominalizations (%.1f%% of words)." % (total, 100.0 * total / doc.words))


SUBORD = re.compile(r"^[“\"']?(Although|Though|While|Whereas|Because|Since|If|When|After|Before|Unless|Despite|In order|Given|Considering|As|Once|Until|In|With|For|By|During|Through|Following|Based on|Even though|Even if|Due to|According to)\b")


def _t_front(x, doc, rc):
    if not SUBORD.match(x["t"]) or x["n"] < 25:
        return None
    c = x["t"].find(",")
    if c < 0:
        return None
    n = len(WORD_RE.findall(x["t"][:c]))
    return ("%d words before the main clause." % n, "Lead with the main clause.", 0, c) if n > 15 else None


# ------------------------------------------------------------------ style tables
BUZZ = {"utilize": "use", "utilise": "use", "utilizes": "uses", "utilized": "used", "utilizing": "using", "utilization": "use",
        "utilisation": "use", "leverage": "use", "leverages": "uses", "leveraged": "used", "leveraging": "using",
        "synergy": "cooperation", "synergies": "benefits", "synergistic": "combined", "holistic": "complete / whole",
        "paradigm": "model", "seamless": "smooth", "seamlessly": "smoothly", "impactful": "effective", "actionable": "practical",
        "best-in-class": "leading", "world-class": "excellent", "next-generation": "new", "empower": "enable",
        "empowers": "enables", "deep dive": "detailed look", "circle back": "return", "touch base": "talk",
        "value-add": "benefit", "going forward": "from now on (or delete)", "learnings": "lessons", "ideate": "think of",
        "operationalize": "put into practice", "incentivize": "encourage", "facilitate": "help / enable",
        "facilitates": "helps", "commence": "begin", "commenced": "began", "endeavor": "try / effort", "endeavour": "try / effort",
        "ascertain": "find out", "aforementioned": "this / these", "heretofore": "until now", "henceforth": "from now on",
        "herein": "here", "therein": "there / in it", "wherein": "where / in which", "thereof": "of it", "notwithstanding": "despite",
        "in lieu of": "instead of", "vis-à-vis": "about / compared with", "per se": "(delete)", "myriad of": "many",
        "plethora of": "many", "multitude of": "many", "a wide range of": "many", "transformative": "(be specific)",
        "game-changing": "(be specific)", "mission-critical": "essential", "low-hanging": "easy"}

REDUNDANT = {"past history": "history", "exact same": "same", "general consensus": "consensus", "consensus of opinion": "consensus",
             "period of time": "period / time", "still remains": "remains", "reason why": "reason", "free gift": "gift",
             "unexpected surprise": "surprise", "past experience": "experience", "advance warning": "warning",
             "each individual": "each", "mutual cooperation": "cooperation", "new beginning": "beginning", "small in size": "small",
             "large in size": "large", "red in color": "red", "blue in color": "blue", "round in shape": "round",
             "final conclusion": "conclusion", "current status": "status", "end goal": "goal", "future prospects": "prospects",
             "sum total": "total", "true facts": "facts", "actual fact": "fact", "personal opinion": "opinion",
             "first began": "began", "first started": "started", "continue on": "continue", "refer back": "refer",
             "reply back": "reply", "plan ahead": "plan", "postpone until later": "postpone", "collaborate together": "collaborate",
             "cooperate together": "cooperate", "merge together": "merge", "mix together": "mix", "gather together": "gather",
             "connect together": "connect", "add up together": "add up", "ATM machine": "ATM", "PIN number": "PIN",
             "LCD display": "LCD", "HIV virus": "HIV", "UPC code": "UPC", "very unique": "unique", "totally unique": "unique",
             "completely unanimous": "unanimous", "fully complete": "complete", "completely finished": "finished",
             "absolutely certain": "certain", "empty space": "space", "foreign imports": "imports", "major breakthrough": "breakthrough",
             "basic necessities": "necessities", "brief moment": "moment", "kneel down": "kneel", "ascend up": "ascend",
             "descend down": "descend", "raise up": "raise", "sudden impulse": "impulse", "usual custom": "custom",
             "visible to the eye": "visible", "estimated at about": "estimated at", "approximately about": "about",
             "and also": "and", "but however": "but", "in my opinion, I think": "I think", "whether or not": "whether"}

NEGATIVE = {"not able": "unable", "not possible": "impossible", "not certain": "uncertain", "not sure": "unsure",
            "did not remember": "forgot", "did not succeed": "failed", "not many": "few", "not much": "little",
            "not the same": "different", "not different": "similar", "did not pay attention to": "ignored",
            "does not have": "lacks", "do not have": "lack", "did not have": "lacked", "not unlike": "like",
            "not uncommon": "common", "not unusual": "usual", "not insignificant": "significant", "not unimportant": "important",
            "not infrequently": "often", "not often": "rarely", "not include": "omit / exclude", "did not include": "omitted",
            "not accept": "reject", "did not accept": "rejected", "not allow": "prevent", "did not allow": "prevented",
            "not correct": "incorrect", "not complete": "incomplete", "not consistent": "inconsistent", "not clear": "unclear",
            "not relevant": "irrelevant", "not honest": "dishonest", "not on time": "late", "not present": "absent",
            "did not consider": "overlooked", "not the case": "untrue / false", "not dissimilar": "similar", "not legal": "illegal"}

INFORMAL = {"gonna": "going to", "wanna": "want to", "gotta": "have to", "kinda": "somewhat", "sorta": "somewhat",
            "ain't": "is not", "lol": "(delete)", "btw": "by the way", "okay": "acceptable", "ok": "acceptable",
            "a lot": "many / much", "lots of": "many / much", "stuff": "material / items", "huge": "large", "awesome": "excellent",
            "cool": "good", "super": "very", "totally": "entirely", "guys": "people", "kids": "children", "pretty much": "almost",
            "anyways": "anyway", "nope": "no", "yeah": "yes", "basically": "(delete)", "tons of": "many", "loads of": "many",
            "a bunch of": "several", "get rid of": "remove", "figure out": "determine", "come up with": "devise",
            "find out": "discover", "look into": "investigate", "deal with": "address", "big deal": "important",
            "no-brainer": "obvious choice"}

OVERCLAIM = ["proves", "proved", "prove that", "proof that", "definitively", "conclusively", "undeniably", "undoubtedly",
             "irrefutable", "irrefutably", "unquestionably", "beyond doubt", "without a doubt", "without doubt",
             "always", "never", "everyone knows", "everybody knows", "all researchers", "no one", "nobody", "guarantees",
             "guaranteed", "certainly shows", "clearly proves", "it is certain", "settles the question", "once and for all",
             "the only explanation", "perfectly", "completely solves", "solves the problem"]

TRANSITIONS = r"However|Moreover|Furthermore|Additionally|In addition|Thus|Therefore|Hence|Consequently|Nevertheless|Nonetheless|Indeed|Notably|Importantly|Interestingly|Also|Further|Accordingly|Subsequently|Similarly|Likewise"

VAGUEW = ["thing", "things", "stuff", "aspect", "aspects", "various", "nice", "interesting", "a lot", "lots", "somehow",
          "something", "certain things", "and so on", "and so forth", "etc", "kind of", "sort of", "some sort of", "a number of things",
          "factors", "issues", "situations", "good", "bad", "big", "really"]

TAGS = r"exclaimed|retorted|hissed|snapped|growled|chuckled|smirked|sighed|breathed|interjected|opined|queried|ejaculated|grinned|laughed|smiled|snorted|barked|purred|spat|gasped|shrieked|bellowed|murmured|muttered|mumbled|whined|declared|announced|proclaimed|stated|remarked|uttered|intoned|chortled|giggled|hollered|implored|insisted|quipped|reasoned|scoffed|sneered|squeaked|stammered|stuttered|wailed|whimpered|yelped"

FILTER = r"felt|feel|feels|feeling|saw|see|sees|seeing|heard|hear|hears|noticed|notice|notices|realized|realised|realize|realise|wondered|wonder|seemed|seem|seems|watched|watch|decided|knew|thought|looked|smelled|smelt|tasted|could see|could hear|could feel|could smell|could tell"

GESTURES = ["nodded", "nods", "shrugged", "shrugs", "sighed", "sighs", "grinned", "grins", "smirked", "smirks", "frowned", "frowns",
            "rolled her eyes", "rolled his eyes", "rolled their eyes", "rolled my eyes", "raised an eyebrow", "raised her eyebrow",
            "raised his eyebrow", "arched an eyebrow", "let out a breath", "a breath she didn't know she was holding",
            "a breath he didn't know he was holding", "bit her lip", "bit his lip", "clenched his jaw", "clenched her jaw",
            "his jaw tightened", "her jaw tightened", "ran a hand through his hair", "ran a hand through her hair",
            "heart pounded", "heart raced", "stomach dropped", "stomach churned", "blood ran cold", "eyes widened",
            "swallowed hard", "gulped", "chuckled darkly", "padded"]

GENERIC_PERSON = r"(?:the|a|an|each|every|any|one|no)\s+(?:user|student|reader|participant|patient|person|researcher|author|teacher|doctor|physician|nurse|employee|customer|child|client|scientist|engineer|writer|applicant|individual|subject|respondent|worker|manager|player|developer)"


def m_sty17(doc, rc, s):
    if doc.words < 100:
        return None
    rx = re.compile(r"^[“\"']?(%s)\b" % TRANSITIONS)
    fs, prev = [], None
    for k, x in enumerate(doc.sents):
        m = rx.match(x["t"])
        if m:
            same = prev == m.group(1) and doc.sents[k - 1]["b"] == x["b"]
            fs.append(F(doc, "“%s” opens consecutive sentences." % m.group(1) if same else "Opens with “%s”." % m.group(1),
                        s=x["s"] + m.start(1), e=x["s"] + m.end(1), sent=k, fix="Drop the transition if the link is already clear."))
            prev = m.group(1)
        else:
            prev = None
    c = Counter(f["x"][f["hs"]:f["he"]] for f in fs)
    top = ", ".join("%s (%d)" % kv for kv in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
    return M(per1k(len(fs), doc.words), fs, "%d transition-led sentence(s)%s." % (len(fs), (": " + top) if top else ""))


def m_sty20(doc, rc, s):
    if len(doc.prose) < 3:
        return None
    firsts = []
    for b in doc.prose:
        w = WORD_RE.match(doc.blocks[b]["t"].lstrip("“\"'‘(— "))
        firsts.append(w.group(0).lower() if w else "")
    fs, k = [], 0
    while k < len(firsts):
        j = k
        while j + 1 < len(firsts) and firsts[j + 1] == firsts[k] and firsts[k]:
            j += 1
        if j - k + 1 >= 3:
            fs.append(F(doc, "%d paragraphs in a row start with “%s”." % (j - k + 1, firsts[k]), doc.prose[k], 0, len(firsts[k]) + 1))
        k = j + 1
    return M(len(fs), fs, "%d run(s) of repeated paragraph openings." % len(fs))


def _t_conj(x, doc, rc):
    m = re.match(r"^[“\"']?(And|But|So|Or|Plus|Also|Yet)\b(?!\s+(?:on|forth)\b)", x["t"])
    return ("Starts with “%s”." % m.group(1), "Join to the previous sentence or use a formal transition.", m.start(1), m.end(1)) if m else None


def _t_question(x, doc, rc):
    t = x["t"].rstrip()
    if t.endswith("?") and not t.lstrip().startswith(("“", '"', "‘", "'")) and not re.search(r"[“\"]", t):
        return ("Rhetorical question.", "State the claim instead.", None, None)
    return None


def _t_partic(x, doc, rc):
    m = re.match(r"^[“\"']?([A-Z][a-z]+ing)\b[^,.;]{0,60},", x["t"])
    if m and m.group(1).lower() not in ("during", "according", "nothing", "something", "anything", "everything", "morning", "evening",
                                        "king", "spring", "string", "thing", "ring", "being", "building", "notwithstanding", "regarding",
                                        "concerning", "including", "following", "considering", "assuming", "excluding", "beginning"):
        return ("Opens with “%s…”." % m.group(1), "Vary the opening; check the two actions really happen at once.", m.start(1), m.end(1))
    return None


def m_sty22(doc, rc, s):
    rx = re.compile(GENERIC_PERSON + r"\b[^.!?;]{0,80}?\b(he|his|him|himself)\b", I)
    fs = []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in rx.finditer(t):
            fs.append(_f(doc, b, m.start(1), m.end(1), "Generic “%s”." % m.group(1), "Use “they/their” or a plural noun."))
    return M(len(fs), fs, "%d generic masculine pronoun(s)." % len(fs)) if doc.words >= 50 else None


# ------------------------------------------------------------------ grammar
def _keep_cap_sent(m, t, doc, b):
    return True


CONFUSED = [
    Pt(r"\b(more|less|better|worse|rather|other|greater|fewer|larger|smaller|higher|lower|faster|slower|older|younger|earlier|later) then\b", "“{0}”", lambda m: "Use “%s than”." % m.group(1)),
    Pt(r"\bits (?:a|an|the|been|not|also|very|important|clear|possible|likely|true|time|unclear|difficult|easy|hard|worth)\b", "“{0}”", "Use “it's” (it is) here."),
    Pt(r"\bit['’]s (?:own|size|shape|colou?r|value|purpose|role|use|function|members|users|name|structure|effects?|impact|ability|performance|weight|length|width|height|origin|place)\b", "“{0}”", "Use “its” (possessive)."),
    Pt(r"\byour (?:welcome|going to|not|right that)\b", "“{0}”", "Use “you're” (you are)."),
    Pt(r"\b(?:could|should|would|must|might|may) of\b", "“{0}”", lambda m: "Use “%s have”." % m.group(0).split()[0]),
    Pt(r"\b(?:an|the|any|no|positive|negative|significant|large|small|major|minor|strong|direct) affect (?:on|of)\b", "“{0}”", "Use “effect” (noun)."),
    Pt(r"\b(?:will|would|could|can|may|might|to|not|does|did|do) effect (?:the|a|an|our|their|its|his|her|how|whether|students|people|participants|results)\b", "“{0}”", "Did you mean “affect” (influence)? “Effect” as a verb means 'bring about'."),
    Pt(r"\bloose (?:the|a|my|your|his|her|their|our|weight|track|sight|interest|money|time|focus)\b", "“{0}”", "Use “lose”."),
    Pt(r"\balot\b", "“{0}”", "Use “a lot” (or “many”)."),
    Pt(r"\birregardless\b", "“{0}”", "Use “regardless”."),
    Pt(r"\bfor all intensive purposes\b", "“{0}”", "Use “for all intents and purposes”."),
    Pt(r"\bcould care less\b", "“{0}”", "Use “couldn't care less”."),
    Pt(r"\bprinciple (?:investigator|component|components|author|reason|cause|aim|source|investigators)\b", "“{0}”", "Use “principal” (main)."),
    Pt(r"\bcompliment (?:each other|one another)\b", "“{0}”", "Use “complement” (go well with)."),
    Pt(r"\bthere (?:own|car|house|work|results|data|study|studies|paper|findings|team|research|lives|children|parents|names?)\b", "“{0}”", "Use “their”."),
    Pt(r"\btheir (?:is|are|was|were|will be|has been|have been)\b", "“{0}”", "Use “there”."),
    Pt(r"\bwho['’]s (?:own|name|work|role|job|book|paper|study|research|mother|father|house)\b", "“{0}”", "Use “whose”."),
    Pt(r"\baccept for\b", "“{0}”", "Use “except for”."),
    Pt(r"\bweather or not\b", "“{0}”", "Use “whether or not”."),
    Pt(r"\badvice (?:you|him|her|them|us|me) to\b", "“{0}”", "Use “advise” (verb)."),
    Pt(r"\badverse to\b", "“{0}”", "Use “averse to” (unwilling)."),
    Pt(r"\bpeak(?:ed|s)? (?:my|your|his|her|their|our) (?:interest|curiosity)\b", "“{0}”", "Use “pique”."),
    Pt(r"\bsneak peak\b", "“{0}”", "Use “sneak peek”."),
    Pt(r"\bon accident\b", "“{0}”", "Use “by accident”."),
    Pt(r"\bsupposably\b", "“{0}”", "Use “supposedly”."),
    Pt(r"\bthe reason is because\b", "“{0}”", "Use “the reason is that”."),
    Pt(r"\bcomprised of\b", "“{0}”", "Use “composed of” or “comprises”."),
    Pt(r"\bbetween (?:you|him|her|them|us) and I\b", "“{0}”", "Use “… and me”."),
    Pt(r"\b(?:try|tries|tried) and (?=[a-z])", "“{0}”", "Use “try to”."),
    Pt(r"\bmoot point\b", None, None, lambda m, t, d, b: False),
    Pt(r"\bmute point\b", "“{0}”", "Use “moot point”."),
    Pt(r"\bwreck havoc\b", "“{0}”", "Use “wreak havoc”."),
    Pt(r"\bstatue of limitations\b", "“{0}”", "Use “statute of limitations”."),
    Pt(r"\bbaited breath\b", "“{0}”", "Use “bated breath”."),
    Pt(r"\bfree reign\b", "“{0}”", "Use “free rein”."),
    Pt(r"\btow the line\b", "“{0}”", "Use “toe the line”."),
    Pt(r"\bdamp(?:en)? squib\b", None, None, lambda m, t, d, b: False),
    Pt(r"\b(?:i|I)ts['’]", "“{0}”", "“its'” is never correct: use “its” or “it's”."),
    Pt(r"\b(?:lead) to (?:believe|be) (?=[a-z])", None, None, lambda m, t, d, b: False),
    Pt(r"\bhas lead\b|\bhave lead\b|\bhad lead\b", "“{0}”", "Past participle of 'lead' is “led”."),
    Pt(r"\bthan (?:I|we|they|he|she) (?:were|was) (?:able|going)\b", None, None, lambda m, t, d, b: False),
    Pt(r"\bdiffuse (?:the|a) (?:situation|tension|crisis|conflict)\b", "“{0}”", "Use “defuse”."),
    Pt(r"\b(?:farther|further) (?:more)\b", None, None, lambda m, t, d, b: False),
    Pt(r"\bperscription\b|\bpercription\b", "“{0}”", "Use “prescription”."),
    Pt(r"\bfor free\b", None, None, lambda m, t, d, b: False),
    Pt(r"\bimply (?:from|by)\b", "“{0}”", "Did you mean “infer from”?"),
    Pt(r"\bdiscreet (?:variable|variables|values?|data|time|mathematics|distribution|steps?|categories)\b", "“{0}”", "Use “discrete” (separate)."),
    Pt(r"\bdiscrete (?:inquiry|enquiry|manner|way|conversation)\b", "“{0}”", "Use “discreet” (tactful)."),
    Pt(r"\bstationary (?:shop|supplies|store|paper)\b", "“{0}”", "Use “stationery” (paper goods)."),
    Pt(r"\bcomplimentary (?:colou?rs|angles|strand|DNA|approach|methods)\b", "“{0}”", "Use “complementary”."),
    Pt(r"\bpreceed(?:s|ed|ing)?\b", "“{0}”", "Spell it “precede”."),
]


def _an(word):
    lw = word.lower()
    if re.match(r"^[A-Z]{2,6}s?$", word):
        return lw[0] in "aefhilmnorsx"
    if lw.startswith(("hour", "honest", "honor", "honour", "heir")):
        return True
    if lw.startswith(("unique", "unit", "union", "unif", "uniform", "univers", "unison", "unicorn", "unilateral", "unisex", "unidirect",
                      "use", "usu", "usa", "uti", "uri", "ure", "uro", "eu", "ewe", "one", "once", "ubiq", "ukr", "uto", "ufo",
                      "ukulele", "unanim", "uganda", "ural", "uranium", "urin", "utopia")):
        return False
    return lw[0] in "aeiou"


ART_RE = re.compile(r"(?<![\w.'’-])(a|an|A|An)\s+([A-Za-z][A-Za-z'’-]*)")
ART_SKIP_BEFORE = re.compile(r"(?:part|type|group|appendix|table|figure|section|class|plan|grade|level|phase|option|model|category|vitamin|point|step|item|size|column|row|exhibit|chapter|hepatitis|condition|series|panel|box|stage|wave|cluster|form|list|line|note)\s*$", I)


def m_grm02(doc, rc, s):
    if doc.words < 30:
        return None
    fs = []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in ART_RE.finditer(t):
            art, w = m.group(1), m.group(2)
            if len(w) == 1 or ART_SKIP_BEFORE.search(t[max(0, m.start() - 14):m.start()]):
                continue
            if art in ("A",) and m.start() > 0 and t[m.start() - 1:m.start()] not in (" ", "“", '"', "(") and m.start() != 0:
                continue
            if t[m.end():m.end() + 1] in (")", ".") and len(w) <= 2:
                continue
            want_an = _an(w)
            if want_an and art.lower() == "a":
                fs.append(_f(doc, b, m.start(), m.end(), "“%s %s”" % (art, w), "Use “%s %s”." % ("An" if art[0] == "A" else "an", w)))
            elif not want_an and art.lower() == "an":
                fs.append(_f(doc, b, m.start(), m.end(), "“%s %s”" % (art, w), "Use “%s %s”." % ("A" if art[0] == "A" else "a", w)))
    return M(len(fs), fs, "%d article mismatch(es)." % len(fs))


PARTICIPLES = {"went": "gone", "came": "come", "ran": "run", "did": "done", "saw": "seen", "wrote": "written", "took": "taken",
               "began": "begun", "drank": "drunk", "swam": "swum", "broke": "broken", "spoke": "spoken", "chose": "chosen",
               "ate": "eaten", "gave": "given", "fell": "fallen", "forgot": "forgotten", "rode": "ridden", "stole": "stolen",
               "drove": "driven", "grew": "grown", "knew": "known", "threw": "thrown", "flew": "flown", "blew": "blown",
               "sang": "sung", "rang": "rung", "sank": "sunk", "shook": "shaken", "froze": "frozen", "hid": "hidden", "bit": "bitten"}
AGREE = [
    Pt(r"\b(?:he|she|it) don['’]t\b", "“{0}”", "Use “doesn't”."),
    Pt(r"\b(?:they|we|you) was\b", "“{0}”", "Use “were”."),
    Pt(r"\b(?:I|he|she|it) were (?!to\b|able\b)(?=\w)", None, None, lambda m, t, d, b: False),
    Pt(r"\bthere(?:['’]s| is| was) (?:many|several|two|three|four|five|six|numerous|multiple|lots|a number of|various|few)\b", "“{0}”", "Use “there are / were”."),
    Pt(r"\b(?:these|those) (?:is|was|has)\b", "“{0}”", "Use “are / were / have”."),
    Pt(r"\bthis (?:are|were)\b", "“{0}”", "Use “this is / was” or “these are”."),
    Pt(r"\b(?:the|these|those|our|their) (?:results|findings|studies|participants|authors|researchers|students|patients|analyses|experiments|models|methods|samples) (?:shows|suggests|indicates|demonstrates|reveals|was|has|is)\b", "“{0}”", "Plural subject: use the plural verb."),
    Pt(r"\b(?:each|every|either|neither) (?:of the )?(?:\w+) (?:are|were|have)\b(?! been)", "“{0}”", "“Each / every” takes a singular verb."),
    Pt(r"\b(?:has|have|had|having) (" + "|".join(PARTICIPLES) + r")\b", "“{0}”", lambda m: "Use “%s %s”." % (m.group(0).split()[0], PARTICIPLES[m.group(1).lower()])),
    Pt(r"\bI is\b", "“{0}”", "Use “I am”.", None, 0),
    Pt(r"\b(?:he|she|it) have\b(?! to)", "“{0}”", "Use “has”.",
       lambda m, t, d, b: not re.search(r"\b(?:did|does|do|would|could|should|will|might|may|must|to|can|shall|let|had|make|made|help|see|saw|hear|watch|why|where|how|when|what|which|that|if|not)\s*$", t[max(0, m.start() - 12):m.start()], re.I)),
]

DOUBLE_NEG = [
    Pt(r"\b(?:don['’]t|doesn['’]t|didn['’]t|won['’]t|can['’]t|cannot|isn['’]t|aren['’]t|wasn['’]t|weren['’]t|never|did not|does not|do not|is not|was not) (?:\w+ )?(?:no|nothing|nobody|none|nowhere|never)\b(?! (?:longer|more|matter|less|one|doubt|other))",
       "“{0}”", "Keep one negative."),
    Pt(r"\b(?:can['’]t|couldn['’]t|cannot|could not) (?:hardly|barely|scarcely)\b", "“{0}”", "Use “can hardly”."),
    Pt(r"\bwithout no\b", "“{0}”", "Use “without any”."),
]

LATIN = [
    Pt(r"\be\.g(?![.\w])", "“{0}”", "Write “e.g.”."),
    Pt(r"\beg[.,](?=\s)", "“{0}”", "Write “e.g.”."),
    Pt(r"\bi\.e(?![.\w])", "“{0}”", "Write “i.e.”."),
    Pt(r"\bie[.,](?=\s)", "“{0}”", "Write “i.e.”."),
    Pt(r"\bet\.\s?al\.?", "“{0}”", "Write “et al.” (no full stop after et)."),
    Pt(r"\bet al(?![.\w])", "“{0}”", "Write “et al.”."),
    Pt(r"\betc(?![.\w])", "“{0}”", "Write “etc.”."),
    Pt(r"\betc\.\.", "“{0}”", "One full stop is enough after “etc.”."),
    Pt(r"\bsuch as\b[^.;:]{1,120}?,?\s+etc\.?", "“such as … etc.”", "“Such as” already implies more; drop “etc.”."),
    Pt(r"\b(?:e\.g\.|i\.e\.)\s*,?\s*(?:for example|that is)\b", "“{0}”", "Redundant: keep one."),
]

COMPAR = [
    Pt(r"\b(?:more|most) (?:better|worse|easier|harder|bigger|smaller|faster|stronger|weaker|higher|lower|larger|cheaper|best|worst|greater)\b", "“{0}”", "Drop “more/most”."),
    Pt(r"\b(?:more|most|very|less|least|quite|rather|somewhat) (?:unique|optimal|perfect|infinite|unanimous|impossible|absolute|universal|essential|ultimate|fatal|complete(?:ly)? unique)\b", "“{0}”", "This adjective is absolute; drop the modifier."),
    Pt(r"\bless (?:people|persons|students|participants|patients|items|words|errors|mistakes|cars|books|children|subjects|cases|samples|users|steps|times|calories|questions|studies|papers|members|employees|cells|trials|points|files|lines|pages)\b", "“{0}”", lambda m: "Use “fewer %s”." % m.group(0).split()[1]),
    Pt(r"\bfewer (?:time|money|water|data|information|research|work|evidence|energy|effort|attention|traffic|noise|space|power|stress|pain)\b", "“{0}”", lambda m: "Use “less %s”." % m.group(0).split()[1]),
    Pt(r"\b(?:more|less) (?:unique|optimal)\b", None, None, lambda m, t, d, b: False),
]

APOS = [
    Pt(r"\b(?:1[0-9]|20)\d0['’]s\b", "“{0}”", lambda m: "Use “%ss”." % m.group(0)[:4]),
    Pt(r"(?<![\w])['’]?\d0['’]s\b", "“{0}”", lambda m: "Use “%ss” (no apostrophe before s)." % m.group(0)[:-2].lstrip("'’"), lambda m, t, d, b: not re.match(r"(?:1[0-9]|20)", m.group(0))),
    Pt(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|several|many|multiple|some|few|all|\d+) [A-Z]{2,}['’]s\b", "“{0}”", "Plural: no apostrophe (“PhDs”, “CDs”).", None, 0),
    Pt(r"\blets (?:go|see|consider|look|assume|say|take|begin|start|turn|examine|review|discuss|move)\b", "“{0}”", "Use “let's” (let us)."),
    Pt(r"\b(?:apple|banana|tomato|potato|car|book|dog|cat|photo|ticket|product|item|computer)['’]s (?:for sale|are|were|available)\b", "“{0}”", "Plural, not possessive: drop the apostrophe."),
]

INTRO_WORDS = r"However|Therefore|Moreover|Furthermore|Nevertheless|Nonetheless|Consequently|Additionally|Finally|Firstly|Secondly|Lastly|Meanwhile|Similarly|Likewise|Otherwise|Accordingly|Indeed|Of course|For example|For instance|In addition|In contrast|In conclusion|In summary|In particular|On the other hand|As a result|In other words|To summarize|To summarise|Overall|Importantly|Interestingly|Notably|Surprisingly|Specifically|Unfortunately|Fortunately|Ultimately|Conversely"
HOWEVER_OK = {"much", "many", "large", "small", "often", "hard", "long", "far", "little", "few", "big", "good", "bad", "high", "low", "great", "strong", "slight", "minor"}


def _t_intro(x, doc, rc):
    m = re.match(r"^[“\"']?(%s)\s+(\w+)" % INTRO_WORDS, x["t"])
    if not m:
        return None
    if m.group(1) == "However" and m.group(2).lower() in HOWEVER_OK:
        return None
    if m.group(1) in ("Finally", "Ultimately", "Overall", "Indeed") and m.group(2).lower() in ("we", "the", "it", "this", "they", "i", "a"):
        return None  # short adverbs commonly run without a comma
    return ("No comma after “%s”." % m.group(1), "Write “%s, …”." % m.group(1), m.start(1), m.end(1))


WHICH_OK = {"of", "in", "on", "to", "by", "for", "with", "from", "at", "during", "through", "under", "after", "before", "upon",
            "into", "among", "between", "all", "some", "none", "each", "both", "many", "most", "one", "two", "several", "about",
            "against", "over", "within", "without", "via", "towards", "toward", "beyond", "behind", "below", "above", "across",
            "around", "near", "whereby", "and", "or", "but", "know", "knows", "knew", "decide", "decided", "choose", "chose",
            "determine", "determined", "see", "tell", "show", "shows", "ask", "asked", "wonder", "matter", "regardless"}


def _keep_which(m, t, doc, b):
    pre = t[:m.start()].rstrip()
    if not pre or pre[-1] in ",(—–:;\"“":
        return False
    w = re.search(r"(\w+)\s*$", pre)
    return bool(w) and w.group(1).lower() not in WHICH_OK


SPLICE = Pt(r"(?<=[a-z0-9\)]), (however|therefore|thus|hence|consequently|moreover|furthermore|nevertheless|nonetheless|otherwise|instead|accordingly|meanwhile) (?:we|they|it|this|that|these|those|the|our|there|he|she|I|you|a|an|its|their|his|her)\b",
            "Comma splice before “{0}”", lambda m: "Use a semicolon: “…; %s, …”." % m.group(1), None, 0)


def _keep_ly(m, t, doc, b):
    return m.group(1).lower() not in LX.LY_EXCEPT and len(m.group(1)) > 4


FRAG_RE = re.compile(r"^[“\"']?(Because|Although|Though|While|Whereas|Since|Unless|Which|Whereby|Especially|Such as|For example|Like|Including|Even though|So that|In order to)\b", I)


def _t_frag(x, doc, rc):
    m = FRAG_RE.match(x["t"])
    if m and x["n"] <= 14 and "," not in x["t"] and not x["t"].rstrip().endswith("?"):
        return ("Possible fragment starting with “%s”." % m.group(1), "Join it to the neighbouring sentence.", None, None)
    return None


# ------------------------------------------------------------------ mechanics
def _case_of(title):
    t = re.sub(r"^(?:(?:chapter|part|section|appendix)\s+)?(?:\d+(?:\.\d+)*|[IVXLC]+|[A-Z])[.):]?\s+", "", title.strip(), flags=I)
    ws = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", t)]
    if len(ws) < 3:
        return None
    letters = [c for c in t if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return "ALL CAPS"
    rest = [w for w in ws[1:] if len(w) >= 4 and not w.isupper()]
    if not rest:
        return None
    cap = sum(1 for w in rest if w[0].isupper())
    if cap / len(rest) >= 0.7:
        return "Title Case"
    if cap / len(rest) <= 0.25:
        return "Sentence case"
    return None


def m_mec07(doc, rc, s):
    hs = [(h, _case_of(doc.blocks[h]["t"])) for h in doc.headings]
    hs = [(h, c) for h, c in hs if c]
    if len(hs) < 3:
        return None
    cnt = Counter(c for _, c in hs)
    major = max(cnt, key=lambda c: (cnt[c], c))
    fs = [F(doc, "%s heading; most headings use %s." % (c, major), h, 0, len(doc.blocks[h]["t"]), fix="Convert to %s." % major)
          for h, c in hs if c != major]
    return M(100.0 * len(fs) / len(hs), fs, "Heading case: " + ", ".join("%s %d" % kv for kv in sorted(cnt.items(), key=lambda kv: -kv[1])) + ".")


def m_mec08(doc, rc, s):
    if len(doc.headings) < 2:
        return None
    ends = [(h, doc.blocks[h]["t"].rstrip()[-1:]) for h in doc.headings]
    dot = [h for h, e in ends if e == "." and not re.match(r"^(?:\d+\.|[IVXLC]+\.|chapter\s+\w+\.?)$", doc.blocks[h]["t"].strip(), I)]
    colon = [h for h, e in ends if e == ":"]
    fs = []
    if dot and len(dot) < len(ends) / 2:
        fs += [F(doc, "Heading ends with a full stop.", h, len(doc.blocks[h]["t"].rstrip()) - 1, len(doc.blocks[h]["t"].rstrip()), fix="Remove it.") for h in dot]
    fs += [F(doc, "Heading ends with a colon.", h, len(doc.blocks[h]["t"].rstrip()) - 1, len(doc.blocks[h]["t"].rstrip()), fix="Remove it.") for h in colon]
    return M(len(fs), fs, "%d heading(s) with stray terminal punctuation." % len(fs))


def _keep_dash(m, t, doc, b):
    a = t[max(0, m.start() - 1):m.start()]
    z = t[m.end():m.end() + 1]
    return not (a.isdigit() and z.isdigit())


def m_mec10(doc, rc, s):
    if doc.words < 50:
        return None
    fs = []
    sp = re.compile(r"(?<=\w) - (?=\w)")
    dd = re.compile(r"(?<=\w) ?-- ?(?=\w)")
    tight, spaced = [], []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in sp.finditer(t):
            if _keep_dash(m, t, doc, b):
                fs.append(_f(doc, b, m.start(), m.end(), "Spaced hyphen used as a dash.", "Use an em dash (—) or en dash (–)."))
        for m in dd.finditer(t):
            fs.append(_f(doc, b, m.start(), m.end(), "Double hyphen used as a dash.", "Use an em dash (—)."))
        tight += [(b, m.start(), m.end()) for m in re.finditer(r"\w—\w", t)]
        spaced += [(b, m.start(), m.end()) for m in re.finditer(r"\w —\s\w", t)]
    if tight and spaced:
        minority = tight if len(tight) < len(spaced) else spaced
        b, st, en = minority[0]
        fs.append(_f(doc, b, st, en, "Em dashes are spaced in %d places and closed up in %d." % (len(spaced), len(tight)), "Use one spacing style for em dashes."))
    return M(per1k(len(fs), doc.words), fs, "%d dash issue(s)." % len(fs))


ALLCAPS_RE = re.compile(r"(?<![\w-])([A-Z]{4,})(?![\w-])")


def m_mec12(doc, rc, s):
    if doc.words < 50:
        return None
    D = E.dictionaries()["both"]
    allowed = set(s["rules"]["CON-01"]["params"]["allowed"]) | LX.NOT_ACRONYMS
    fs = []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        letters = [c for c in t if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
            continue
        for m in ALLCAPS_RE.finditer(t):
            w = m.group(1)
            if w in allowed or w.lower() not in D or E.ROMAN_RE.match(w):
                continue
            fs.append(_f(doc, b, m.start(), m.end(), "“%s” in capitals." % w, "Write “%s” (use italics for emphasis)." % w.lower()))
    return M(per1k(len(fs), doc.words), fs, "%d shouted word(s)." % len(fs))


def m_mec13(doc, rc, s):
    fs = []
    for n, b in enumerate(doc.prose):
        t = doc.blocks[b]["t"].rstrip()
        if doc.block_words.get(b, 0) < 8:
            continue
        if re.search(r"[.!?:;…\"”’'\)\]]$", t):
            continue
        nxt = doc.prose[n + 1] if n + 1 < len(doc.prose) else None
        if nxt is not None and nxt == b + 1 and doc.blocks[nxt]["t"][:1].islower():
            continue  # paragraph continues (page break in PDFs)
        fs.append(F(doc, "Paragraph ends without punctuation.", b, max(0, len(t) - 12), len(t), fix="Finish the sentence."))
    return M(len(fs), fs, "%d paragraph(s) without end punctuation." % len(fs)) if doc.prose else None


MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)"


def m_mec16(doc, rc, s):
    D = E.dictionaries()["both"]
    fs = []
    for b in doc.textual:
        t = doc.blocks[b]["t"]
        for m in re.finditer(r"\b([a-z]{2,})- ([a-z]{2,})\b", t):
            if m.group(2) in ("and", "or", "to", "nor", "but"):
                continue
            j = m.group(1) + m.group(2)
            if j in D and not (m.group(1) in D and m.group(2) in D and len(m.group(1)) > 2 and len(m.group(2)) > 2):
                fs.append(_f(doc, b, m.start(), m.end(), "Broken word “%s”." % m.group(0), "Join it: “%s”." % j))
        for m in re.finditer(r"\b([a-z]{2,})-([a-z]{2,})\b", t):
            j = m.group(1) + m.group(2)
            p1, p2 = m.group(1), m.group(2)
            if j in D and len(j) >= 6 and not ((p1 in D or len(p1) <= 3) and p2 in D) and (p1 not in D and p2 not in D):
                fs.append(_f(doc, b, m.start(), m.end(), "Leftover line-break hyphen in “%s”." % m.group(0), "Join it: “%s”." % j))
    return M(len(fs), fs, "%d broken word(s)." % len(fs)) if doc.words >= 50 else None


# ------------------------------------------------------------------ evidence
def m_evd07(doc, rc, s):
    mx = rc["params"]["max_refs"]
    fs = []
    for kind, val, b, st, en in doc.cites:
        if kind == "num" and len(val) > mx:
            fs.append(F(doc, "One citation lists %d sources." % len(val), b, st, en, fix="Cite the few that matter most."))
    seen = set()
    for b in doc.textual:
        for m in E.AY_PAREN_RE.finditer(doc.blocks[b]["t"]):
            parts = [p for p in m.group(1).split(";") if re.search(r"\d{4}", p)]
            if len(parts) > mx and (b, m.start()) not in seen:
                seen.add((b, m.start()))
                fs.append(F(doc, "One citation lists %d sources." % len(parts), b, m.start(), m.end(), fix="Cite the few that matter most."))
    if not doc.cites:
        return None
    return M(len(fs), fs, "%d citation cluster(s) over %d sources." % (len(fs), mx))


STAT_RE = re.compile(r"\bp\s*[=<>≤≥]|\bCI\b|\bt\s*\(|\bF\s*\(|χ2|χ²|\bchi-square|\bz\s*=|\br\s*=|\bOR\s*=|\bβ\s*=|\bd\s*=|\bU\s*=|\bH\s*\(")


def m_evd08(doc, rc, s):
    if doc.words < 100:
        return None
    fs = []
    lead0, nolead = [], []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in re.finditer(r"\bp\s*([=<>≤])\s*(0?)\.(\d+)", t):
            if re.fullmatch(r"0+", m.group(3)):
                fs.append(_f(doc, b, m.start(), m.end(), "“%s” is impossible." % m.group(0), "Report “p < .001”."))
            (lead0 if m.group(2) else nolead).append((b, m.start(), m.end()))
        for m in re.finditer(r"\bp\s*=\s*([1-9]\d*(?:\.\d*)?)(?![\d.])", t):
            if float(m.group(1)) > 1:
                fs.append(_f(doc, b, m.start(), m.end(), "p value above 1.", "Check the value."))
    if lead0 and nolead:
        minority = lead0 if len(lead0) < len(nolead) else nolead
        b, st, en = minority[0]
        fs.append(_f(doc, b, st, en, "p values written both with and without a leading zero (%d vs %d)." % (len(lead0), len(nolead)),
                     "APA: no leading zero (p = .03); be consistent either way."))
    for k, x in enumerate(doc.sents):
        t = x["t"]
        if re.search(r"\bM\s*=\s*\d", t) and not re.search(r"\bSD\s*=|\bSE\s*=|±|\bSEM\b|\bCI\b", t):
            fs.append(F(doc, "Mean reported without SD or SE.", sent=k, fix="Add the standard deviation."))
        if re.search(r"\b(statistically significant|significant(?:ly)? (?:difference|differences|effect|effects|increase|decrease|correlation|association|improvement|reduction|higher|lower|greater|less|more))\b", t, I):
            nxt = doc.sents[k + 1]["t"] if k + 1 < len(doc.sents) else ""
            if not STAT_RE.search(t) and not STAT_RE.search(nxt) and not doc.sentence_cited(k):
                fs.append(F(doc, "“Significant” claimed with no statistic nearby.", sent=k, fix="Report the test, statistic, df and p."))
    return M(len(fs), fs, "%d statistical reporting issue(s)." % len(fs))


def m_evd11(doc, rc, s):
    if len(doc.refs) < 3:
        return None
    fs = []
    if doc.cite_style == "author-year":
        def key(r):
            import unicodedata
            f = r["first"] or ""
            return unicodedata.normalize("NFKD", f).encode("ascii", "ignore").decode().lower()
        for a, z in zip(doc.refs, doc.refs[1:]):
            if a["first"] and z["first"] and key(z) < key(a):
                fs.append(F(doc, "“%s” is listed after “%s” (not alphabetical)." % (z["first"], a["first"]), z["i"], 0, 0, fix="Sort the list by first author."))
    elif doc.cite_style == "numeric":
        nums = [r["num"] for r in doc.refs if r["num"] is not None]
        for a, z in zip(nums, nums[1:]):
            if z != a + 1:
                ri = next(r["i"] for r in doc.refs if r["num"] == z)
                fs.append(F(doc, "Reference [%d] follows [%d]." % (z, a), ri, 0, 0, fix="Number references consecutively."))
        if rc["params"]["numbered_by_first_citation"]:
            order, seen = [], set()
            for kind, val, b, st, en in doc.cites:
                if kind == "num":
                    for n in val:
                        if n not in seen:
                            seen.add(n)
                            order.append((n, b, st, en))
            top = 0
            for n, b, st, en in order:
                if n > top + 1 and top < len(doc.refs):
                    fs.append(F(doc, "[%d] is the first new citation here, but [%d] has not been cited yet." % (n, top + 1), b, st, en,
                                sev="note", fix="Number references in order of first citation (IEEE/Vancouver)."))
                    top = max(top, n)
                    continue
                top = max(top, n)
    else:
        return None
    return M(len(fs[:50]), fs[:50], "%d ordering issue(s) (%s style)." % (min(len(fs), 50), doc.cite_style))


def _keep_quote(m, t, doc, b):
    return True


def m_evd14(doc, rc, s):
    if doc.words < 100:
        return None
    fs = []
    for k, x in enumerate(doc.sents):
        m = re.search(r"[“\"]([^”\"]{20,})[”\"]", x["t"])
        if not m or len(WORD_RE.findall(m.group(1))) < 8:
            continue
        if doc.sentence_cited(k) or (k + 1 < len(doc.sents) and doc.sents[k + 1]["b"] == x["b"] and doc.sentence_cited(k + 1)):
            continue
        fs.append(F(doc, "Direct quotation without a citation.", s=x["s"] + m.start(), e=x["s"] + m.end(), sent=k, fix="Add the source and page number."))
    return M(len(fs), fs, "%d unsourced quotation(s)." % len(fs))


# ------------------------------------------------------------------ consistency
def m_con07(doc, rc, s):
    D = E.dictionaries()["both"]
    cap, low, first = Counter(), Counter(), {}
    SKIP = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
            "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "god", "lord", "sir", "madam",
            "miss", "lady", "mother", "father", "mum", "mom", "dad", "aunt", "uncle", "will", "mark", "rose", "grace", "hope", "bill",
            "frank", "may", "said", "colonel", "captain", "doctor", "king", "queen", "prince", "princess"}
    for x in doc.sents:
        toks = list(WORD_RE.finditer(x["t"]))
        for j, m in enumerate(toks):
            w = m.group(0)
            if j == 0 or len(w) < 4 or not w.isalpha():
                continue
            pre = x["t"][max(0, m.start() - 2):m.start()]
            if any(c in pre for c in ":“\"‘(—"):
                continue
            lw = w.lower()
            if lw in SKIP or lw not in D:
                continue
            if w[0].isupper() and w[1:].islower():
                cap[lw] += 1
                first.setdefault((lw, "c"), (x["b"], x["s"] + m.start(), x["s"] + m.end()))
            elif w.islower():
                low[lw] += 1
                first.setdefault((lw, "l"), (x["b"], x["s"] + m.start(), x["s"] + m.end()))
    fs = []
    for w in sorted(cap):
        if cap[w] >= 2 and low[w] >= 2:
            minority = "c" if cap[w] < low[w] else "l"
            b, st, en = first[(w, minority)]
            fs.append(_f(doc, b, st, en, "“%s” (%d) and “%s” (%d) both used mid-sentence." % (w.capitalize(), cap[w], w, low[w]),
                         "Pick one capitalization."))
    return M(len(fs), fs, "%d term(s) with mixed capitalization." % len(fs)) if doc.words >= 100 else None


LABELS = [("Fig.", r"\bFigs?\.\s*\d", "Figure", r"\bFigures?\s+\d"), ("Eq.", r"\bEqs?\.\s*\(?\d", "Equation", r"\bEquations?\s+\(?\d"),
          ("Sec.", r"\bSecs?\.\s*\d", "Section", r"\bSections?\s+\d"), ("Ch.", r"\bCh(?:ap)?\.\s*\d", "Chapter", r"\bChapters?\s+\d"),
          ("Tab.", r"\bTab\.\s*\d", "Table", r"\bTables?\s+\d"), ("Ref.", r"\bRefs?\.\s*\[?\d", "Reference", r"\bReferences?\s+\[?\d")]


def m_con08(doc, rc, s):
    fs, used = [], 0
    for a, ra, b, rb in LABELS:
        ha, hb = [], []
        for bl in doc.prose:
            t = doc.blocks[bl]["t"]
            ha += [(bl, m.start(), m.end()) for m in re.finditer(ra, t)]
            hb += [(bl, m.start(), m.end()) for m in re.finditer(rb, t)]
        if ha or hb:
            used += 1
        if ha and hb:
            minority = ha if len(ha) < len(hb) else hb
            bl, st, en = minority[0]
            fs.append(_f(doc, bl, st, en, "“%s” (%d) and “%s” (%d) both used." % (a, len(ha), b, len(hb)), "Use one form."))
    return M(len(fs), fs, "%d label(s) used in mixed forms." % len(fs)) if used else None


PRESENT_V = re.compile(r"\b(is|are|shows?|uses?|includes?|consists?|contains?|measures?|compares?|performs?|demonstrates?|indicates?|reveals?|selects?|collects?|recruits?)\b", I)
PAST_V = re.compile(r"\b(was|were|showed|used|included|consisted|contained|measured|compared|performed|demonstrated|indicated|revealed|recruited|collected|analy[sz]ed|conducted|obtained|selected|calculated|tested|found|assigned|completed|received|reported)\b", I)


def m_con09(doc, rc, s):
    bl = []
    for c in ("methods", "results"):
        x, _ = section_blocks(doc, c)
        bl += x
    if not bl:
        return None
    pres = past = 0
    fs = []
    for b in bl:
        for si in doc.sents_by_block.get(b, []):
            t = doc.sents[si]["t"]
            p, q = len(PRESENT_V.findall(t)), len(PAST_V.findall(t))
            pres += p
            past += q
            if p >= 2 and q == 0 and not re.match(r"^(Table|Figure|Fig\.)", t):
                fs.append(F(doc, "Present tense in Methods/Results.", sent=si, fix="Use the past tense for what was done and found."))
    if pres + past < 10:
        return None
    return M(100.0 * pres / (pres + past), fs[:30], "%d present vs %d past-tense verbs." % (pres, past))


def m_con10(doc, rc, s):
    i_hits, we_hits = [], []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        if t.lstrip().startswith(("“", '"')):
            continue
        i_hits += [(b, m.start(), m.end()) for m in E.FIRST_RE.finditer(t) if m.group(1) == "I"]
        we_hits += [(b, m.start(), m.end()) for m in re.finditer(r"\b(?:we|We|our|Our)\b", t)]
    if len(i_hits) < 3 or len(we_hits) < 3:
        return M(0, [], "Author voice is consistent.") if (i_hits or we_hits) else None
    minority = i_hits if len(i_hits) < len(we_hits) else we_hits
    b, st, en = minority[0]
    return M(1, [_f(doc, b, st, en, "Uses both “I” (%d) and “we/our” (%d)." % (len(i_hits), len(we_hits)), "Use one author voice.")],
             "I: %d, we/our: %d." % (len(i_hits), len(we_hits)))


NUMWORDS = "one two three four five six seven eight nine".split()
NUM_SKIP_AFTER = r"(?:%|percent|per|kg|g|mg|km|m|cm|mm|nm|s|ms|min|h|hr|hrs|hours?|minutes?|seconds?|days?|weeks?|months?|years?|times|x|and|or|to|of|out|in|on|by|am|pm|a\.m|p\.m|point|points|degrees?|°|dB|Hz|GB|MB|mL|L|cups?|tbsp|tsp|inch(?:es)?|feet|ft|miles?|lbs?|pounds?|dollars?|cents?)"
NUM_SKIP_BEFORE = re.compile(r"(?:Figure|Fig\.|Table|Section|Sec\.|Chapter|Ch\.|Eq\.|Equation|step|Step|Phase|phase|Experiment|Study|Appendix|version|Version|No\.|level|Level|grade|Grade|page|p\.|pp\.|Type|type|Group|group|Model|model|Hypothesis|Question|Item|item|Wave|Day|day|Week|week|Session|session|Trial|trial|Condition|condition|\d[.,]|[\d/:-])\s*$")


def m_con11(doc, rc, s):
    digits, words = [], []
    rd = re.compile(r"(?<![\w.,/:-])([1-9])\s+(?!%s\b)([a-z]{3,})" % NUM_SKIP_AFTER)
    rw = re.compile(r"\b(%s)\s+(?!(?:of|another|or|and|hand|day|way|by|another|thing|time|point|to)\b)([a-z]{3,})" % "|".join(NUMWORDS))
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in rd.finditer(t):
            if not NUM_SKIP_BEFORE.search(t[max(0, m.start() - 14):m.start()]):
                digits.append((b, m.start(), m.end()))
        for m in rw.finditer(t):
            if m.start() == 0 or t[m.start() - 1] in ".!?":  # sentence-initial words are required anyway
                continue
            words.append((b, m.start(), m.end()))
    total = len(digits) + len(words)
    if total < 4:
        return None
    if not digits or not words:
        return M(0.0, [], "Numbers under ten: consistently %s (%d)." % ("digits" if digits else "words", total))
    minority, mname, major = (digits, "digit", "words") if len(digits) < len(words) else (words, "word", "digits")
    fs = [_f(doc, b, st, en, "Small number as a %s; most are %s." % (mname, major), "Write it as %s (APA: words for one to nine)." % major[:-1]) for b, st, en in minority[:40]]
    return M(100.0 * len(minority) / total, fs, "Numbers under ten: %d as digits, %d as words." % (len(digits), len(words)))


def _ed1(a, b):
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False


def m_con12(doc, rc, s):
    D = E.dictionaries()["both"]
    cnt, first = Counter(), {}
    for x in doc.sents:
        toks = list(re.finditer(r"\b[A-Z][a-z]{2,}\b", x["t"]))
        for j, m in enumerate(toks):
            if m.start() == 0:
                continue
            w = m.group(0)
            if w.lower() in D:
                continue
            cnt[w] += 1
            first.setdefault(w, (x["b"], x["s"] + m.start(), x["s"] + m.end()))
    names = sorted([w for w, c in cnt.items() if c >= 2], key=lambda w: (-cnt[w], w))[:400]
    fs = []
    for i, a in enumerate(names):
        for b2 in names[i + 1:]:
            if a + "s" == b2 or b2 + "s" == a or not _ed1(a.lower(), b2.lower()):
                continue
            minor_ = b2 if cnt[b2] <= cnt[a] else a
            major_ = a if minor_ == b2 else b2
            b, st, en = first[minor_]
            fs.append(_f(doc, b, st, en, "“%s” (%d×) vs “%s” (%d×)." % (minor_, cnt[minor_], major_, cnt[major_]), "Check which spelling is right."))
    return M(len(fs), fs, "%d near-identical name pair(s)." % len(fs)) if doc.words >= 200 else None


def m_con13(doc, rc, s):
    if len(doc.refs) < 4:
        return None
    styles = Counter()
    tag = []
    for r in doc.refs:
        t = r["t"]
        if re.search(r"\((1[5-9]\d{2}|20\d{2})[a-z]?\)", t):
            st = "(Year)"
        elif re.search(r"[,.]\s(1[5-9]\d{2}|20\d{2})[a-z]?[.;,]", t):
            st = ", Year."
        else:
            st = None
        tag.append((r, st))
        if st:
            styles[st] += 1
    if len(styles) < 2:
        return M(0.0, [], "Reference years formatted consistently.") if styles else None
    major = max(styles, key=lambda k: (styles[k], k))
    fs = [F(doc, "Year formatted as %s; most entries use %s." % (st, major), r["i"], 0, 0, fix="Reformat to %s." % major) for r, st in tag if st and st != major]
    return M(100.0 * len(fs) / sum(styles.values()), fs, "Year styles: " + ", ".join("%s %d" % kv for kv in styles.items()) + ".")


# ------------------------------------------------------------------ integrity
def m_int12(doc, rc, s):
    rx = re.compile(r"\b(our (?:previous|prior|earlier|recent|own) (?:work|study|studies|paper|papers|research|findings|results|system|model)|"
                    r"we (?:previously|have previously|earlier|recently) (?:showed|shown|reported|demonstrated|proposed|found|described|published|introduced)|"
                    r"in our (?:previous|prior|earlier) (?:work|study|paper)|(?:my|our) (?:lab|laboratory|group|university|institution|department))\b", I)
    fs = []
    for b in doc.prose:
        for m in rx.finditer(doc.blocks[b]["t"]):
            fs.append(_f(doc, b, m.start(), m.end(), "Self-identifying: “%s”." % m.group(0), "Write in the third person (“Smith et al. showed…”)."))
    if "acknowledgements" in doc.present:
        fs.append(F(doc, "Acknowledgements are present in the manuscript.", doc.present["acknowledgements"], 0, 0, fix="Move them to the title page."))
    return M(len(fs), fs, "%d self-identifying item(s)." % len(fs))


def m_int14(doc, rc, s):
    seen, fs = {}, []
    for x in doc.sections:
        key = (x["level"], re.sub(r"^[\d.\s]+", "", x["title"].strip().lower()))
        if key in seen:
            fs.append(F(doc, "“%s” repeats an earlier heading." % x["title"], x["i"], 0, len(x["title"]), fix="Merge or rename."))
        seen.setdefault(key, x)
    return M(len(fs), fs, "%d duplicate heading(s)." % len(fs)) if doc.sections else None


def m_int15(doc, rc, s):
    refd, have = {}, {}
    for b in doc.prose:
        for m in re.finditer(r"\bAppendi(?:x|ces)\s+([A-Z]|\d+)\b", doc.blocks[b]["t"]):
            refd.setdefault(m.group(1), (b, m.start(), m.end()))
    for h in doc.headings:
        m = re.match(r"^(?:\d+\.?\s+)?Appendix\s+([A-Z]|\d+)\b", doc.blocks[h]["t"], I)
        if m:
            have.setdefault(m.group(1).upper() if m.group(1).isalpha() else m.group(1), h)
    if not refd and not have:
        return None
    fs = []
    for k in sorted(refd):
        if k not in have:
            b, st, en = refd[k]
            fs.append(_f(doc, b, st, en, "Appendix %s is referenced but not found." % k, "Add the appendix or fix the letter.", sev="major"))
    for k in sorted(have):
        if k not in refd:
            fs.append(F(doc, "Appendix %s is never referenced in the text." % k, have[k], 0, 0, fix="Refer to it where relevant."))
    return M(len(fs), fs, "%d appendix issue(s)." % len(fs))


ENC = [Pt("�", "Replacement character “�”.", "Re-type the damaged character.", None, 0),
       Pt(r"â€[™œ\x9d\"“”˜¦\x93\x94\x98\x99]|â€", "Mojibake “{0}”.", "Re-export as UTF-8; this was a quote or dash.", None, 0),
       Pt(r"Ã[\x80-\xbf©¨¤¶¼½§¡£]", "Mojibake “{0}”.", "Re-export as UTF-8; this was an accented letter.", None, 0),
       Pt(r"ï¬[\x80-\x8f]", "Mojibake “{0}”.", "This was a ligature (fi/fl).", None, 0),
       Pt(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", "Control character.", "Delete it.", None, 0)]
EMPTYB = [Pt(r"\(\s*\)|\[\s*\]|\{\s*\}|\(\s*[,;]+\s*\)|\[\s*[,;]+\s*\]", "Empty brackets “{0}”.", "Fill in or delete.", None, 0)]
ARTIFACTS = [Pt(r"\bPage \d+ of \d+\b", "“{0}”", "Delete the page marker."),
             Pt(r"\bDownloaded (?:from|by)\b", "“{0}”", "Delete the download stamp."),
             Pt(r"\bClick here\b", "“{0}”", "Delete or rewrite the link text."),
             Pt(r"\bAll rights reserved\b", "“{0}”", "Delete the copyright line from the body."),
             Pt(r"\[[A-Z]{1,3}\d{1,3}\]", "Word comment marker “{0}”.", "Delete the comment marker.", None, 0),
             Pt(r"\b(?:Formatted|Deleted|Commented|Moved \(insertion\)|Moved \(deletion\)):", "Track-changes residue “{0}”.", "Delete it.", None, 0),
             Pt(r"\bthis page (?:is )?intentionally left blank\b", "“{0}”", "Delete it."),
             Pt(r"\b(?:Scanned|Created|Generated|Made) (?:with|by) (?:CamScanner|Adobe Scan|Microsoft|Canva|Grammarly)\b", "“{0}”", "Delete the watermark text."),
             Pt(r"\bPress (?:Ctrl|Cmd|Command)\b", "“{0}”", "Delete the UI instruction.", None, 0),
             Pt(r"\bRetrieved (?:from|on)\b", "“{0}” in the body text", "Move source details into the reference list.")]
AI_LEFT = ["as an ai language model", "as a large language model", "as an ai assistant", "as an ai model", "i hope this helps",
           "i'm sorry, but i cannot", "i am sorry, but i cannot", "i cannot fulfill", "certainly! here", "sure! here", "sure, here is",
           "sure, here's", "here is a revised version", "here's a revised version", "here is the revised", "regenerate response",
           "my knowledge cutoff", "as of my last update", "as of my knowledge cutoff", "feel free to ask", "let me know if you need",
           "let me know if you'd like", "let me know if you want", "i'd be happy to help", "i would be happy to help",
           "great question", "chatgpt said", "copy code", "it's important to note that while", "in conclusion, while"]
PERSONAL = [Pt(r"[\w.+-]+@[\w-]+\.[A-Za-z][\w.]*", "Email address “{0}”.", "Remove it or move it to the title page.", None, 0),
            Pt(r"(?<![\d\w])(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)", "Phone number “{0}”.", "Remove it.", None, 0),
            Pt(r"\b\d{3}-\d{2}-\d{4}\b", "ID-like number “{0}”.", "Remove it.", None, 0)]
LINKS = [Pt(r"\b(?:https?://(?:localhost|127\.0\.0\.1|192\.168\.[\d.]+|10\.\d+\.[\d.]+)\S*|file:///?\S+|[A-Z]:\\\S+)", "Local address or path “{0}”.", "Replace with a public citation.", None, 0),
         Pt(r"\bhttps?://(?:bit\.ly|tinyurl\.com|goo\.gl|t\.co|ow\.ly|is\.gd|buff\.ly|rb\.gy)/\S+", "URL shortener “{0}”.", "Use the full address.", None, 0),
         Pt(r"\bhttp://(?!localhost|127\.)\S+", "Insecure http link “{0}”.", "Use https if available.", None, 0),
         Pt(r"(?<![\w/])(?:https://|www\.)\S+", "Bare URL in the text.", "Cite it in the reference list instead.", None, 0)]
DRAFT = [Pt(r"\bDRAFT\b", "“{0}”", "Remove the draft marker.", None, 0),
         Pt(r"\b(?:do not (?:cite|distribute|circulate|quote)|preliminary draft|work in progress|for internal use only|not for distribution)\b", "“{0}”", "Remove before submission."),
         Pt(r"\bCONFIDENTIAL\b", "“{0}”", "Remove the marking.", None, 0),
         Pt(r"\b(?:to be (?:completed|written|added|confirmed|determined|updated|decided)|fill (?:this )?in|insert (?:later|here)|add (?:later|more here|more detail here)|needs? (?:more work|rewriting|a citation|a reference)|rewrite this|check this|fix this|update this|expand this|cut this|delete this|move this)\b", "Note to self: “{0}”.", "Resolve and delete."),
         Pt(r"\b(?:NOTE TO SELF|NB:|N\.B\.:)", "Note to self: “{0}”.", "Resolve and delete.", None, 0),
         Pt(r"\?\?(?!\?)", "“??” marker.", "Resolve the query.", None, 0)]


def m_int09(doc, rc, s):
    rx = E.phrase_re(AI_LEFT)
    fs = []
    for b in range(doc.n):
        for m in rx.finditer(doc.blocks[b]["t"].replace("’", "'")):
            fs.append(_f(doc, b, m.start(), m.end(), "Chatbot text “%s”." % m.group(0), "Remove it; disclose AI use if your venue requires."))
    return M(len(fs), fs, "%d chatbot phrase(s)." % len(fs))


MEASURES = {
    "STR-09": m_str09, "STR-10": m_str10, "STR-11": m_str11, "STR-12": m_str12, "STR-13": m_str13, "STR-14": m_str14,
    "STR-15": m_str15, "STR-16": m_str16, "STR-17": m_str17, "STR-18": m_str18,
    "CLR-06": m_clr06,
    "CLR-07": sentrule(_t_of, "sentence(s) with long 'of' chains"),
    "CLR-08": sentrule(_t_paren, "sentence(s) overloaded with brackets"),
    "CLR-09": sentrule(_t_nums, "number-dense sentence(s)"),
    "CLR-10": sentrule(_t_vague, "vague 'this/that' opener(s)"),
    "CLR-11": m_clr11,
    "CLR-12": sentrule(_t_front, "front-loaded sentence(s)"),
    "STY-11": patrule([Pt(r"(?:^|(?<=[.!?]\s)|(?<=,\s))(?:There (?:is|are|was|were|has been|have been|exists?|seems?|appears?|remains?)|It (?:is|was) (?:\w+ )?(?:that|to|clear|important|necessary|possible|likely|evident|apparent|obvious|essential|interesting|worth|noteworthy|crucial))\b",
                          "Expletive “{0}”", "Start with the real subject.", None, 0)], "expletive construction(s)"),
    "STY-12": lexrule(BUZZ, "buzzword(s)"),
    "STY-13": lexrule(REDUNDANT, "redundant pair(s)"),
    "STY-14": lexrule(NEGATIVE, "negative construction(s)"),
    "STY-15": sentrule(_t_conj, "sentence(s) starting with a conjunction", unit="pct"),
    "STY-16": sentrule(_t_question, "rhetorical question(s)"),
    "STY-17": m_sty17,
    "STY-18": lexrule(INFORMAL, "informal word(s)"),
    "STY-19": patrule([Pt(r"\b(?:you|your|yours|yourself|yourselves)\b", "“{0}”", "Rephrase impersonally.", lambda m, t, d, b: not t.lstrip().startswith(("“", '"')))], "second-person use(s)"),
    "STY-20": m_sty20,
    "STY-21": lexrule(VAGUEW, "vague word(s)", fixfmt="%s"),
    "STY-22": m_sty22,
    "STY-23": patrule([Pt(r"[,!?][\"”’]\s*(?:\w+\s+)?(%s)\b" % TAGS, "Dialogue tag “{0}”", "Use “said” or an action beat.", None, I),
                       Pt(r"\b(?:said|asked|replied|answered|whispered|shouted)\s+(\w+ly)\b", "“{0}”", "Let the dialogue carry the tone; drop the adverb.",
                          lambda m, t, d, b: m.group(1).lower() not in LX.LY_EXCEPT)], "showy dialogue tag(s)"),
    "STY-24": patrule([Pt(r"\b(?:I|he|she|they|we|you|[A-Z][a-z]+) (?:%s)\b" % FILTER, "Filter: “{0}”", "Show the thing perceived directly.", None, 0)], "filter word(s)"),
    "STY-25": lexrule(GESTURES, "stock gesture(s)", fixfmt="%s"),
    "STY-26": sentrule(_t_partic, "participial opener(s)"),
    "GRM-01": patrule(CONFUSED, "confused word(s)", unit="count"),
    "GRM-02": m_grm02,
    "GRM-03": patrule(AGREE, "agreement or verb-form error(s)", unit="count"),
    "GRM-04": patrule(DOUBLE_NEG, "double negative(s)", unit="count"),
    "GRM-05": patrule(LATIN, "malformed Latin abbreviation(s)", unit="count", where="text"),
    "GRM-06": patrule([Pt(r"\bto (\w+ly) (?=[a-z]+\b)", "Split infinitive “{0}”", "Move the adverb if it reads better.",
                          lambda m, t, d, b: m.group(1).lower() not in LX.LY_EXCEPT)], "split infinitive(s)"),
    "GRM-07": sentrule(lambda x, d, rc: ("Ends with a preposition.", "Rephrase only if awkward.", None, None)
                       if re.search(r"\b(?:of|to|for|with|about|from|at|on|in|by|into|upon|over)\s*[.?!][\"”’]?$", x["t"]) else None, "sentence-final preposition(s)"),
    "GRM-08": patrule([Pt(r"(?<![\w.'’(-])i(?![\w.'’)-])", "Lower-case “i”.", "Write “I”.", lambda m, t, d, b: True, 0)], "lower-case 'i'", unit="count"),
    "GRM-09": patrule(COMPAR, "comparative error(s)", unit="count"),
    "GRM-10": patrule(APOS, "apostrophe error(s)", unit="count"),
    "GRM-11": sentrule(_t_intro, "missing comma(s) after an introductory word"),
    "GRM-12": patrule([Pt(r"\bwhich\b", "Restrictive “which”", "Use “that”, or add a comma before “which”.", _keep_which, 0)], "restrictive 'which'"),
    "GRM-13": patrule([SPLICE], "comma splice(s)"),
    "GRM-14": patrule([Pt(r"\b([a-z]+ly)-([a-z]+)\b", "“{0}”", lambda m: "Write “%s %s”." % (m.group(1), m.group(2)), _keep_ly, 0)], "hyphenated -ly compound(s)", unit="count"),
    "GRM-15": sentrule(_t_frag, "possible fragment(s)"),
    "MEC-07": m_mec07,
    "MEC-08": m_mec08,
    "MEC-09": mixrule([("...", r"(?<!\.)\.\.\.(?!\.)"), ("…", r"…"), (". . .", r"\. \. \.")], "Ellipses", min_total=2),
    "MEC-10": m_mec10,
    "MEC-11": mixrule([("serial comma", r"\b[a-z]{3,}, [a-z]{3,}, (?:and|or) [a-z]{3,}"),
                       ("no serial comma", r"\b[a-z]{3,}, [a-z]{3,} (?:and|or) [a-z]{3,}",
                        lambda m, t, d, b: not any(w in LX.STOP for w in re.findall(r"[a-z]+", m.group(0))[:2]))], "Lists of three", min_total=4),
    "MEC-12": m_mec12,
    "MEC-13": m_mec13,
    "MEC-14": mixrule([("Month D, YYYY", r"\b%s \d{1,2}(?:st|nd|rd|th)?, \d{4}\b" % MONTHS), ("D Month YYYY", r"\b\d{1,2} %s \d{4}\b" % MONTHS),
                       ("YYYY-MM-DD", r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"),
                       ("numeric dd/mm", r"\b\d{1,2}/\d{1,2}/(?:19|20)?\d{2}\b")], "Date styles", min_total=2),
    "MEC-15": mixrule([("3 pm", r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b"), ("3 p.m.", r"\b\d{1,2}(?::\d{2})?\s?[ap]\.m\."),
                       ("3 PM", r"\b\d{1,2}(?::\d{2})?\s?(?:AM|PM)\b"),
                       ("15:00", r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b(?!\s?(?:[ap]\.?m|AM|PM))")], "Time styles", min_total=2),
    "MEC-16": m_mec16,
    "EVD-07": m_evd07,
    "EVD-08": m_evd08,
    "EVD-09": patrule([Pt(r"(?<![\w.])\d+\.\d{4,}\b", "“{0}”", "Round to meaningful precision.", lambda m, t, d, b: not re.search(r"\bp\s*[=<]\s*$", t[max(0, m.start() - 6):m.start()]), 0),
                       Pt(r"(?<![\w.])\d+\.\d{3,}\s?%", "“{0}”", "Round the percentage.", None, 0)], "over-precise number(s)", unit="count"),
    "EVD-10": patrule([Pt(r"wikipedia|blogspot\.|wordpress\.com|medium\.com|quora\.com|reddit\.com|answers\.com|tumblr\.|substack\.com|chat\.openai\.com|chatgpt|gemini\.google|personal communication",
                          "Weak source: “{0}”.", "Replace with a primary or peer-reviewed source.")], "weak source(s)", unit="count", where="refs", min_words=0),
    "EVD-11": m_evd11,
    "EVD-12": lexrule(OVERCLAIM, "overclaim(s)", fixfmt="%s", msgfmt="Absolute claim “%s”"),
    "EVD-13": patrule([Pt(r"\b(?:as cited in|cited in|quoted in|as quoted by|as cited by)\b", "“{0}”", "Cite the original source.")], "secondary citation(s)", unit="count"),
    "EVD-14": m_evd14,
    "CON-07": m_con07,
    "CON-08": m_con08,
    "CON-09": m_con09,
    "CON-10": m_con10,
    "CON-11": m_con11,
    "CON-12": m_con12,
    "CON-13": m_con13,
    "INT-06": patrule(ENC, "encoding problem(s)", unit="count", where="all", min_words=0),
    "INT-07": patrule(EMPTYB, "empty bracket(s)", unit="count", where="all", min_words=0),
    "INT-08": patrule(ARTIFACTS, "export artifact(s)", unit="count", where="nonref", min_words=0),
    "INT-09": m_int09,
    "INT-10": patrule(PERSONAL, "personal data item(s)", unit="count", where="nonref", min_words=0),
    "INT-11": patrule(LINKS, "link issue(s)", unit="count", where="prose", min_words=0),
    "INT-12": m_int12,
    "INT-13": patrule(DRAFT, "draft note(s)", unit="count", where="all", min_words=0),
    "INT-14": m_int14,
    "INT-15": m_int15,
}
