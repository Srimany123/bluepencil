"""Review engine: measure a document, score it against the settings, decide a verdict.

Two stages:
  Doc(blocks, meta)  - everything that depends only on the file (sentences, sections,
                       citations, references, word counts). Built once, cached.
  review(doc, s)     - runs every enabled rule with the given settings. Deterministic.
"""
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict

import lexicon as LX
from rules import RULES, RULE_BY_ID, CATEGORIES, CAT_NAMES, CANON_SECTIONS, ENGINE_VERSION, merge_settings

WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z]+)*")
YEAR_RE = re.compile(r"(?<![\d/.-])(1[5-9]\d{2}|20\d{2})(?![\d/])")
FINDINGS_CAP = 150

# ------------------------------------------------------------------ dictionaries
_DICT = {}


def dictionaries():
    if _DICT:
        return _DICT
    def load(p):
        try:
            with open(p, encoding="utf8", errors="ignore") as f:
                return {l.strip().lower().replace("’", "'") for l in f if l.strip()}
        except OSError:
            return set()
    am = load("/usr/share/dict/american-english")
    br = load("/usr/share/dict/british-english")
    _DICT["american"], _DICT["british"], _DICT["both"] = am, br, am | br
    us_only, uk_only = am - br, br - am
    pairs = {}
    for w in sorted(us_only):
        for u in _us_to_uk(w):
            if u in uk_only:
                pairs[w] = u
                break
    _DICT["us2uk"] = pairs
    uk2us = {}
    for k2 in sorted(pairs):
        uk2us.setdefault(pairs[k2], k2)
    _DICT["uk2us"] = uk2us
    return _DICT


def _us_to_uk(w):
    out = []
    subs = [("or", "our"), ("iz", "is"), ("yz", "ys"), ("ense", "ence"), ("og", "ogue"), ("ment", "ement"),
            ("e", "ae"), ("e", "oe"), ("er", "re"), ("ed", "red"), ("am", "amme"), ("ay", "ey"), ("um", "ium"),
            ("ow", "ough"), ("old", "ould"), ("sk", "sc"), ("ire", "yre")]
    for a, b in subs:
        start = 0
        while True:
            i = w.find(a, start)
            if i < 0:
                break
            out.append(w[:i] + b + w[i + len(a):])
            start = i + 1
    m = re.match(r"^(.*[aeiou])l(ed|ing|er|ers)$", w)
    if m:
        out.append(m.group(1) + "ll" + m.group(2))
    m = re.match(r"^(.*)er(s|ed|ing)?$", w)
    if m:
        out.append(m.group(1) + "re" + (m.group(2) or "").replace("ed", "d").replace("ing", "ing"))
    return out


SUFFIXES = [("ies", "y"), ("ied", "y"), ("ier", "y"), ("iest", "y"), ("ily", "y"), ("iness", "y"), ("ves", "f"),
            ("ves", "fe"), ("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e"), ("es", ""), ("s", ""), ("er", ""),
            ("er", "e"), ("ers", ""), ("ers", "e"), ("est", ""), ("est", "e"), ("ly", ""), ("ly", "le"), ("ness", ""),
            ("ment", ""), ("ments", ""), ("able", ""), ("able", "e"), ("ably", ""), ("ability", "able"),
            ("ibility", "ible"), ("ally", ""), ("al", ""), ("ity", ""), ("ity", "e"), ("ism", ""), ("ist", ""),
            ("ists", ""), ("ize", ""), ("ise", ""), ("ization", "ize"), ("isation", "ise"), ("izations", "ize"),
            ("ful", ""), ("less", ""), ("ship", ""), ("wise", ""), ("ward", ""), ("wards", ""), ("'s", ""), ("s'", ""),
            ("ic", ""), ("ical", ""), ("ically", ""), ("ion", ""), ("ion", "e"), ("ions", ""), ("ions", "e"),
            ("ive", ""), ("ive", "e"), ("ivity", "ive"), ("ization", ""), ("isations", "ise")]
PREFIXES = ["un", "re", "pre", "non", "co", "sub", "multi", "inter", "over", "under", "anti", "semi", "post", "dis",
            "mis", "de", "bi", "tri", "micro", "macro", "meta", "hyper", "super", "auto", "cross", "counter", "out",
            "mid", "mini", "pseudo", "quasi", "neo", "proto", "trans", "ultra", "intra", "extra", "self", "cyber",
            "nano", "poly", "mono", "uni", "geo", "bio", "eco", "tele", "photo", "electro", "thermo", "hydro", "neuro"]


def known(w, D):
    if w in D:
        return True
    cands = [w]
    for p in PREFIXES:
        if w.startswith(p) and len(w) - len(p) >= 3:
            cands.append(w[len(p):])
    for c in cands:
        if c in D:
            return True
        for suf, rep in SUFFIXES:
            if c.endswith(suf) and len(c) - len(suf) >= 2:
                base = c[: -len(suf)] + rep
                if base in D:
                    return True
                if rep == "" and len(base) >= 3 and base[-1] == base[-2] and base[:-1] in D:
                    return True
    return False


def suggest(w, D, freq):
    letters = "abcdefghijklmnopqrstuvwxyz"
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    c = set()
    for a, b in splits:
        if b:
            c.add(a + b[1:])
            for ch in letters:
                c.add(a + ch + b[1:])
        if len(b) > 1:
            c.add(a + b[1] + b[0] + b[2:])
        for ch in letters:
            c.add(a + ch + b)
    good = [x for x in c if x != w and x in D]
    good.sort(key=lambda x: (-freq.get(x, 0), x))
    return good[:3]


# ------------------------------------------------------------------ text helpers
SPLIT_RE = re.compile(r"([.!?…]+)([\"'\)\]’”]*)(\s+)(?=[“\"'‘(\[]?[A-Z0-9])")


def split_sentences(text):
    out, start = [], 0
    for m in SPLIT_RE.finditer(text):
        if m.group(1) == ".":
            pre = text[start:m.start(1)]
            w = re.search(r"([A-Za-z][A-Za-z.]*)$", pre)
            if w:
                tok = w.group(1).lower().rstrip(".")
                if tok in LX.ABBR or len(tok) == 1 or re.fullmatch(r"(?:[a-z]\.)+[a-z]", tok):
                    continue
            if re.search(r"\d$", pre) and re.match(r"\d", text[m.end():]):
                continue
        out.append((start, m.end(2)))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    res = []
    for s, e in out:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            res.append((s, e))
    return res


def syllables(w):
    w = w.lower().strip("'’")
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def phrase_re(phrases):
    ps = sorted(set(phrases), key=lambda p: (-len(p), p))
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in ps) + r")(?![\w-])", re.I)


WORDY_RE = phrase_re(LX.WORDY)
HEDGE_RE = phrase_re(LX.HEDGES)
CLICHE_RE = phrase_re(LX.CLICHES)
INCL_RE = phrase_re(LX.INCLUSIVE)
PASSIVE_RE = re.compile(r"\b(am|is|are|was|were|be|been|being|is not|are not|was not|were not|isn't|aren't|wasn't|weren't)\s+"
                        r"(?:\w+ly\s+)?(\w+ed|" + "|".join(LX.PASSIVE_IRREG) + r")\b", re.I)
CANON_PATTERNS = [
    ("abstract", r"^(abstract|executive summary|summary of the (paper|article))$"),
    ("keywords", r"^(key ?words|index terms)$"),
    ("introduction", r"introduction|^background$|^motivation$|^overview$"),
    ("literature review", r"literature review|related work|prior work|previous work|state of the art"),
    ("methods", r"method|materials|experimental (setup|design|procedure)|^approach$|study design|^procedure|research design|^data and methods|methodology"),
    ("results", r"^results|results and|^findings$|^experiments?$|^evaluation|experimental results"),
    ("discussion", r"discussion"),
    ("limitations", r"limitation"),
    ("conclusion", r"conclu|^summary$|closing remarks|^summary and"),
    ("references", r"^(references|bibliography|works cited|literature cited|reference list|sources|citations)$"),
    ("acknowledgements", r"acknowledge?ments?"),
    ("funding", r"^funding|financial support|financial disclosure"),
    ("data availability", r"data (and (code|software) )?availability|availability of data|data access|code availability"),
    ("competing interests", r"conflicts? of interest|competing interests?|declaration of interests?|disclosures?$"),
    ("ethics", r"ethic|institutional review|informed consent"),
    ("author contributions", r"author(s'|s)? contributions?|credit author"),
    ("contents", r"^(table of )?contents$"),
    ("appendix", r"^appendi(x|ces)"),
]
CANON_COMPILED = [(c, re.compile(p, re.I)) for c, p in CANON_PATTERNS]
INLINE_LABEL = re.compile(r"^(abstract|key ?words|index terms|data (?:and code )?availability(?: statement)?|funding(?: statement)?|"
                          r"(?:conflicts? of interest|competing interests?)(?: statement)?|declaration of interests?|"
                          r"acknowledge?ments?|ethics(?: statement| approval)?|author contributions)\s*[:.—–-]", re.I)
NON_BODY = {"references", "acknowledgements", "keywords", "funding", "data availability", "competing interests", "ethics",
            "author contributions", "contents", "appendix", "abstract"}


def canon_of(title):
    t = re.sub(r"^(?:chapter|part|section)?\s*(?:\d+(?:\.\d+)*|[IVXLC]+|[A-Z])?[.):]?\s+", "", title.strip(), flags=re.I)
    t = re.sub(r"[:.]\s*$", "", t).strip().lower()
    if not t:
        t = title.strip().lower()
    return [c for c, rx in CANON_COMPILED if rx.search(t)]


NUM_CITE_RE = re.compile(r"\[(\d{1,4}(?:\s*[-–,]\s*\d{1,4})*)\]")
KEY_CITE_RE = re.compile(r"\[([A-Za-z][\w:-]*\d{2,4}[a-z]?(?:\s*,\s*[A-Za-z][\w:-]*)*)\]")
AY_PAREN_RE = re.compile(r"\(([^()]{0,300}?\b(?:1[6-9]\d{2}|20\d{2})[a-z]?\b[^()]{0,200}?)\)")
AY_PART_RE = re.compile(r"([A-Z][A-Za-z'’\-]+)(?:\s+(?:et al\.?|(?:and|&)\s+[A-Z][A-Za-z'’\-]+))?,?\s+((?:(?:1[6-9]\d{2}|20\d{2})[a-z]?(?:,\s*)?)+)")
AY_NARR_RE = re.compile(r"\b([A-Z][A-Za-z'’\-]+)(?:\s+et al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'’\-]+)?\s+\((1[6-9]\d{2}|20\d{2})[a-z]?(?:[,;][^)]*)?\)")
NOTE_CITE_RE = re.compile(r"(?<=[a-z][.,;:”\"])(\d{1,3})(?=\s|$)")
NOT_SURNAME = {"Table", "Figure", "Fig", "Section", "Chapter", "Eq", "Equation", "Appendix", "January", "February", "March",
               "April", "May", "June", "July", "August", "September", "October", "November", "December", "Spring", "Summer",
               "Fall", "Autumn", "Winter", "In", "On", "At", "By", "Since", "From", "During", "Until", "The", "This", "Before",
               "After", "Year", "Of", "And", "Or", "For", "To", "As", "Between", "Around", "About", "Circa", "Est", "Founded",
               "Version", "Vol", "Volume", "Issue", "Part", "Phase", "Wave", "Step", "Stage", "See", "Also", "Data", "Age", "N"}
CAP_LABEL_RE = re.compile(r"^(fig\.?|figure|table|tab\.)\s*(\d+)", re.I)
XREF_RE = re.compile(r"\b(fig(?:ure)?s?\.?|tables?|tab\.)\s*(\d+)((?:\s*(?:[-–,]|and|&)\s*\d+)*)", re.I)


def _expand(spec):
    nums = []
    for part in re.split(r"\s*(?:,|and|&)\s*", spec):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a < 200:
                nums.extend(range(a, b + 1))
            else:
                nums.extend([a, b])
        elif part.isdigit():
            nums.append(int(part))
    return nums


# ------------------------------------------------------------------ document model
class Doc:
    def __init__(self, blocks, meta, name="", sha=""):
        self.blocks, self.meta, self.name, self.sha = blocks, meta, name, sha
        self.mcache = {}
        self.last_changes = {}
        self.n = len(blocks)
        self.sec_of, self.ordinal = [], []
        sec, ordn = None, 0
        for b in blocks:
            if b["k"] in ("h", "title"):
                sec = b["t"]
                self.ordinal.append(None)
            else:
                ordn += 1
                self.ordinal.append(ordn)
            self.sec_of.append(sec)
        self.prose = [i for i, b in enumerate(blocks) if b["k"] == "para"]
        self.textual = [i for i, b in enumerate(blocks) if b["k"] in ("para", "cap")]
        self.sents, self.sents_by_block = [], defaultdict(list)
        self.block_words = {}
        freq = Counter()
        syl = 0
        for i in self.prose:
            t = blocks[i]["t"]
            ws = WORD_RE.findall(t)
            self.block_words[i] = len(ws)
            for w in ws:
                freq[w.lower()] += 1
                syl += syllables(w)
            for s, e in split_sentences(t):
                st = t[s:e]
                words = WORD_RE.findall(st)
                nums = re.findall(r"\b\d[\d,.]*\b", st)
                if not words:
                    continue
                d = {"b": i, "s": s, "e": e, "t": st, "n": len(words) + len(nums), "w": words}
                self.sents_by_block[i].append(len(self.sents))
                self.sents.append(d)
        for i in self.textual:
            if i not in self.block_words:
                self.block_words[i] = len(WORD_RE.findall(blocks[i]["t"]))
        self.freq = freq
        self.words = sum(self.block_words[i] for i in self.prose)
        self.syllables = syl
        self._sections()
        self._citations()
        self._references()
        self._figures()

    # -- sections & labelled statements
    def _sections(self):
        heads = [i for i, b in enumerate(self.blocks) if b["k"] == "h"]
        self.headings = heads
        self.sections = []
        for j, hi in enumerate(heads):
            lvl = self.blocks[hi]["l"] or 1
            own_end = heads[j + 1] if j + 1 < len(heads) else self.n
            tot_end = self.n
            for hk in heads[j + 1:]:
                if (self.blocks[hk]["l"] or 1) <= lvl:
                    tot_end = hk
                    break
            own = sum(self.block_words.get(i, 0) for i in range(hi + 1, own_end) if self.blocks[i]["k"] in ("para", "cap"))
            tot = sum(self.block_words.get(i, 0) for i in range(hi + 1, tot_end) if self.blocks[i]["k"] in ("para", "cap"))
            has_sub = j + 1 < len(heads) and heads[j + 1] < tot_end
            self.sections.append({"i": hi, "title": self.blocks[hi]["t"], "level": lvl, "own": own, "total": tot,
                                  "canon": canon_of(self.blocks[hi]["t"]), "has_sub": has_sub, "own_end": own_end})
        self.present = {}
        for sec in self.sections:
            for c in sec["canon"]:
                self.present.setdefault(c, sec["i"])
        self.abstract_words = None
        for i, b in enumerate(self.blocks):
            if b["k"] not in ("para", "cap"):
                continue
            m = INLINE_LABEL.match(b["t"])
            if m:
                for c in canon_of(m.group(1)):
                    self.present.setdefault(c, i)
                    if c == "abstract" and self.abstract_words is None:
                        self.abstract_words = len(WORD_RE.findall(b["t"][m.end():]))
        for sec in self.sections:
            if "abstract" in sec["canon"] and self.abstract_words is None and sec["own"]:
                self.abstract_words = sec["own"]

    # -- in-text citations
    def _citations(self):
        self.cites = []  # (kind, value, block, start, end)
        for i in self.textual:
            t = self.blocks[i]["t"]
            for m in NUM_CITE_RE.finditer(t):
                nums = _expand(m.group(1))
                if nums and max(nums) < 2000:
                    self.cites.append(("num", nums, i, m.start(), m.end()))
            for m in KEY_CITE_RE.finditer(t):
                self.cites.append(("key", m.group(1), i, m.start(), m.end()))
            spans = []
            for m in AY_PAREN_RE.finditer(t):
                for part in m.group(1).split(";"):
                    pm = AY_PART_RE.search(part)
                    if pm and pm.group(1) not in NOT_SURNAME:
                        for y in re.findall(r"1[6-9]\d{2}|20\d{2}", pm.group(2)):
                            self.cites.append(("ay", (pm.group(1), int(y)), i, m.start(), m.end()))
                            spans.append((m.start(), m.end()))
            for m in AY_NARR_RE.finditer(t):
                if m.group(1) in NOT_SURNAME:
                    continue
                if any(a <= m.start(2) < b for a, b in spans):
                    continue
                self.cites.append(("ay", (m.group(1), int(m.group(2))), i, m.start(), m.end()))
            for m in NOTE_CITE_RE.finditer(t):
                self.cites.append(("note", int(m.group(1)), i, m.start(), m.end()))
        self.cite_spans = defaultdict(list)
        for c in self.cites:
            self.cite_spans[c[2]].append((c[3], c[4]))
        kinds = Counter(c[0] for c in self.cites)
        self.cite_style = "numeric" if kinds["num"] and kinds["num"] >= kinds["ay"] else ("author-year" if kinds["ay"] else ("notes" if kinds["note"] else ("keys" if kinds["key"] else "none")))

    def sentence_cited(self, si):
        s = self.sents[si]
        return any(a < s["e"] and b > s["s"] for a, b in self.cite_spans.get(s["b"], []))

    # -- reference list
    def _references(self):
        self.refs = []
        for i, b in enumerate(self.blocks):
            if b["k"] != "ref":
                continue
            t = b["t"].strip()
            m = re.match(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.)])\s*", t)
            num = int(m.group(1) or m.group(2)) if m else None
            body = t[m.end():] if m else t
            years = [int(y) for y in YEAR_RE.findall(body)]
            sm = re.match(r"^\W*([A-Z][A-Za-z'’\-]+)", body)
            yi = YEAR_RE.search(body)
            names = set(re.findall(r"[A-Z][A-Za-z'’\-]{1,}", body[: yi.start()] if yi else body[:120]))
            self.refs.append({"i": i, "t": t, "num": num, "year": years[0] if years else None, "years": years,
                              "first": sm.group(1) if sm else None, "names": names,
                              "nd": bool(re.search(r"\bn\.\s?d\.|\bin press\b|\bforthcoming\b|\bundated\b", body, re.I))})
        if self.refs and all(r["num"] is None for r in self.refs) and self.cite_style == "numeric":
            for k, r in enumerate(self.refs, 1):
                r["num"] = k

    # -- figures & tables
    def _figures(self):
        self.captions = defaultdict(list)  # (kind, n) -> [block]
        for i, b in enumerate(self.blocks):
            if b["k"] == "cap":
                m = CAP_LABEL_RE.match(b["t"])
                if m:
                    kind = "Figure" if m.group(1).lower().startswith("fig") else "Table"
                    self.captions[(kind, int(m.group(2)))].append(i)
        self.xrefs = defaultdict(list)  # (kind, n) -> [(block, s, e)]
        for i in self.prose:
            for m in XREF_RE.finditer(self.blocks[i]["t"]):
                kind = "Figure" if m.group(1).lower().startswith("fig") else "Table"
                for n in _expand(m.group(2) + (m.group(3) or "")):
                    if 0 < n < 500:
                        self.xrefs[(kind, n)].append((i, m.start(), m.end()))

    # -- helpers for findings
    def loc(self, b):
        if b is None:
            return {}
        return {"b": b, "page": self.blocks[b].get("p"), "sec": self.sec_of[b], "para": self.ordinal[b], "kind": self.blocks[b]["k"]}

    def excerpt(self, b, s=None, e=None, width=280):
        t = self.blocks[b]["t"]
        if s is None:
            s = e = 0
        e = max(e, s)
        if len(t) <= width:
            return t, s, e
        mid = (s + e) // 2
        a = max(0, mid - width // 2)
        z = min(len(t), a + width)
        a = max(0, z - width)
        if a > 0:
            sp = t.find(" ", a)
            a = sp + 1 if 0 <= sp < s else a
        if z < len(t):
            sp = t.rfind(" ", e, z)
            z = sp if sp > e else z
        pre = "…" if a > 0 else ""
        x = pre + t[a:z] + ("…" if z < len(t) else "")
        return x, s - a + len(pre), min(e, z) - a + len(pre)


# ------------------------------------------------------------------ findings
def F(doc, msg, b=None, s=None, e=None, sev=None, fix=None, sent=None):
    f = {"msg": msg, "sev": sev}
    if sent is not None:
        sd = doc.sents[sent]
        b = sd["b"]
        if s is None:
            s, e = sd["s"], sd["e"]
            hs = he = None
        else:
            hs, he = s, e
            f["as"], f["ae"] = s, e
        # excerpt = the sentence; highlight the span if given
        x = sd["t"]
        if len(x) > 320:
            x, h1, h2 = doc.excerpt(b, hs if hs is not None else sd["s"], he if he is not None else sd["s"])
        else:
            h1 = (hs - sd["s"]) if hs is not None else 0
            h2 = (he - sd["s"]) if he is not None else 0
        f.update(doc.loc(b))
        f["x"], f["hs"], f["he"] = x, h1, h2
    elif b is not None:
        if s is not None and e is not None and e > s:
            f["as"], f["ae"] = s, e
        x, h1, h2 = doc.excerpt(b, s, e)
        f.update(doc.loc(b))
        f["x"], f["hs"], f["he"] = x, h1, h2
    if fix:
        f["fix"] = fix
    return f


def sent_at(doc, b, pos):
    for si in doc.sents_by_block.get(b, []):
        if doc.sents[si]["s"] <= pos < doc.sents[si]["e"] + 1:
            return si
    return None


def scan(doc, rx, blocks=None, fix=None, msg=None, sev=None):
    """Find every match of rx in the given blocks and return findings."""
    out = []
    for b in (doc.textual if blocks is None else blocks):
        for m in rx.finditer(doc.blocks[b]["t"]):
            si = sent_at(doc, b, m.start())
            text = m.group(0)
            fx = fix(text) if callable(fix) else fix
            mm = msg(text) if callable(msg) else (msg or "“%s”" % text)
            if si is not None:
                out.append(F(doc, mm, s=m.start(), e=m.end(), sent=si, fix=fx, sev=sev))
            else:
                out.append(F(doc, mm, b, m.start(), m.end(), fix=fx, sev=sev))
    return out


def per1k(n, words):
    return 1000.0 * n / words if words else 0.0


def M(value, findings=None, detail="", **extra):
    d = {"value": value, "findings": findings or [], "detail": detail}
    d.update(extra)
    return d


def fmt(v, nd=1):
    if v is None:
        return "–"
    if abs(v - round(v)) < 1e-9:
        return "{:,}".format(int(round(v)))
    return ("{:,.%df}" % nd).format(v)


# ------------------------------------------------------------------ measures
def m_str01(doc, rc, s):
    req = rc["params"]["required"]
    if not req:
        return None
    crit = set(rc["params"]["critical"])
    have = [c for c in req if c in doc.present]
    miss = [c for c in req if c not in doc.present]
    fs = [F(doc, "No “%s” section was found." % c.title(), sev="critical" if c in crit else None,
            fix="Add a heading named “%s” (or a labelled paragraph such as “%s:”)." % (c.title(), c.title())) for c in miss]
    det = "%d of %d expected sections found" % (len(have), len(req)) + ("; missing: " + ", ".join(c.title() for c in miss) if miss else ".")
    return M(100.0 * len(have) / len(req), fs, det)


ORDER = ["abstract", "introduction", "methods", "results", "discussion", "conclusion", "references"]


def m_str02(doc, rc, s):
    pos = [(doc.present[c], k, c) for k, c in enumerate(ORDER) if c in doc.present]
    if len(pos) < 3:
        return None
    pos.sort()
    fs, best = [], -1
    for p, k, c in pos:
        if k < best:
            later = ORDER[best]
            fs.append(F(doc, "“%s” appears after “%s”." % (c.title(), later.title()), p,
                        fix="Move “%s” before “%s”." % (c.title(), later.title())))
        best = max(best, k)
    seen = {}
    for x in doc.sections:
        for c in x["canon"]:
            if c in ORDER and c in seen and x["level"] == seen[c]["level"]:
                fs.append(F(doc, "“%s” appears again after “%s” already did." % (x["title"], seen[c]["title"]), x["i"], 0, len(x["title"]),
                            fix="Merge the two sections or rename one."))
            seen.setdefault(c, x)
    return M(len(fs), fs, "%d section(s) out of order or repeated." % len(fs) if fs else "Sections follow the conventional order.")


def m_str03(doc, rc, s):
    fs, prev = [], None
    for hi in doc.headings:
        lvl = doc.blocks[hi]["l"] or 1
        if prev is not None and lvl > prev + 1:
            fs.append(F(doc, "Heading jumps from level %d to level %d." % (prev, lvl), hi,
                        fix="Use a level-%d heading here or add the missing level." % (prev + 1)))
        prev = lvl
    if not doc.headings and doc.words > rc["params"]["min_words_for_headings"]:
        fs.append(F(doc, "No headings were found in a %s-word document." % fmt(doc.words), sev="major",
                    fix="Divide the text into titled sections."))
    return M(len(fs), fs, "%d heading(s) checked, %d issue(s)." % (len(doc.headings), len(fs)))


def m_str04(doc, rc, s):
    paras = [i for i in doc.prose if doc.block_words[i] > 0]
    if not paras:
        return None
    lim = rc["params"]["max_words"]
    long_ = [i for i in paras if doc.block_words[i] > lim]
    fs = [F(doc, "Paragraph of %s words (limit %d)." % (fmt(doc.block_words[i]), lim), i, 0, 0) for i in long_]
    return M(100.0 * len(long_) / len(paras), fs, "%d of %d paragraphs exceed %d words." % (len(long_), len(paras), lim))


def m_str05(doc, rc, s):
    el = [i for i in doc.prose if doc.block_words[i] >= 8]
    if len(el) < 5:
        return None
    single = [i for i in el if len(doc.sents_by_block[i]) == 1 and not doc.blocks[i]["t"].lstrip().startswith(("“", '"', "‘"))]
    fs = [F(doc, "Single-sentence paragraph.", i, 0, 0) for i in single]
    return M(100.0 * len(single) / len(el), fs, "%d of %d paragraphs are a single sentence." % (len(single), len(el)))


def m_str06(doc, rc, s):
    body = [x for x in doc.sections if not (set(x["canon"]) & NON_BODY)]
    if not body:
        return None
    top = min(x["level"] for x in body)
    secs = [x for x in body if x["level"] == top and x["total"] > 0]
    if len(secs) < 3:
        return None
    vals = [x["total"] for x in secs]
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    fs = []
    for x in secs:
        r = x["total"] / mean
        if r >= 2.5:
            fs.append(F(doc, "“%s” has %s words, %.1f× the average section." % (x["title"], fmt(x["total"]), r), x["i"],
                        fix="Consider splitting this section."))
        elif r <= 0.33:
            fs.append(F(doc, "“%s” has %s words, %.0f%% of the average section." % (x["title"], fmt(x["total"]), 100 * r), x["i"],
                        fix="Develop this section or merge it with a neighbour."))
    return M(sd / mean if mean else 0, fs, "%d top-level sections, average %s words (range %s–%s)." % (
        len(secs), fmt(mean, 0), fmt(min(vals)), fmt(max(vals))))


def m_str07(doc, rc, s):
    if doc.abstract_words is None:
        return None
    return M(doc.abstract_words, [], "Abstract is %s words." % fmt(doc.abstract_words))


def m_str08(doc, rc, s):
    return M(doc.words, [], "%s words of body text." % fmt(doc.words))


def fk_grade(words, sents, syl):
    if not words or not sents:
        return 0
    return 0.39 * words / sents + 11.8 * syl / words - 15.59


def m_clr01(doc, rc, s):
    if doc.words < 100 or not doc.sents:
        return None
    g = fk_grade(doc.words, len(doc.sents), doc.syllables)
    fs = []
    lo, hi = rc["lo"], rc["hi"]
    scored = []
    for i in doc.prose:
        if doc.block_words[i] >= 60 and doc.sents_by_block[i]:
            ws = [w for si in doc.sents_by_block[i] for w in doc.sents[si]["w"]]
            pg = fk_grade(len(ws), len(doc.sents_by_block[i]), sum(syllables(w) for w in ws))
            scored.append((pg, i))
    if g > hi:
        for pg, i in sorted(scored, key=lambda x: (-x[0], x[1]))[:8]:
            if pg > hi:
                fs.append(F(doc, "Paragraph reads at grade %.1f." % pg, i, 0, 0, fix="Shorten sentences and use plainer words."))
    elif g < lo:
        for pg, i in sorted(scored)[:8]:
            if pg < lo:
                fs.append(F(doc, "Paragraph reads at grade %.1f." % pg, i, 0, 0, fix="Combine short sentences; use precise terms."))
    fre = 206.835 - 1.015 * doc.words / len(doc.sents) - 84.6 * doc.syllables / doc.words
    return M(round(g, 2), fs, "Flesch-Kincaid grade %.1f (Flesch reading ease %.0f), target %s–%s." % (g, fre, fmt(lo), fmt(hi)))


def m_clr02(doc, rc, s):
    if len(doc.sents) < 5:
        return None
    lim = rc["params"]["max_words"]
    long_ = [k for k, x in enumerate(doc.sents) if x["n"] > lim]
    fs = [F(doc, "Sentence of %d words (limit %d)." % (doc.sents[k]["n"], lim), sent=k) for k in long_]
    return M(100.0 * len(long_) / len(doc.sents), fs, "%d of %d sentences exceed %d words." % (len(long_), len(doc.sents), lim))


def m_clr03(doc, rc, s):
    if len(doc.sents) < 5:
        return None
    lim = rc["params"]["max_words"]
    long_ = [k for k, x in enumerate(doc.sents) if x["n"] > lim]
    fs = [F(doc, "Sentence of %d words." % doc.sents[k]["n"], sent=k, fix="Break into two or three sentences.") for k in long_]
    return M(per1k(len(long_), doc.words), fs, "%d sentence(s) over %d words." % (len(long_), lim))


def m_clr04(doc, rc, s):
    if len(doc.sents) < 5:
        return None
    fs = []
    for k, x in enumerate(doc.sents):
        m = next((mm for mm in PASSIVE_RE.finditer(x["t"]) if mm.group(2).lower() not in LX.ADJ_PARTICIPLES), None)
        if m:
            fs.append(F(doc, "Passive: “%s”." % m.group(0), s=x["s"] + m.start(), e=x["s"] + m.end(), sent=k,
                        fix="Name who performs the action."))
    return M(100.0 * len(fs) / len(doc.sents), fs, "%d of %d sentences use the passive voice." % (len(fs), len(doc.sents)))


def m_clr05(doc, rc, s):
    if len(doc.sents) < 20:
        return None
    ns = [x["n"] for x in doc.sents]
    mean = sum(ns) / len(ns)
    sd = math.sqrt(sum((n - mean) ** 2 for n in ns) / len(ns))
    fs, k = [], 0
    while k < len(ns):
        j = k
        while j + 1 < len(ns) and max(ns[k:j + 2]) - min(ns[k:j + 2]) <= 5 and doc.sents[j + 1]["b"] == doc.sents[k]["b"]:
            j += 1
        if j - k + 1 >= 6:
            fs.append(F(doc, "%d sentences in a row of nearly the same length (%d–%d words)." % (j - k + 1, min(ns[k:j + 1]), max(ns[k:j + 1])),
                        sent=k, fix="Vary sentence length."))
            k = j + 1
        else:
            k += 1
    return M(round(sd, 2), fs, "Average sentence %.1f words, spread (SD) %.1f." % (mean, sd))


def m_phrases(rx, table=None, label=""):
    def run(doc, rc, s):
        if doc.words < 50:
            return None
        fs = scan(doc, rx, doc.prose,
                  fix=(lambda t: "Use: %s" % table.get(re.sub(r"\s+", " ", t.lower()), "")) if table else None,
                  msg=lambda t: "“%s”" % t)
        c = Counter(re.sub(r"\s+", " ", f["x"][f["hs"]:f["he"]].lower()) for f in fs)
        top = ", ".join("%s (%d)" % (w, n) for w, n in sorted(c.items(), key=lambda x: (-x[1], x[0]))[:5])
        return M(per1k(len(fs), doc.words), fs, "%d %s%s." % (len(fs), label, (": " + top) if top else ""))
    return run


def m_sty04(doc, rc, s):
    if doc.words < 100:
        return None
    fs = []
    for b in doc.prose:
        ss = doc.sents_by_block[b]
        firsts = [doc.sents[k]["w"][0].lower() if doc.sents[k]["w"] else "" for k in ss]
        k = 0
        while k < len(ss):
            j = k
            while j + 1 < len(ss) and firsts[j + 1] == firsts[k] and firsts[k]:
                j += 1
            if j - k + 1 >= 3:
                fs.append(F(doc, "%d sentences in a row start with “%s”." % (j - k + 1, doc.sents[ss[k]]["w"][0]), sent=ss[k]))
            k = j + 1
    return M(per1k(len(fs), doc.words), fs, "%d run(s) of repeated openings." % len(fs))


def m_sty05(doc, rc, s):
    if doc.words < 100:
        return None
    fs = []
    for k, x in enumerate(doc.sents):
        c = Counter(w.lower() for w in x["w"] if len(w) >= 4 and w.lower() not in LX.STOP)
        for w, n in sorted(c.items()):
            if n >= 3 or (n == 2 and len(w) >= 6 and doc.freq.get(w, 0) <= n + 1):
                pos = re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(w), x["t"], re.I)
                st = x["s"] + (pos.start() if pos else 0)
                fs.append(F(doc, "“%s” appears %d times in one sentence." % (w, n), s=st, e=st + len(w), sent=k,
                            fix="Replace one use with a pronoun or synonym."))
                break
    return M(per1k(len(fs), doc.words), fs, "%d sentence(s) with close repetition." % len(fs))


def m_sty06(doc, rc, s):
    if doc.words < 100:
        return None
    adv = Counter()
    fs = []
    for k, x in enumerate(doc.sents):
        a = [w for w in x["w"] if len(w) >= 5 and w.lower().endswith("ly") and w.lower() not in LX.LY_EXCEPT and not w[0].isupper()]
        adv.update(w.lower() for w in a)
        if len(a) >= 3:
            fs.append(F(doc, "%d adverbs in one sentence: %s." % (len(a), ", ".join(a)), sent=k))
    total = sum(adv.values())
    top = ", ".join("%s (%d)" % kv for kv in sorted(adv.items(), key=lambda kv: (-kv[1], kv[0]))[:6])
    return M(100.0 * total / doc.words, fs, "%d -ly adverbs%s." % (total, ("; most used: " + top) if top else ""))


FIRST_RE = re.compile(r"(?<!Type )(?<!Phase )(?<!Part )(?<!Class )(?<!Stage )(?<!Chapter )(?<!Section )(?<!Volume )(?<!War )(?<!Act )(?<!Grade )(?<!Level )\b(I|me|my|mine|myself)\b(?!\.\s?[A-Z]\.)")
CONTR_RE = re.compile(r"\b(\w+(?:n['’]t)|\w+['’](?:re|ve|ll|m|d)|(?:it|that|there|here|what|who|let|he|she|where)['’]s)\b", re.I)


def m_regex(rx, label, fixtxt=None, blocks="prose", min_words=50):
    def run(doc, rc, s):
        if doc.words < min_words:
            return None
        fs = scan(doc, rx, doc.prose if blocks == "prose" else doc.textual, fix=fixtxt)
        return M(per1k(len(fs), doc.words), fs, "%d %s." % (len(fs), label))
    return run


def m_sty10(doc, rc, s):
    if doc.words < 50:
        return None
    fs = scan(doc, INCL_RE, doc.textual, fix=lambda t: "Consider: %s" % LX.INCLUSIVE.get(t.lower(), ""))
    return M(len(fs), fs, "%d term(s) flagged." % len(fs))


DOUBLE_RE = re.compile(r"\b([A-Za-z]+)\s+(\1)\b", re.I)
DOUBLE_OK = {"had", "that", "is", "bye", "ha", "hah", "no", "so", "very", "tut", "knock", "yada", "blah", "bla", "la", "ta",
             "now", "far", "well", "there", "do", "dum", "chop", "pooh", "boo", "oh"}


def m_mec01(doc, rc, s):
    fs = []
    for b in doc.textual:
        t = doc.blocks[b]["t"]
        for m in DOUBLE_RE.finditer(t):
            if m.group(1).lower() in DOUBLE_OK:
                continue
            si = sent_at(doc, b, m.start())
            fs.append(F(doc, "Doubled word “%s %s”." % (m.group(1), m.group(2)), b, m.start(), m.end(), sent=si,
                        fix="Delete one “%s”." % m.group(1)) if si is not None else
                      F(doc, "Doubled word “%s %s”." % (m.group(1), m.group(2)), b, m.start(), m.end(), fix="Delete one."))
    return M(len(fs), fs, "%d doubled word(s)." % len(fs))


SPACE_RULES = [
    (re.compile(r"(?<=\S)  +(?=\S)"), "Double space.", "Use a single space.", True),
    (re.compile(r"(?<=[A-Za-z)\]])\s+([,;:!?])(?!\w)"), "Space before punctuation.", "Remove the space.", False),
    (re.compile(r"(?<=[a-z])\s+\.(?=\s|$)"), "Space before full stop.", "Remove the space.", False),
    (re.compile(r"(?<=[a-z]{2})[,;](?=[A-Za-z]{2})"), "No space after comma or semicolon.", "Add a space.", False),
    (re.compile(r"(?<=[a-z]{3})\.(?=[A-Z][a-z]{2})"), "No space after full stop.", "Add a space.", False),
]


def m_mec02(doc, rc, s):
    if doc.words < 50:
        return None
    fs = []
    for rx, msg, fix, needs_spacing in SPACE_RULES:
        if needs_spacing and not doc.meta.get("spacing_reliable"):
            continue
        for b in doc.prose:
            t = doc.blocks[b]["t"]
            for m in rx.finditer(t):
                ctxt = t[max(0, m.start() - 30): m.end() + 30]
                if re.search(r"https?://|www\.|@|\.(com|org|net|io|py|js|html)\b", ctxt):
                    continue
                si = sent_at(doc, b, m.start())
                e = max(m.end(), m.start() + 1)
                fs.append(F(doc, msg, b, m.start(), e, sent=si, fix=fix) if si is not None else F(doc, msg, b, m.start(), e, fix=fix))
    return M(per1k(len(fs), doc.words), fs, "%d spacing issue(s)%s." % (
        len(fs), "" if doc.meta.get("spacing_reliable") else " (double spaces not checked for this format)"))


ENUM_RE = re.compile(r"(?:(?<=\s)|^)[a-zA-Z0-9]{1,3}\)(?=\s)|(?<!\w)[:;]-?[()]")


def m_mec03(doc, rc, s):
    """Running balance across paragraphs: a bracket may open in one paragraph and close up to 3 later."""
    fs = []
    pairs = [("(", ")"), ("[", "]")]
    if rc["params"]["check_quotes"]:
        pairs.append(("“", "”"))
    for o, c in pairs:
        carry, opened_at = 0, None
        for n, b in enumerate(doc.prose):
            raw = doc.blocks[b]["t"]
            t = ENUM_RE.sub(" ", raw)
            for ch in t:
                if ch == o:
                    if carry == 0:
                        opened_at = (n, b, raw.find(o))
                    carry += 1
                elif ch == c:
                    if carry == 0:
                        p = raw.find(c)
                        fs.append(F(doc, "Closing “%s” with no opening “%s”." % (c, o), b, max(p, 0), max(p, 0) + 1,
                                    fix="Remove the stray “%s” or add the opening mark." % c))
                    else:
                        carry -= 1
            if carry and opened_at and n - opened_at[0] >= 3:
                _, ob, op = opened_at
                fs.append(F(doc, "“%s” opened here is never closed." % o, ob, max(op, 0), max(op, 0) + 1, fix="Close or remove the “%s”." % o))
                carry, opened_at = 0, None
        if carry and opened_at:
            _, ob, op = opened_at
            fs.append(F(doc, "“%s” opened here is never closed." % o, ob, max(op, 0), max(op, 0) + 1, fix="Close or remove the “%s”." % o))
    if rc["params"]["check_quotes"]:
        for b in doc.prose:
            t = doc.blocks[b]["t"]
            if t.count('"') % 2:
                pos = t.find('"')
                fs.append(F(doc, "Odd number of straight quotation marks.", b, pos, pos + 1, fix="Close the quotation."))
    fs.sort(key=lambda f: (f.get("b", 0), f.get("hs", 0)))
    return M(len(fs), fs, "%d unbalanced bracket(s) or quotation mark(s)." % len(fs))


LOWER_START_RE = re.compile(r"(?<![A-Za-z]\.)\b([A-Za-z]{2,})([.!?])\s+([a-z]{2,})\b")


def m_mec04(doc, rc, s):
    if doc.words < 50:
        return None
    fs = []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in LOWER_START_RE.finditer(t):
            if m.group(2) == "." and m.group(1).lower() in LX.ABBR:
                continue
            if m.group(3) in ("et", "al", "vs", "etc", "cf", "pp", "and", "or"):
                continue
            fs.append(F(doc, "“%s” starts a sentence in lower case." % m.group(3), b, m.start(3), m.end(3),
                        fix="Capitalise, or remove the full stop before it."))
    return M(per1k(len(fs), doc.words), fs, "%d sentence(s) begin in lower case." % len(fs))


REP_PUNCT_RE = re.compile(r"([!?,;:])\1+|\?!|!\?|(?<!\.)\.\.(?!\.)")


def m_mec05(doc, rc, s):
    fs = scan(doc, REP_PUNCT_RE, doc.prose, fix="Use a single mark.", msg=lambda t: "Repeated punctuation “%s”." % t)
    return M(len(fs), fs, "%d instance(s)." % len(fs))


def m_mec06(doc, rc, s):
    if doc.words < 50:
        return None
    D = dictionaries()[rc["params"]["dictionary"]]
    if not D:
        return None
    custom = {w.lower() for w in rc["params"]["custom_words"]} | LX.TECH_WORDS
    occ, first = Counter(), {}
    for b in doc.textual:
        t = doc.blocks[b]["t"]
        for m in WORD_RE.finditer(t):
            tok = m.group(0)
            if any(c.isupper() for c in tok):
                continue
            off = 0
            for part in tok.replace("’", "'").split("-"):
                p = part[:-2] if part.endswith("'s") else part
                if len(p) > 2 and p not in custom and not known(p, D):
                    occ[p] += 1
                    first.setdefault(p, (b, m.start() + off, m.start() + off + len(part)))
                off += len(part) + 1
    ign = rc["params"]["ignore_repeated"]
    bad = {w: n for w, n in occ.items() if n < ign}
    fs = []
    for w in sorted(bad, key=lambda w: (first[w][0], first[w][1])):
        b, st, en = first[w]
        sug = suggest(w, D, doc.freq) if len(w) <= 20 else []
        si = sent_at(doc, b, st)
        msg = "“%s” not in dictionary%s." % (w, (" (%d×)" % bad[w]) if bad[w] > 1 else "")
        fix = ("Did you mean: " + ", ".join(sug)) if sug else "Check the spelling or add to custom words."
        fs.append(F(doc, msg, b, st, en, sent=si, fix=fix) if si is not None else F(doc, msg, b, st, en, fix=fix))
    n = sum(bad.values())
    skipped = sum(1 for w, c in occ.items() if c >= ign)
    return M(per1k(n, doc.words), fs, "%d unknown word occurrence(s), %d distinct%s." % (
        n, len(bad), "; %d repeated term(s) treated as intentional" % skipped if skipped else ""))


def m_evd01(doc, rc, s):
    if doc.words < 200:
        return None
    n = len([c for c in doc.cites if c[0] != "note" or doc.cite_style == "notes"])
    fs = []
    if n == 0:
        fs.append(F(doc, "No in-text citations were found.", sev="major", fix="Cite your sources in the text."))
    else:
        for sec in doc.sections:
            if not set(sec["canon"]) & {"introduction", "literature review", "discussion"}:
                continue
            for i in range(sec["i"] + 1, sec["own_end"]):
                if doc.blocks[i]["k"] == "para" and doc.block_words.get(i, 0) >= 120 and not doc.cite_spans.get(i):
                    fs.append(F(doc, "%d-word paragraph in “%s” with no citation." % (doc.block_words[i], sec["title"]), i, 0, 0))
    return M(per1k(n, doc.words), fs, "%d citation(s), style looks %s." % (n, doc.cite_style))


def m_evd02(doc, rc, s):
    num = [c for c in doc.cites if c[0] == "num"]
    ay = [c for c in doc.cites if c[0] == "ay"]
    if not doc.refs and not num and not ay:
        return None
    fs = []
    if not doc.refs:
        fs.append(F(doc, "%d in-text citation(s) but no reference list was found." % (len(num) + len(ay)), sev="critical",
                    fix="Add a References section."))
        return M(100.0, fs, "No reference list.")
    if doc.cite_style == "numeric":
        refnums = {r["num"] for r in doc.refs if r["num"] is not None}
        cited = set()
        missing = {}
        for _, nums, b, st, en in num:
            for n in nums:
                cited.add(n)
                if n not in refnums and n not in missing:
                    missing[n] = (b, st, en)
        for n in sorted(missing):
            b, st, en = missing[n]
            fs.append(F(doc, "Citation [%d] has no matching reference entry." % n, b, st, en, sev="critical",
                        fix="Add reference [%d] to the list or correct the number." % n))
        unc = [r for r in doc.refs if r["num"] is not None and r["num"] not in cited]
        for r in unc:
            fs.append(F(doc, "Reference [%d] is never cited in the text." % r["num"], r["i"], 0, 0, fix="Cite it where it is relevant, or remove it."))
        total = len(cited | refnums) or 1
        return M(100.0 * (len(missing) + len(unc)) / total, fs,
                 "%d cited number(s), %d reference(s); %d unmatched citation(s), %d uncited reference(s)." % (
                     len(cited), len(doc.refs), len(missing), len(unc)))
    if doc.cite_style == "author-year":
        seen, unmatched = {}, []
        for _, (name, year), b, st, en in ay:
            key = (name, year)
            if key in seen:
                continue
            ok = any(name in r["names"] and year in r["years"] for r in doc.refs)
            seen[key] = ok
            if not ok:
                unmatched.append(key)
                fs.append(F(doc, "(%s, %d) has no matching reference entry." % key, b, st, en))
        uncited = []
        for r in doc.refs:
            if not r["first"] or r["year"] is None:
                continue
            if not any(n in r["names"] and y in r["years"] for (n, y) in seen):
                uncited.append(r)
                fs.append(F(doc, "Reference “%s … %d” is never cited." % (r["first"], r["year"]), r["i"], 0, 0))
        total = (len(seen) + len(doc.refs)) or 1
        return M(100.0 * (len(unmatched) + len(uncited)) / total, fs,
                 "%d distinct citation(s), %d reference(s); %d unmatched, %d uncited." % (
                     len(seen), len(doc.refs), len(unmatched), len(uncited)))
    return M(0.0, fs, "Citation style not machine-checkable (%s); %d reference(s) listed." % (doc.cite_style, len(doc.refs)))


CLAIM_RE = re.compile(
    r"\b(studies (?:have )?(?:show|shown|suggest|suggested|found|indicate|indicated|demonstrate|demonstrated)|"
    r"research (?:has )?(?:shows?|shown|suggests?|suggested|found|indicates?|demonstrated?)|"
    r"it (?:is|has been) (?:well[- ])?(?:known|established|documented|accepted|shown|reported|demonstrated)|"
    r"it is widely (?:believed|accepted|known|recognized|recognised)|"
    r"(?:many|most|several|numerous) (?:studies|researchers|scholars|experts|scientists|authors)|"
    r"experts (?:say|agree|believe)|scientists (?:say|agree|believe|have found)|"
    r"evidence (?:shows|suggests|indicates)|according to (?:research|studies|experts)|"
    r"(?:recent|previous|prior|earlier) (?:work|research|studies|literature) (?:has|have)|"
    r"(?:is|are) (?:commonly|generally|widely) (?:used|accepted|believed|known|regarded)|"
    r"(?:has|have) been (?:widely|extensively) (?:studied|used|reported|investigated))\b", re.I)


def m_evd03(doc, rc, s):
    if doc.words < 100:
        return None
    fs = []
    for b in doc.prose:
        ss = doc.sents_by_block[b]
        for j, k in enumerate(ss):
            m = CLAIM_RE.search(doc.sents[k]["t"])
            if not m:
                continue
            if doc.sentence_cited(k) or (j + 1 < len(ss) and doc.sentence_cited(ss[j + 1])):
                continue
            st = doc.sents[k]["s"] + m.start()
            fs.append(F(doc, "“%s” without a citation." % m.group(0), s=st, e=st + len(m.group(0)), sent=k,
                        fix="Cite the studies you mean, or narrow the claim."))
    return M(per1k(len(fs), doc.words), fs, "%d unsupported claim(s)." % len(fs))


def m_evd04(doc, rc, s):
    yrs = [r for r in doc.refs if r["year"]]
    if len(yrs) < 5:
        return None
    asof, age = s["asof_year"], rc["params"]["max_age"]
    old = [r for r in yrs if asof - r["year"] > age]
    fut = [r for r in yrs if r["year"] > asof]
    fs = [F(doc, "Published %d (%d years before %d)." % (r["year"], asof - r["year"], asof), r["i"], 0, 0, sev="note") for r in old]
    fs += [F(doc, "Year %d is after the review year %d." % (r["year"], asof), r["i"], 0, 0, sev="minor") for r in fut]
    ys = sorted(r["year"] for r in yrs)
    med = ys[len(ys) // 2]
    return M(100.0 * len(old) / len(yrs), fs, "%d of %d dated references are older than %d years; median year %d." % (
        len(old), len(yrs), age, med))


def m_evd05(doc, rc, s):
    if not doc.refs:
        return None
    prob = set()
    fs = []
    seen = {}
    for k, r in enumerate(doc.refs):
        if r["year"] is None and not r["nd"]:
            prob.add(k)
            fs.append(F(doc, "Reference has no year.", r["i"], 0, 0, fix="Add the publication year (or n.d.)."))
        key = re.sub(r"^\s*(\[\d+\]|\d+[.)])\s*", "", r["t"]).lower()
        key = re.sub(r"[^a-z0-9]", "", key)[:90]
        if len(key) > 25:
            if key in seen:
                prob.add(k)
                fs.append(F(doc, "Duplicate of an earlier reference entry.", r["i"], 0, 0, sev="major", fix="Remove the duplicate."))
            seen.setdefault(key, k)
        if rc["params"]["require_doi"] and not re.search(r"\bdoi\b|10\.\d{4,9}/|https?://", r["t"], re.I):
            prob.add(k)
            fs.append(F(doc, "No DOI or URL.", r["i"], 0, 0, fix="Add the DOI."))
    return M(100.0 * len(prob) / len(doc.refs), fs, "%d of %d reference entries have problems." % (len(prob), len(doc.refs)))


QUOTE_RE = re.compile(r"“([^”]{60,})”|\"([^\"]{60,})\"")


def m_evd06(doc, rc, s):
    lim = rc["params"]["max_words"]
    fs = []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        for m in QUOTE_RE.finditer(t):
            n = len(WORD_RE.findall(m.group(1) or m.group(2)))
            if n > lim:
                fs.append(F(doc, "Inline quotation of %d words." % n, b, m.start(), m.end(), fix="Set as a block quote."))
    return M(len(fs), fs, "%d long inline quotation(s)." % len(fs))


ACR_RE = re.compile(r"(?<![\w-])([A-Z][A-Z0-9&]*[A-Z][A-Z0-9]*|[A-Z][A-Z0-9]*[A-Z])(s)?(?![\w-])")
ROMAN_RE = re.compile(r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def m_con01(doc, rc, s):
    allowed = set(rc["params"]["allowed"])
    D = dictionaries()["both"]
    uses = defaultdict(list)  # acr -> [(b, st, en, is_def)]
    for b in doc.textual:
        t = doc.blocks[b]["t"]
        letters = [c for c in t if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
            continue
        for m in ACR_RE.finditer(t):
            a = m.group(1)
            if len(a) < 2 or len(a) > 8 or a in allowed or a in LX.NOT_ACRONYMS or ROMAN_RE.match(a) or a.isdigit():
                continue
            if sum(c.isupper() for c in a) < 2:
                continue
            if len(a) >= 4 and a.lower() in D:
                continue
            st, en = m.start(), m.end()
            if re.search(r"\b[A-Z]{2,}[\s,:;-]+$", t[max(0, st - 30):st]) or re.match(r"^[\s,:;-]+[A-Z]{2,}\b", t[en:en + 30]):
                continue  # part of an all-caps run: emphasis or a heading, not an acronym
            after = t[en:en + 90]
            before = t[max(0, st - 1):st]
            is_def = False
            if before == "(" and re.match(r"^\s*[);,]", t[en:en + 2] or ")"):
                pre = t[max(0, st - 120):st - 1]
                if len(WORD_RE.findall(pre)) >= 2:
                    is_def = True
            dm = re.match(r"^\s*\(([^()]{3,90})\)", after)
            if dm and len(WORD_RE.findall(dm.group(1))) >= 2 and not re.search(r"\d{4}", dm.group(1)):
                is_def = True
            if re.match(r"^\s*(?:,\s*)?(?:stands for|short for|is an acronym for|is short for)\b", after):
                is_def = True
            uses[a].append((b, st, en, is_def))
    fs, issues = [], 0
    for a in sorted(uses, key=lambda a: (uses[a][0][0], uses[a][0][1])):
        us = uses[a]
        defs = [u for u in us if u[3]]
        nondef = [u for u in us if not u[3]]
        if not defs:
            if nondef:
                issues += 1
                b, st, en, _ = nondef[0]
                fs.append(F(doc, "“%s” is used %d time(s) but never defined." % (a, len(nondef)), b, st, en,
                            fix="Spell it out at first use, or add it to the allowed list."))
            continue
        if nondef and (nondef[0][0], nondef[0][1]) < (defs[0][0], defs[0][1]):
            issues += 1
            b, st, en, _ = nondef[0]
            fs.append(F(doc, "“%s” is used before its definition (defined in ¶ %s)." % (a, doc.ordinal[defs[0][0]]), b, st, en,
                        fix="Move the definition to the first use."))
        if len(defs) > 1:
            issues += 1
            b, st, en, _ = defs[1]
            fs.append(F(doc, "“%s” is defined %d times." % (a, len(defs)), b, st, en, sev="note", fix="Define it once, at first use."))
    if not uses:
        return None
    return M(issues, fs, "%d distinct acronym(s); %d issue(s)." % (len(uses), issues))


def m_con02(doc, rc, s):
    d = dictionaries()
    us2uk, uk2us = d["us2uk"], d["uk2us"]
    us, uk, first = Counter(), Counter(), {}
    for b in doc.textual:
        for m in WORD_RE.finditer(doc.blocks[b]["t"]):
            w = m.group(0)
            if not w.islower():
                continue
            if w in us2uk:
                us[w] += 1
                first.setdefault(w, (b, m.start(), m.end()))
            elif w in uk2us:
                uk[w] += 1
                first.setdefault(w, (b, m.start(), m.end()))
    nu, nk = sum(us.values()), sum(uk.values())
    if nu + nk < 3:
        return None
    pref = rc["params"]["prefer"]
    if pref == "majority":
        pref = "american" if nu >= nk else "british"
    minority, mp = (uk, uk2us) if pref == "american" else (us, us2uk)
    fs = []
    for w in sorted(minority, key=lambda w: first[w]):
        b, st, en = first[w]
        fs.append(F(doc, "“%s” (%d×) is the %s spelling." % (w, minority[w], "British" if pref == "american" else "American"),
                    b, st, en, fix="Use “%s”." % mp[w]))
    share = 100.0 * sum(minority.values()) / (nu + nk)
    return M(share, fs, "%d American and %d British spellings; convention: %s." % (nu, nk, pref.title()))


def m_con03(doc, rc, s):
    uni, big, first = Counter(), Counter(), {}
    for x in doc.sents:
        ws = [w.lower() for w in x["w"]]
        for w in ws:
            uni[w] += 1
        for a, b in zip(ws, ws[1:]):
            big[(a, b)] += 1
    groups = {}
    for w, n in uni.items():
        if "-" in w and w.count("-") == 1:
            a, b = w.split("-")
            if len(a) >= 2 and len(b) >= 2 and uni.get(a + b):
                groups.setdefault(a + b, {})[w] = n
                groups[a + b][a + b] = uni[a + b]
    for (a, b), n in big.items():
        if a in LX.STOP or b in LX.STOP or len(a) < 3 or len(b) < 3 or len(a + b) < 6:
            continue
        if uni.get(a + b):
            groups.setdefault(a + b, {})[a + " " + b] = n
            groups[a + b][a + b] = uni[a + b]
    fs = []
    for key in sorted(groups):
        forms = groups[key]
        if len(forms) < 2:
            continue
        major = max(forms, key=lambda f: (forms[f], f))
        desc = ", ".join("“%s” (%d)" % (f, n) for f, n in sorted(forms.items(), key=lambda kv: (-kv[1], kv[0])))
        loc = None
        for f in sorted(forms, key=lambda f: forms[f]):
            if f == major:
                continue
            rx = re.compile(r"(?<![\w-])" + re.escape(f).replace(r"\ ", r"\s+") + r"(?![\w-])", re.I)
            for b in doc.prose:
                m = rx.search(doc.blocks[b]["t"])
                if m:
                    loc = (b, m.start(), m.end())
                    break
            if loc:
                break
        fs.append(F(doc, "Written several ways: %s." % desc, *(loc or (None,)), fix="Use “%s” throughout." % major))
    return M(len(fs), fs, "%d term(s) written inconsistently." % len(fs))


UNIT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)( ?)(kg|mg|µg|km|cm|mm|nm|µm|ms|µs|ns|Hz|kHz|MHz|GHz|kB|KB|MB|GB|TB|°C|°F|mV|kV|mA|kW|MW|kJ|mL|ml|Pa|kPa|MPa|dB|px)(?![\w])")


def m_con04(doc, rc, s):
    if doc.words < 50:
        return None
    pct_s, pct_w, th_c, th_p, u_sp, u_ns = [], [], [], [], [], []
    for b in doc.prose:
        t = doc.blocks[b]["t"]
        pct_s += [(b, m.start(), m.end()) for m in re.finditer(r"\d\s?%", t)]
        pct_w += [(b, m.start(), m.end()) for m in re.finditer(r"\d\s+(?:percent|per cent)\b", t)]
        th_c += [(b, m.start(), m.end()) for m in re.finditer(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d,])", t)]
        th_p += [(b, m.start(), m.end()) for m in re.finditer(r"(?<![\d.,])\d{5,}(?![\d,.])", t)]
        for m in UNIT_RE.finditer(t):
            (u_sp if m.group(2) else u_ns).append((b, m.start(), m.end()))
    fs = []
    for a, bb, what in ((pct_s, pct_w, "“%” and “percent”"), (th_c, th_p, "“10,000” and “10000” styles"),
                        (u_sp, u_ns, "spaced and unspaced units (“5 kg” and “5kg”)")):
        if a and bb:
            minority = a if len(a) < len(bb) else bb
            fs.append(F(doc, "Mixes %s (%d vs %d)." % (what, len(a), len(bb)), *minority[0], fix="Use one style throughout."))
    starts = 0
    for k, x in enumerate(doc.sents):
        if re.match(r"^\d", x["t"]) and not re.match(r"^\d+(?:\.[\dA-Za-z]+)*[.)]\s|^\d+(\.\d+)+\s+[A-Z]", x["t"]):
            starts += 1
            fs.append(F(doc, "Sentence starts with a numeral.", s=x["s"], e=x["s"] + len(re.match(r"^[\d.,%]+", x["t"]).group(0)),
                        sent=k, fix="Spell the number out or rephrase."))
    return M(len(fs), fs, "%d mixed style(s), %d sentence(s) starting with a numeral." % (len(fs) - starts, starts))


def m_con05(doc, rc, s):
    keys = set(doc.captions) | set(doc.xrefs)
    if not keys:
        return None
    fs, prob = [], set()
    for key in sorted(keys):
        kind, n = key
        if key in doc.xrefs and key not in doc.captions:
            prob.add(key)
            b, st, en = doc.xrefs[key][0]
            fs.append(F(doc, "%s %d is referenced but has no caption." % key, b, st, en, sev="major",
                        fix="Add the caption “%s %d: …” or fix the number." % key))
        if key in doc.captions and key not in doc.xrefs:
            prob.add(key)
            fs.append(F(doc, "%s %d has a caption but is never referenced in the text." % key, doc.captions[key][0], 0, 0,
                        sev="minor", fix="Refer to it in the text, e.g. “(see %s %d)”." % key))
    for kind in ("Figure", "Table"):
        nums = sorted(n for (k, n) in doc.captions if k == kind)
        if nums:
            for gap in sorted(set(range(1, nums[-1] + 1)) - set(nums)):
                if (kind, gap) not in doc.xrefs:
                    prob.add((kind, gap))
                    fs.append(F(doc, "%s numbering skips %d." % (kind, gap), sev="minor", fix="Renumber the %ss in order." % kind.lower()))
        order = sorted(((min((b, st) for b, st, _ in v), n) for (k, n), v in doc.xrefs.items() if k == kind))
        top = 0
        for (b, st), n in order:
            if n < top:
                prob.add((kind, n))
                fs.append(F(doc, "%s %d is first mentioned after %s %d." % (kind, n, kind, top), b, st, st + 1, sev="minor",
                            fix="Number figures and tables in order of first mention."))
            top = max(top, n)
    nf = len({k for k in keys if k[0] == "Figure"})
    nt = len(keys) - nf
    return M(100.0 * len(prob) / len(keys), fs, "%d figure(s), %d table(s); %d with problems." % (nf, nt, len(prob)))


def m_con06(doc, rc, s):
    st = cu = 0
    for b in doc.textual:
        t = doc.blocks[b]["t"]
        st += t.count('"') + len(re.findall(r"[A-Za-z]'[A-Za-z]", t))
        cu += t.count("“") + t.count("”") + len(re.findall(r"[A-Za-z]’[A-Za-z]", t))
    if st + cu < 4:
        return None
    minority = min(st, cu)
    fs = []
    if minority:
        sym = '"' if st < cu else "“"
        for b in doc.textual:
            p = doc.blocks[b]["t"].find(sym if sym == '"' else "“")
            if p < 0 and sym == '"':
                m = re.search(r"[A-Za-z]'[A-Za-z]", doc.blocks[b]["t"])
                p = m.start() + 1 if m else -1
            if p >= 0:
                fs.append(F(doc, "%d straight and %d curly quote marks." % (st, cu), b, p, p + 1, fix="Convert to %s quotes." % ("curly" if st < cu else "straight")))
                break
    return M(100.0 * minority / (st + cu), fs, "%d straight, %d curly." % (st, cu))


PLACEHOLDER_RES = [
    re.compile(r"\b(TODO|TBD|TBA|FIXME|XXX+|TKTK|TK)\b"),
    re.compile(r"\[(?:citation needed|cite|cit\.?|ref|reference|source|\?+|todo|tbd|insert[^\]]*|add[^\]]*|citation)\]", re.I),
    re.compile(r"\b(?:lorem ipsum|dolor sit amet)\b", re.I),
    re.compile(r"Error! (?:Reference source not found|Bookmark not defined|No text of specified style)\.?", re.I),
    re.compile(r"\b(?:Fig(?:ure)?\.?|Table|Section|Sec\.|Eq(?:uation)?\.?|Chapter|Appendix)\s*\?\?"),
    re.compile(r"\[\?\]|\?\?\?+|\(\?\?\)"),
    re.compile(r"\b(?:INSERT|ADD) (?:HERE|REF|CITATION|FIGURE|TABLE|NAME|DATE)\b"),
    re.compile(r"\[(?:Author|Name|Year|Date|Company|Title|Your Name)\]"),
    re.compile(r"<<[^<>]{1,40}>>"),
]


ROMAN_CTX_RE = re.compile(r"(chapter|part|book|vol\.?|volume|letter|act|scene|canto|section|no\.)\s*$", re.I)


def m_int01(doc, rc, s):
    fs = []
    for rx in PLACEHOLDER_RES:
        for b in range(doc.n):
            t = doc.blocks[b]["t"]
            for m in rx.finditer(t):
                if m.group(0).startswith("XX") and (doc.blocks[b]["k"] in ("h", "title") or ROMAN_CTX_RE.search(t[max(0, m.start() - 14):m.start()])
                                                    or re.match(r"[IVXLC]", t[m.end():m.end() + 1] or " ")):
                    continue
                si = sent_at(doc, b, m.start()) if doc.blocks[b]["k"] == "para" else None
                msg = "Placeholder or draft mark “%s”." % m.group(0)
                fs.append(F(doc, msg, s=m.start(), e=m.end(), sent=si, fix="Resolve before submission.") if si is not None
                          else F(doc, msg, b, m.start(), m.end(), fix="Resolve before submission."))
    fs.sort(key=lambda f: (f.get("b", 0), f.get("hs", 0)))
    return M(len(fs), fs, "%d placeholder(s) or draft mark(s)." % len(fs))


def m_int02(doc, rc, s):
    fs = []
    groups = defaultdict(list)
    for k, x in enumerate(doc.sents):
        if len(x["w"]) >= 10:
            groups[" ".join(w.lower() for w in x["w"])].append(k)
    dup_paras = set()
    for key, ks in sorted(groups.items(), key=lambda kv: kv[1][0]):
        if len(ks) > 1:
            fs.append(F(doc, "This sentence appears %d times (also ¶ %s)." % (len(ks), ", ".join(str(doc.ordinal[doc.sents[k]["b"]]) for k in ks[1:6])),
                        sent=ks[1], fix="Delete or rewrite the repetition."))
    # near-identical paragraphs: share of 6-word shingles
    sh = {}
    index = defaultdict(set)
    for b in doc.prose:
        ws = [w.lower() for w in WORD_RE.findall(doc.blocks[b]["t"])]
        if len(ws) < 40:
            continue
        S = {hash(" ".join(ws[i:i + 6])) for i in range(len(ws) - 5)}
        sh[b] = S
        for h in S:
            index[h].add(b)
    for b in sorted(sh):
        c = Counter()
        for h in sh[b]:
            if len(index[h]) <= 20:
                for o in index[h]:
                    if o < b:
                        c[o] += 1
        for o, n in sorted(c.items()):
            if n >= 0.6 * min(len(sh[b]), len(sh[o])) and (o, b) not in dup_paras:
                dup_paras.add((o, b))
                fs.append(F(doc, "Paragraph is %d%% the same as ¶ %s." % (round(100.0 * n / min(len(sh[b]), len(sh[o]))), doc.ordinal[o]),
                            b, 0, 0, fix="Remove the duplicate or merge the two paragraphs."))
                break
    return M(len(fs), fs, "%d repeated sentence group(s) or paragraph(s)." % len(fs))


EXEMPT_STUB = {"keywords", "acknowledgements", "funding", "data availability", "competing interests", "ethics",
               "author contributions", "contents", "references", "abstract"}


def m_int03(doc, rc, s):
    if not doc.sections:
        return None
    lim = rc["params"]["min_words"]
    fs = []
    for x in doc.sections:
        if x["has_sub"] or set(x["canon"]) & EXEMPT_STUB:
            continue
        if any(doc.blocks[i]["k"] in ("ref", "tbl") for i in range(x["i"] + 1, x["own_end"])):
            continue
        if x["own"] < lim:
            fs.append(F(doc, "“%s” has only %d word(s)." % (x["title"], x["own"]), x["i"], 0, len(x["title"]),
                        fix="Write it out or merge it with a neighbour."))
    return M(len(fs), fs, "%d section(s) under %d words." % (len(fs), lim))


def m_int04(doc, rc, s):
    req = rc["params"]["required"]
    if not req:
        return None
    miss = [c for c in req if c not in doc.present]
    fs = [F(doc, "No “%s” statement found." % c.title(), fix="Add a short “%s” statement." % c.title()) for c in miss]
    return M(100.0 * (len(req) - len(miss)) / len(req), fs, "%d of %d statements present." % (len(req) - len(miss), len(req)))


def m_int05(doc, rc, s):
    if not doc.meta.get("track_capable"):
        return None
    tc, cm = doc.meta.get("tracked_changes", 0), doc.meta.get("comments", 0)
    fs = []
    if tc:
        fs.append(F(doc, "%d unaccepted tracked change(s) in the file." % tc, fix="Review > Accept all / Reject all."))
    if cm:
        fs.append(F(doc, "%d comment(s) still in the file." % cm, fix="Delete all comments."))
    return M(tc + cm, fs, "%d tracked change(s), %d comment(s)." % (tc, cm))


MEASURES = {
    "STR-01": m_str01, "STR-02": m_str02, "STR-03": m_str03, "STR-04": m_str04, "STR-05": m_str05, "STR-06": m_str06,
    "STR-07": m_str07, "STR-08": m_str08,
    "CLR-01": m_clr01, "CLR-02": m_clr02, "CLR-03": m_clr03, "CLR-04": m_clr04, "CLR-05": m_clr05,
    "STY-01": m_phrases(WORDY_RE, LX.WORDY, "wordy phrase(s)"),
    "STY-02": m_phrases(HEDGE_RE, None, "hedge(s) or intensifier(s)"),
    "STY-03": m_phrases(CLICHE_RE, None, "cliché(s)"),
    "STY-04": m_sty04, "STY-05": m_sty05, "STY-06": m_sty06,
    "STY-07": m_regex(FIRST_RE, "first-person singular use(s)"),
    "STY-08": m_regex(CONTR_RE, "contraction(s)", lambda t: "Expand “%s”." % t),
    "STY-09": m_regex(re.compile(r"(?<![!=])!(?![=!])"), "exclamation mark(s)", "Use a full stop."),
    "STY-10": m_sty10,
    "MEC-01": m_mec01, "MEC-02": m_mec02, "MEC-03": m_mec03, "MEC-04": m_mec04, "MEC-05": m_mec05, "MEC-06": m_mec06,
    "EVD-01": m_evd01, "EVD-02": m_evd02, "EVD-03": m_evd03, "EVD-04": m_evd04, "EVD-05": m_evd05, "EVD-06": m_evd06,
    "CON-01": m_con01, "CON-02": m_con02, "CON-03": m_con03, "CON-04": m_con04, "CON-05": m_con05, "CON-06": m_con06,
    "INT-01": m_int01, "INT-02": m_int02, "INT-03": m_int03, "INT-04": m_int04, "INT-05": m_int05,
}
import measures_more  # noqa: E402  (needs the helpers above)
import analysis  # noqa: E402

MEASURES.update(measures_more.MEASURES)
MEASURES.update(analysis.MEASURES)
assert set(MEASURES) == set(RULE_BY_ID), set(RULE_BY_ID) ^ set(MEASURES)


# ------------------------------------------------------------------ scoring
def curve(rule, rc, v, k):
    mode = rule["mode"]
    if mode == "lower":
        g, b = rc["good"] / k, rc["bad"] / k
        eff = {"good": round(g, 3), "bad": round(b, 3)}
        if v <= g:
            return 100.0, eff
        if v >= b or b <= g:
            return 0.0, eff
        return 100.0 * (b - v) / (b - g), eff
    if mode == "higher":
        g = rc["good"]
        b = g - (g - rc["bad"]) / k
        eff = {"good": round(g, 3), "bad": round(b, 3)}
        if v >= g:
            return 100.0, eff
        if v <= b or g <= b:
            return 0.0, eff
        return 100.0 * (v - b) / (g - b), eff
    lo, hi, tol = rc["lo"], rc["hi"], rc["tol"] / k
    eff = {"lo": lo, "hi": hi, "tol": round(tol, 3)}
    if lo <= v <= hi:
        return 100.0, eff
    d = (lo - v) if v < lo else (v - hi)
    if rule["rel"]:
        edge = lo if v < lo else hi
        d = d / edge if edge else d
    if tol <= 0:
        return 0.0, eff
    return max(0.0, 100.0 * (1 - d / tol)), eff


VERDICTS = [("accept", "Accept"), ("minor", "Minor revision"), ("major", "Major revision"), ("reject", "Reject")]
VRANK = {v: i for i, (v, _) in enumerate(VERDICTS)}
VLABEL = dict(VERDICTS)


def fingerprint(doc_sha, s):
    blob = json.dumps({"doc": doc_sha, "settings": s, "engine": ENGINE_VERSION}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class Pending(Exception):
    def __init__(self, done, total):
        Exception.__init__(self, "pending")
        self.done, self.total = done, total


def review(doc, settings, deadline=None):
    import time as _time
    s = merge_settings(settings or {})
    k = s["strictness"]
    rules_out = []
    rules_full = []
    done = 0
    for rule in RULES:
        rid = rule["id"]
        rc = s["rules"][rid]
        cw = s["categories"][rule["cat"]]
        base = {"id": rid, "cat": rule["cat"], "name": rule["name"], "unit": rule["unit"], "mode": rule["mode"],
                "w": rc["w"], "sev": rc["sev"]}
        if not rc["on"] or rc["w"] == 0 or cw == 0:
            rules_out.append(dict(base, status="off"))
            continue
        key = rid + json.dumps([rc["params"], rc.get("lo"), rc.get("hi"), s["asof_year"] if rid == "EVD-04" else 0,
                                s["rules"]["CON-01"]["params"]["allowed"] if rid == "MEC-12" else 0], sort_keys=True)
        if key not in doc.mcache:
            if deadline is not None and _time.time() > deadline:
                raise Pending(done, sum(1 for r in RULES if s["rules"][r["id"]]["on"]))
            doc.mcache[key] = MEASURES[rid](doc, rc, s)
        done += 1
        out = doc.mcache[key]
        if out is not None:
            out = dict(out, findings=[dict(f) for f in out["findings"]])
        if out is None:
            rules_out.append(dict(base, status="na", detail="Not applicable to this document."))
            continue
        v = out["value"]
        sc, eff = curve(rule, rc, v, k)
        fs = out["findings"]
        for f in fs:
            if f["sev"] is None:
                f["sev"] = rc["sev"]
        sevc = Counter(f["sev"] for f in fs)
        rules_full.append((base, fs))
        rules_out.append(dict(base, status="scored", value=round(v, 3), score=round(sc, 1), eff=eff, detail=out["detail"],
                              n=len(fs), sevc=dict(sevc), findings=fs[:FINDINGS_CAP]))
    cats = []
    for cid, cname, q in CATEGORIES:
        rs = [r for r in rules_out if r["cat"] == cid and r["status"] == "scored"]
        cw = s["categories"][cid]
        tw = sum(r["w"] for r in rs)
        if not tw:
            sc = None
        elif s.get("aggregation", "rms") == "mean":
            sc = sum(r["w"] * r["score"] for r in rs) / tw
        else:  # weighted root-mean-square of shortfalls: a few bad rules can't hide behind many perfect ones
            sc = 100.0 - math.sqrt(sum(r["w"] * (100.0 - r["score"]) ** 2 for r in rs) / tw)
        cats.append({"id": cid, "name": cname, "q": q, "w": cw, "score": None if sc is None else round(sc, 1),
                     "rules": len(rs), "findings": sum(r["n"] for r in rs)})
    scored_cats = [c for c in cats if c["score"] is not None and c["w"] > 0]
    tcw = sum(c["w"] for c in scored_cats)
    overall = sum(c["w"] * c["score"] for c in scored_cats) / tcw if tcw else 0.0
    overall = round(overall, 1)
    # points lost per rule, in overall-score units
    catw = {c["id"]: c for c in cats}
    for r in rules_out:
        if r["status"] != "scored":
            continue
        c = catw[r["cat"]]
        rs_w = sum(x["w"] for x in rules_out if x["cat"] == r["cat"] and x["status"] == "scored")
        if not (rs_w and tcw):
            r["lost"] = 0.0
            continue
        if s.get("aggregation", "rms") == "mean":
            r["lost"] = round((100 - r["score"]) * r["w"] / rs_w * c["w"] / tcw, 2)
        else:  # how much the category score would rise if this rule were perfect, scaled to the overall
            others = sum(x["w"] * (100.0 - x["score"]) ** 2 for x in rules_out if x["cat"] == r["cat"] and x["status"] == "scored" and x is not r)
            r["lost"] = round(((100.0 - math.sqrt(others / rs_w)) - c["score"]) * c["w"] / tcw, 2)
    # verdict
    th = s["verdict"]
    if overall >= th["accept"]:
        base_v = "accept"
    elif overall >= th["minor"]:
        base_v = "minor"
    elif overall >= th["major"]:
        base_v = "major"
    else:
        base_v = "reject"
    verdict, reasons = base_v, []
    reasons.append("Score %s is in the %s band (%s)." % (fmt(overall), VLABEL[base_v], {
        "accept": "%s and above" % fmt(th["accept"]), "minor": "%s to %s" % (fmt(th["minor"]), fmt(th["accept"])),
        "major": "%s to %s" % (fmt(th["major"]), fmt(th["minor"])), "reject": "below %s" % fmt(th["major"])}[base_v]))
    sev_total = Counter()
    for r in rules_out:
        if r["status"] == "scored":
            sev_total.update(r["sevc"])
    crit = sev_total.get("critical", 0)
    g = s["gates"]
    def cap(to, why):
        nonlocal verdict
        if VRANK[verdict] < VRANK[to]:
            verdict = to
            reasons.append(why)
    if crit > g["max_critical_minor"]:
        cap("major", "Capped at Major revision: %d critical finding(s); Minor revision allows at most %s." % (crit, fmt(g["max_critical_minor"])))
    elif crit > g["max_critical_accept"]:
        cap("minor", "Capped at Minor revision: %d critical finding(s); Accept allows at most %s." % (crit, fmt(g["max_critical_accept"])))
    low = [c for c in scored_cats if c["score"] < g["category_floor"]]
    if low:
        cap("major", "Capped at Major revision: %s scored below the floor of %s." % (
            ", ".join("%s (%s)" % (c["name"], fmt(c["score"])) for c in low), fmt(g["category_floor"])))
    if crit and crit <= g["max_critical_accept"] and verdict == "accept":
        reasons.append("%d critical finding(s), within the %s allowed for Accept." % (crit, fmt(g["max_critical_accept"])))
    elif crit and len(reasons) == 1:
        reasons.append("%d critical finding(s) would also cap the verdict at %s." % (
            crit, "Major revision" if crit > g["max_critical_minor"] else "Minor revision"))
    if low and not any("floor" in r for r in reasons):
        reasons.append("%s also below the category floor of %s." % (", ".join(c["name"] for c in low), fmt(g["category_floor"])))
    scored = [r for r in rules_out if r["status"] == "scored"]
    required = sorted([r for r in scored if (r["sev"] in ("critical", "major") and r["score"] < 80) or r["sevc"].get("critical")],
                      key=lambda r: (-r["lost"], r["id"]))
    req_ids = {r["id"] for r in required}
    suggested = sorted([r for r in scored if r["id"] not in req_ids and r["score"] < 95], key=lambda r: (-r["lost"], r["id"]))
    strengths = sorted([r for r in scored if r["score"] >= 95 and r["id"] not in req_ids],
                       key=lambda r: (-(r["w"] * catw[r["cat"]]["w"]), r["id"]))
    def brief(r):
        return {"id": r["id"], "name": r["name"], "cat": r["cat"], "score": r["score"], "lost": r["lost"], "n": r["n"],
                "sev": r["sev"], "detail": r["detail"], "fix": RULE_BY_ID[r["id"]]["fix"], "crit": r["sevc"].get("critical", 0)}
    hist = [0] * 6
    for x in doc.sents:
        hist[min(5, x["n"] // 10)] += 1
    top_terms = [(w, n) for w, n in doc.freq.most_common(80) if w not in LX.STOP and len(w) > 3][:12]
    stats = {
        "words": doc.words, "sentences": len(doc.sents), "paragraphs": len(doc.prose), "headings": len(doc.headings),
        "references": len(doc.refs), "citations": len(doc.cites), "cite_style": doc.cite_style,
        "figures": len({n for (kk, n) in set(doc.captions) | set(doc.xrefs) if kk == "Figure"}),
        "tables": len({n for (kk, n) in set(doc.captions) | set(doc.xrefs) if kk == "Table"}),
        "avg_sentence": round(doc.words / len(doc.sents), 1) if doc.sents else 0,
        "grade": round(fk_grade(doc.words, len(doc.sents), doc.syllables), 1) if doc.sents else 0,
        "ease": round(206.835 - 1.015 * doc.words / len(doc.sents) - 84.6 * doc.syllables / doc.words, 1) if doc.sents and doc.words else 0,
        "reading_min": round(doc.words / 238.0, 1), "hist": hist, "top_terms": top_terms,
        "pages": doc.meta.get("pages"),
    }
    cat_by_id = {r["id"]: r for r in RULES}
    changes = analysis.rewrites(doc, rules_full, cat_by_id)
    doc.last_changes[fingerprint(doc.sha, s)] = changes
    changed_blocks = sorted({c["b"] for c in changes})
    shown = set(changed_blocks[:400])
    revisions = {"total": len(changes), "blocks": len(changed_blocks), "shown_blocks": len(shown),
                 "changes": [c for c in changes if c["b"] in shown],
                 "texts": {str(b): doc.blocks[b]["t"] for b in sorted(shown)},
                 "meta": {str(b): doc.loc(b) for b in sorted(shown)}}
    for r, fs in rules_full:
        r["rewrites"] = sum(1 for c in changes if c["rule"] == r["id"])
    heatmap = analysis.heat(doc, rules_full, s)
    stats["readability"] = analysis.readability(doc)
    return {
        "engine": ENGINE_VERSION,
        "revisions": revisions,
        "heat": heatmap,
        "fingerprint": fingerprint(doc.sha, s),
        "doc": {"name": doc.name, "sha": doc.sha, "format": doc.meta.get("format"), "pages": doc.meta.get("pages")},
        "settings": s,
        "overall": overall,
        "verdict": verdict, "verdict_label": VLABEL[verdict], "base_verdict": base_v, "reasons": reasons,
        "severity_totals": {sv: sev_total.get(sv, 0) for sv in ("critical", "major", "minor", "note")},
        "categories": cats,
        "rules": rules_out,
        "required": [brief(r) for r in required],
        "suggested": [brief(r) for r in suggested],
        "strengths": [brief(r) for r in strengths[:10]],
        "stats": stats,
    }


def last_changes(doc, settings):
    s = merge_settings(settings or {})
    fp = fingerprint(doc.sha, s)
    if fp not in doc.last_changes:
        review(doc, settings)
    return doc.last_changes[fp]


def outline(doc):
    return [{"t": x["title"], "l": x["level"], "w": x["total"], "c": x["canon"]} for x in doc.sections[:400]]


def suggest_profile(doc):
    p = doc.present
    if "abstract" in p and "references" in p:
        return "paper"
    dialog = sum(1 for i in doc.prose if doc.blocks[i]["t"].lstrip().startswith(("“", '"')))
    if doc.prose and dialog / len(doc.prose) > 0.15 and not doc.refs:
        return "fiction"
    if doc.words > 40000:
        return "book"
    if doc.refs or len(doc.headings) >= 3:
        return "chapter"
    return "general"


# ------------------------------------------------------------------ markdown export
def to_markdown(rep):
    s = rep["settings"]
    L = []
    L.append("# Review: %s" % rep["doc"]["name"])
    L.append("")
    L.append("**Verdict: %s** · overall score **%s / 100**" % (rep["verdict_label"], fmt(rep["overall"])))
    L.append("")
    L.append("Profile: %s · strictness ×%s · engine %s · review ID `%s`" % (
        s["profile"], fmt(s["strictness"], 2), rep["engine"], rep["fingerprint"]))
    L.append("")
    for r in rep["reasons"]:
        L.append("- " + r)
    L.append("")
    st = rep["stats"]
    L.append("%s words · %s sentences · %s paragraphs · grade %s · %s references · %s citations" % (
        fmt(st["words"]), fmt(st["sentences"]), fmt(st["paragraphs"]), fmt(st["grade"]), fmt(st["references"]), fmt(st["citations"])))
    L.append("")
    L.append("## Scores by category")
    L.append("")
    L.append("| Category | Weight | Score |")
    L.append("|---|---:|---:|")
    for c in rep["categories"]:
        L.append("| %s | %s | %s |" % (c["name"], fmt(c["w"], 2), "n/a" if c["score"] is None else fmt(c["score"])))
    L.append("")
    def items(title, lst):
        if not lst:
            return
        L.append("## " + title)
        L.append("")
        for i, r in enumerate(lst, 1):
            L.append("%d. **%s** (%s) — score %s, costs %s points. %s *Fix:* %s" % (
                i, r["name"], r["id"], fmt(r["score"]), fmt(r["lost"], 2), r["detail"], r["fix"]))
        L.append("")
    items("Required revisions", rep["required"])
    items("Suggested improvements", rep["suggested"])
    if rep["strengths"]:
        L.append("## Strengths")
        L.append("")
        for r in rep["strengths"]:
            L.append("- **%s**: %s" % (r["name"], r["detail"]))
        L.append("")
    L.append("## Detailed findings")
    L.append("")
    for c in rep["categories"]:
        rs = [r for r in rep["rules"] if r["cat"] == c["id"] and r["status"] == "scored"]
        if not rs:
            continue
        L.append("### %s — %s" % (c["name"], "n/a" if c["score"] is None else fmt(c["score"])))
        L.append("")
        for r in rs:
            L.append("#### %s %s — %s/100" % (r["id"], r["name"], fmt(r["score"])))
            L.append("")
            L.append(r["detail"])
            L.append("")
            for f in r["findings"][:50]:
                where = []
                if f.get("page"):
                    where.append("p. %s" % f["page"])
                if f.get("para"):
                    where.append("¶ %s" % f["para"])
                if f.get("sec"):
                    where.append("§ %s" % f["sec"])
                x = f.get("x")
                quote = ""
                if x:
                    hs, he = f.get("hs", 0), f.get("he", 0)
                    quote = " — “%s”" % (x[:hs] + ("**" + x[hs:he] + "**" if he > hs else "") + x[he:])[:300].replace("\n", " ")
                L.append("- [%s] %s%s%s%s" % (f["sev"], f["msg"], (" (" + ", ".join(where) + ")") if where else "", quote,
                                             (" *→ %s*" % f["fix"]) if f.get("fix") else ""))
            if r["n"] > 50:
                L.append("- … and %d more" % (r["n"] - 50))
            L.append("")
    rv = rep.get("revisions") or {}
    if rv.get("total"):
        L.append("## Suggested rewrites (%d)" % rv["total"])
        L.append("")
        L.append("```diff")
        for c in rv["changes"][:150]:
            L.append("- %s" % c["old"].replace("\n", " "))
            L.append("+ %s" % c["new"].replace("\n", " "))
            L.append("# %s %s: %s" % (c["rule"], c["name"], c["reason"]))
        L.append("```")
        L.append("")
    h = rep.get("heat") or {}
    if h.get("peaks"):
        L.append("## Flow")
        L.append("")
        L.append("- Peaks of interest: " + ", ".join("¶ %s (%s)" % (p["para"], fmt(p["int"], 0)) for p in h["peaks"]))
        L.append("- Lulls: " + ", ".join("¶ %s (%s)" % (p["para"], fmt(p["int"], 0)) for p in h["lulls"]))
        L.append("- Paragraphs needing the most work: " + ", ".join("¶ %s (quality %s)" % (p["para"], fmt(p["q"], 0)) for p in h["worst"]))
        L.append("")
        if h.get("sections"):
            L.append("| Section | Words | Quality | Interest |")
            L.append("|---|---:|---:|---:|")
            for x in h["sections"]:
                L.append("| %s | %s | %s | %s |" % (x["title"], fmt(x["w"]), fmt(x["q"], 0), "–" if x["int"] is None else fmt(x["int"], 0)))
            L.append("")
    rd = (rep.get("stats") or {}).get("readability") or {}
    if rd:
        L.append("## Metrics")
        L.append("")
        for k, v in rd.items():
            L.append("- %s: %s" % (k, v))
        L.append("")
    L.append("---")
    L.append("Same file + same settings + engine %s always reproduces review ID `%s`." % (rep["engine"], rep["fingerprint"]))
    return "\n".join(L)
