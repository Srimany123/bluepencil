"""The rule catalog and review profiles.

Every rule produces one measured value. The value is turned into a 0-100 score
with a fixed, documented curve:

  lower   lower is better.  score 100 at or below `good`, 0 at or above `bad`,
          straight line in between.
  higher  higher is better. score 100 at or above `good`, 0 at or below `bad`.
  range   inside [lo, hi] scores 100. Outside, the score falls by a straight
          line and reaches 0 at `tol` beyond the edge (`tol` is a fraction of
          the edge when `rel` is true, otherwise absolute units).

Strictness k (0.6 to 1.8, 1.0 = standard) tightens every curve:
  lower:  good/k, bad/k
  higher: good stays, the band (good - bad) shrinks to (good - bad)/k
  range:  tol/k
No randomness and no AI: the same file and the same settings always give the
same numbers.
"""
import copy

ENGINE_VERSION = "2.0.0"

CATEGORIES = [
    ("STR", "Structure & organization", "Are the expected parts there, in a sensible order, with sound proportions?"),
    ("CLR", "Clarity & readability", "Can the intended reader follow the prose without strain?"),
    ("STY", "Style & concision", "Is the writing direct, economical and free of filler?"),
    ("MEC", "Mechanics & spelling", "Is the text free of typos, doubled words and punctuation slips?"),
    ("GRM", "Grammar & usage", "Are the sentences grammatical and the words the right ones?"),
    ("FLW", "Flow & cohesion", "Does each paragraph and sentence follow from the one before?"),
    ("EVD", "Evidence & citations", "Are claims supported, and do citations and references line up?"),
    ("CON", "Consistency & terminology", "Are terms, spellings, numbers and cross-references handled the same way throughout?"),
    ("INT", "Completeness & integrity", "Is this a finished manuscript, with no placeholders, stubs, duplicates or open edits?"),
]
CAT_NAMES = {c[0]: c[1] for c in CATEGORIES}

SEVERITIES = ["critical", "major", "minor", "note"]

CANON_SECTIONS = [
    "abstract", "keywords", "introduction", "literature review", "methods", "results", "discussion",
    "limitations", "conclusion", "references", "acknowledgements", "funding", "data availability",
    "competing interests", "ethics", "author contributions", "contents", "appendix",
]


def R(id, cat, name, mode, unit, desc, why, fix, sev="minor", w=1.0, good=None, bad=None,
      lo=None, hi=None, tol=None, rel=False, params=None):
    return dict(id=id, cat=cat, name=name, mode=mode, unit=unit, desc=desc, why=why, fix=fix,
                sev=sev, w=w, good=good, bad=bad, lo=lo, hi=hi, tol=tol, rel=rel, params=params or {})


def P(type, label, default, **kw):
    d = dict(type=type, label=label, default=default)
    d.update(kw)
    return d


RULES = [
    # ------------------------------------------------------------ structure
    R("STR-01", "STR", "Required sections", "higher", "% present",
      "Share of the sections this document type is expected to have that were found as headings or labelled paragraphs.",
      "Journals and publishers expect a recognisable skeleton (IMRaD for research papers). Readers and reviewers navigate by it.",
      "Add the missing sections, or rename existing headings so their role is clear.",
      sev="major", w=2.0, good=100, bad=50,
      params={"required": P("sections", "Sections that must be present", ["abstract", "introduction", "methods", "results", "discussion", "conclusion", "references"]),
              "critical": P("sections", "Missing any of these is critical", ["abstract", "references"])}),
    R("STR-02", "STR", "Section order", "lower", "out of order",
      "Expected sections that appear out of the conventional order (Abstract, Introduction, Methods, Results, Discussion, Conclusion, References).",
      "The conventional order lets readers find methods before results and conclusions after evidence.",
      "Move the flagged sections into the conventional order, or explain the deviation.",
      sev="major", good=0, bad=2),
    R("STR-03", "STR", "Heading hierarchy", "lower", "issues",
      "Headings that skip a level (a level-1 heading followed directly by level 3), and long documents with no headings at all.",
      "A skipped level breaks the outline, the table of contents, and screen-reader navigation.",
      "Insert the missing intermediate heading or promote the deeper one.",
      sev="minor", good=0, bad=4, params={"min_words_for_headings": P("int", "Expect headings above this many words", 1500, min=200, max=50000)}),
    R("STR-04", "STR", "Paragraph length", "lower", "% too long",
      "Share of paragraphs longer than the word limit.",
      "Very long paragraphs hide the topic sentence and tire the reader; most style guides advise one idea per paragraph.",
      "Split each long paragraph at its natural turns of thought.",
      good=5, bad=25, params={"max_words": P("int", "Maximum words per paragraph", 200, min=60, max=600)}),
    R("STR-05", "STR", "One-sentence paragraphs", "lower", "% of paragraphs",
      "Share of paragraphs made of a single sentence (paragraphs under 8 words are ignored).",
      "In expository writing a single-sentence paragraph usually means an undeveloped point or a fragment of a neighbour.",
      "Develop the point with evidence or merge it into the adjacent paragraph.",
      good=10, bad=35),
    R("STR-06", "STR", "Section balance", "lower", "spread (CV)",
      "Coefficient of variation of the word counts of top-level sections (3 or more sections needed).",
      "Wildly uneven sections or chapters usually signal a missing split or an under-developed part.",
      "Split oversized sections; expand or merge undersized ones.",
      good=0.7, bad=1.6),
    R("STR-07", "STR", "Abstract length", "range", "words",
      "Word count of the abstract.",
      "Most journals cap abstracts at 150-300 words; indexing services truncate longer ones.",
      "Trim to the journal limit: problem, method, key result, implication.",
      lo=150, hi=300, tol=0.6, rel=True),
    R("STR-08", "STR", "Document length", "range", "words",
      "Total words of body text (references and tables excluded).",
      "Venues and publishers set length expectations; far outside them a submission is returned unread.",
      "Cut or expand to the venue's range.",
      lo=3000, hi=10000, tol=0.6, rel=True),
    # ------------------------------------------------------------ clarity
    R("CLR-01", "CLR", "Reading grade", "range", "grade",
      "Flesch-Kincaid grade level of the body text.",
      "The grade should match the audience: about 12-16 for scholarly work, 7-10 for general non-fiction.",
      "Shorten sentences and prefer common words to lower it; the opposite raises it.",
      lo=12, hi=16, tol=4),
    R("CLR-02", "CLR", "Long sentences", "lower", "% of sentences",
      "Share of sentences longer than the word limit.",
      "Comprehension drops sharply beyond about 25-30 words per sentence.",
      "Split at conjunctions and semicolons; move qualifications into their own sentence.",
      good=10, bad=30, params={"max_words": P("int", "Long sentence above (words)", 30, min=15, max=80)}),
    R("CLR-03", "CLR", "Very long sentences", "lower", "per 1,000 words",
      "Sentences over the hard limit, counted per 1,000 words.",
      "Sentences this long are almost always two or three sentences fused together.",
      "Break each flagged sentence into two or more.",
      sev="major", good=0.3, bad=3, params={"max_words": P("int", "Very long sentence above (words)", 50, min=30, max=120)}),
    R("CLR-04", "CLR", "Passive voice", "lower", "% of sentences",
      "Share of sentences containing a passive construction (form of 'to be' + past participle).",
      "Passive voice hides who did what. Some is normal in methods sections; heavy use slows every reader.",
      "Name the actor: 'we measured' instead of 'it was measured'.",
      good=20, bad=45),
    R("CLR-05", "CLR", "Sentence variety", "higher", "word spread (SD)",
      "Standard deviation of sentence length in words.",
      "Uniform sentence length produces monotonous prose; varied length keeps attention.",
      "Mix short emphatic sentences with longer explanatory ones.",
      good=7, bad=3),
    # ------------------------------------------------------------ style
    R("STY-01", "STY", "Wordy phrases", "lower", "per 1,000 words",
      "Phrases with a shorter equivalent ('in order to' -> 'to', 'due to the fact that' -> 'because').",
      "Every style guide from Strunk & White to the APA manual asks for the shortest clear wording.",
      "Replace each phrase with the suggested short form.",
      good=1, bad=6),
    R("STY-02", "STY", "Hedges & intensifiers", "lower", "per 1,000 words",
      "Words such as very, really, quite, clearly, obviously, basically, somewhat.",
      "Intensifiers weaken the claims they decorate, and 'clearly' or 'obviously' annoy reviewers when the point is not obvious.",
      "Delete the word, or replace it with a precise quantity or a citation.",
      good=2, bad=9),
    R("STY-03", "STY", "Clichés & stock phrases", "lower", "per 1,000 words",
      "Worn phrases ('at the end of the day', 'paradigm shift', 'plays a crucial role', 'in today's world').",
      "Stock phrases signal autopilot writing and carry little information.",
      "Say the specific thing the phrase stands in for.",
      good=0.2, bad=2),
    R("STY-04", "STY", "Repeated sentence openings", "lower", "per 1,000 words",
      "Runs of three or more consecutive sentences in a paragraph starting with the same word.",
      "Repeated openings create a list-like drone and hide the logical links between sentences.",
      "Vary the openings or combine the sentences.",
      good=0.3, bad=2.5),
    R("STY-05", "STY", "Word echoes", "lower", "per 1,000 words",
      "The same content word used three or more times in one sentence, or twice for a rare word.",
      "Close repetition reads as careless unless it is deliberate emphasis.",
      "Use a pronoun, a synonym, or restructure the sentence.",
      good=1, bad=6),
    R("STY-06", "STY", "Adverb density", "lower", "% of words",
      "Share of words that are -ly adverbs.",
      "Heavy adverb use usually props up weak verbs.",
      "Choose a stronger verb and drop the adverb.",
      good=2.5, bad=6),
    R("STY-07", "STY", "First-person singular", "lower", "per 1,000 words",
      "Uses of I, me, my, mine, myself.",
      "Many scholarly venues prefer 'we' or impersonal constructions; check your venue's guide.",
      "Rephrase, or switch this rule off if your venue allows it.",
      sev="note", w=0.5, good=1, bad=8),
    R("STY-08", "STY", "Contractions", "lower", "per 1,000 words",
      "Contractions such as don't, can't, it's, we're.",
      "Formal academic registers avoid contractions.",
      "Expand each contraction.",
      good=0, bad=3),
    R("STY-09", "STY", "Exclamation marks", "lower", "per 1,000 words",
      "Exclamation marks in body text.",
      "Formal writing earns emphasis through content, not punctuation.",
      "Replace with a full stop.",
      good=0, bad=1.5),
    R("STY-10", "STY", "Inclusive language", "lower", "count",
      "Terms that style guides (APA 7, Chicago 18) flag as non-inclusive, e.g. mankind, manpower, chairman, he or she.",
      "Inclusive wording is now required by most publishers' style guides.",
      "Use the suggested neutral alternative.",
      good=0, bad=4),
    # ------------------------------------------------------------ mechanics
    R("MEC-01", "MEC", "Doubled words", "lower", "count",
      "The same word typed twice in a row ('the the').",
      "Doubled words are the most common typo that spell-checkers miss.",
      "Delete the duplicate.",
      sev="major", good=0, bad=4),
    R("MEC-02", "MEC", "Spacing errors", "lower", "per 1,000 words",
      "Double spaces, space before punctuation, missing space after a comma or full stop.",
      "Spacing slips look careless in print and break typesetting.",
      "Fix the spacing at each location.",
      good=0.5, bad=5),
    R("MEC-03", "MEC", "Unbalanced brackets & quotes", "lower", "count",
      "Paragraphs where opening and closing brackets or quotation marks don't pair up.",
      "An unclosed bracket or quote usually marks a lost edit.",
      "Close or remove the stray mark.",
      sev="major", good=0, bad=5, params={"check_quotes": P("bool", "Also check quotation marks", True)}),
    R("MEC-04", "MEC", "Lower-case sentence starts", "lower", "per 1,000 words",
      "Sentences that begin with a lower-case letter after a full stop.",
      "Usually a missing capital or a stray full stop.",
      "Capitalise the first word or remove the stray full stop.",
      good=0, bad=2),
    R("MEC-05", "MEC", "Repeated punctuation", "lower", "count",
      "Doubled punctuation such as ,, .. !! ?? or ?!",
      "Doubled punctuation is a typo or informal emphasis.",
      "Keep a single mark (use … for an ellipsis).",
      good=0, bad=4),
    R("MEC-06", "MEC", "Spelling", "lower", "per 1,000 words",
      "Lower-case words not found in the English dictionary on this machine, after allowing for common endings and prefixes.",
      "Misspellings are the first thing editors notice and the cheapest thing to fix.",
      "Correct the word, or add genuine technical terms to the custom word list.",
      good=1, bad=6,
      params={"dictionary": P("choice", "Dictionary", "both", choices=["both", "american", "british"]),
              "ignore_repeated": P("int", "Ignore unknown words used at least this often", 3, min=2, max=50),
              "custom_words": P("words", "Custom words (one per line)", [])}),
    # ------------------------------------------------------------ evidence
    R("EVD-01", "EVD", "Citation density", "higher", "per 1,000 words",
      "In-text citations ([12], (Smith, 2020), Smith et al. (2020)) per 1,000 words of body text.",
      "Scholarly work is expected to situate itself in prior work; sparse citation suggests unsupported claims.",
      "Cite sources for background statements, methods borrowed from others, and comparisons.",
      good=8, bad=2),
    R("EVD-02", "EVD", "Citation-reference match", "lower", "% mismatched",
      "Citations with no matching reference entry, and reference entries never cited in the text.",
      "Every in-text citation must resolve to a reference and every reference must be cited (APA, IEEE, Chicago, Harvard all require this).",
      "Add the missing reference entries and remove or cite the orphaned ones.",
      sev="major", w=2.0, good=0, bad=20),
    R("EVD-03", "EVD", "Unsupported claims", "lower", "per 1,000 words",
      "Sentences such as 'studies show', 'it is well known', 'experts agree' with no citation in the same or next sentence.",
      "Appeals to unnamed authority are the classic reviewer complaint: which studies, which experts?",
      "Add the citation or soften the claim to what you can show.",
      sev="major", good=0, bad=1.5),
    R("EVD-04", "EVD", "Reference recency", "lower", "% older than limit",
      "Share of reference entries published more than the age limit before the review year.",
      "A literature review dominated by old sources suggests the author has missed recent work.",
      "Add recent sources, or justify reliance on classic work.",
      good=40, bad=80, params={"max_age": P("int", "Age limit (years)", 10, min=2, max=60)}),
    R("EVD-05", "EVD", "Reference completeness", "lower", "% with problems",
      "Reference entries missing a year, duplicated, or (optionally) missing a DOI/URL.",
      "Incomplete references can't be traced; duplicates inflate the list.",
      "Complete each flagged entry and merge duplicates.",
      good=0, bad=25, params={"require_doi": P("bool", "Require a DOI or URL", False)}),
    R("EVD-06", "EVD", "Long inline quotations", "lower", "count",
      "Quoted passages over the word limit run inline in a paragraph.",
      "APA and Chicago require long quotations to be set as block quotes (APA: 40+ words).",
      "Set the quotation as an indented block quote, or paraphrase it.",
      good=0, bad=3, params={"max_words": P("int", "Block-quote above (words)", 40, min=20, max=150)}),
    # ------------------------------------------------------------ consistency
    R("CON-01", "CON", "Acronyms", "lower", "issues",
      "Acronyms never defined, used before their definition, or defined more than once.",
      "Every style guide requires spelling out an abbreviation at first use.",
      "Define at first use: 'convolutional neural network (CNN)'. Add well-known ones to the allowed list.",
      good=0, bad=6,
      params={"allowed": P("words", "Allowed without definition", ["US", "USA", "UK", "EU", "UN", "OK", "TV", "PDF", "URL", "HTML", "PhD", "MSc", "BSc", "CEO", "DNA", "RNA", "ID", "IQ", "FAQ", "GPS", "USB", "CPU", "GPU", "RAM", "NASA", "WHO", "NATO", "COVID", "HIV", "AIDS", "ISBN", "DOI", "ISO", "IEEE", "ACM", "BBC", "AM", "PM", "AD", "BC", "CE", "BCE", "OS", "PC", "SD", "SE", "SEM", "CI", "OR", "RR", "ANOVA", "IQR"])}),
    R("CON-02", "CON", "US / UK spelling mix", "lower", "% minority",
      "Words spelled the American way and the British way in the same document (color / colour, analyze / analyse).",
      "Publishers require one spelling convention throughout.",
      "Pick one convention and convert the minority forms.",
      good=0, bad=10, params={"prefer": P("choice", "Preferred convention", "majority", choices=["majority", "american", "british"])}),
    R("CON-03", "CON", "Compound word variants", "lower", "terms",
      "The same compound written different ways ('dataset', 'data set', 'data-set').",
      "Inconsistent compounds look like two different terms to the reader and to search.",
      "Choose one form for each term and use it everywhere.",
      good=0, bad=5),
    R("CON-04", "CON", "Number & unit style", "lower", "issues",
      "Mixing % and 'percent', 10000 and 10,000, '5 kg' and '5kg', and sentences that start with a numeral.",
      "APA and Chicago both require a consistent number style and spelling out numbers that start a sentence.",
      "Choose one style for each and fix the minority; rewrite sentences that start with a digit.",
      good=0, bad=6),
    R("CON-05", "CON", "Figure & table references", "lower", "% with problems",
      "Figures and tables referenced but never captioned, captioned but never referenced, or numbered with gaps.",
      "Every figure and table must be cited in the text, in order (APA 7 §7.4, IEEE).",
      "Add the missing caption or in-text reference and renumber in order.",
      sev="major", good=0, bad=30),
    R("CON-06", "CON", "Quote mark style", "lower", "% minority",
      "Straight quotes (\" ') mixed with curly quotes (“ ” ’).",
      "Mixed quote styles are a typesetting tell of pasted text.",
      "Convert all quotation marks and apostrophes to one style.",
      sev="note", w=0.5, good=0, bad=15),
    # ------------------------------------------------------------ integrity
    R("INT-01", "INT", "Placeholders & draft marks", "lower", "count",
      "TODO, TBD, FIXME, XXX, [citation needed], 'Figure ??', 'Error! Reference source not found', lorem ipsum.",
      "Placeholders mean the manuscript is not finished. Editors desk-reject on sight.",
      "Resolve every placeholder before submission.",
      sev="critical", w=2.0, good=0, bad=3),
    R("INT-02", "INT", "Repeated text", "lower", "count",
      "Identical sentences (10+ words) and near-identical paragraphs appearing more than once.",
      "Duplicated text is usually a copy-paste accident, and plagiarism checkers flag it as self-overlap.",
      "Delete the repetition or rewrite one instance.",
      sev="major", good=0, bad=4),
    R("INT-03", "INT", "Stub sections", "lower", "count",
      "Sections with fewer than the minimum words of their own and no subsections.",
      "A heading with almost nothing under it is an unfinished section.",
      "Write the section out or merge it into its neighbour.",
      sev="major", good=0, bad=3, params={"min_words": P("int", "Minimum words per section", 25, min=5, max=500)}),
    R("INT-04", "INT", "Declarations & statements", "higher", "% present",
      "End-matter statements the venue expects: keywords, data availability, competing interests, funding.",
      "Most journals (COPE, ICMJE, Springer Nature, Elsevier) now require these statements.",
      "Add each missing statement, even if it only says 'none'.",
      good=100, bad=25,
      params={"required": P("sections", "Required statements", ["keywords", "data availability", "competing interests", "funding"])}),
    R("INT-05", "INT", "Open tracked changes & comments", "lower", "count",
      "Unaccepted tracked changes and reviewer comments still embedded in a Word or OpenDocument file.",
      "Open revisions leak editing history and confuse typesetters.",
      "Accept or reject all changes and delete comments before submitting.",
      sev="major", good=0, bad=5),
]
import rules_more  # noqa: E402

RULES += rules_more.define(R, P)
_ORDER = [c[0] for c in CATEGORIES]
RULES.sort(key=lambda r: (_ORDER.index(r["cat"]), r["id"]))
RULE_BY_ID = {r["id"]: r for r in RULES}


def default_settings():
    return {
        "profile": "paper",
        "strictness": 1.0,
        "aggregation": "rms",
        "asof_year": 2026,
        "verdict": {"accept": 85, "minor": 70, "major": 50},
        "gates": {"max_critical_accept": 0, "max_critical_minor": 3, "category_floor": 50},
        "categories": {c[0]: 1.0 for c in CATEGORIES},
        "rules": {r["id"]: {
            "on": True, "w": r["w"], "sev": r["sev"],
            **({"good": r["good"], "bad": r["bad"]} if r["mode"] != "range" else {"lo": r["lo"], "hi": r["hi"], "tol": r["tol"]}),
            "params": {k: copy.deepcopy(v["default"]) for k, v in r["params"].items()},
        } for r in RULES},
    }


def _apply(s, cats=None, rules=None, **top):
    for k, v in top.items():
        s[k] = v
    for k, v in (cats or {}).items():
        s["categories"][k] = v
    for rid, ov in (rules or {}).items():
        tgt = s["rules"][rid]
        for k, v in ov.items():
            if k == "params":
                tgt["params"].update(v)
            else:
                tgt[k] = v
    return s


OFF = {"on": False}

PROFILES = {
    "paper": dict(name="Research paper", blurb="Journal or conference article. IMRaD structure, citations, declarations.",
                  cats={"EVD": 1.5, "INT": 1.2, "STY": 0.8, "MEC": 0.8}, rules={}),
    "chapter": dict(name="Book chapter", blurb="Non-fiction or academic chapter. Readability and flow weigh more; citations lighter.",
                    cats={"CLR": 1.2, "EVD": 0.8},
                    rules={"STR-01": {"params": {"required": ["introduction", "conclusion"], "critical": []}, "w": 1.0},
                           "STR-02": OFF, "STR-07": OFF,
                           "STR-08": {"lo": 3000, "hi": 12000},
                           "STR-04": {"params": {"max_words": 220}},
                           "CLR-01": {"lo": 10, "hi": 14},
                           "CLR-04": {"good": 15, "bad": 35},
                           "EVD-01": {"good": 3, "bad": 0.5},
                           "EVD-04": {"good": 50, "bad": 90, "params": {"max_age": 15}},
                           "INT-04": OFF, "STY-07": OFF}),
    "book": dict(name="Full book", blurb="Whole non-fiction manuscript. Chapter balance, consistency and readability lead.",
                 cats={"CLR": 1.2, "STY": 1.1, "CON": 1.2, "EVD": 0.5},
                 rules={"STR-01": {"params": {"required": [], "critical": []}, "on": False},
                        "STR-02": OFF, "STR-07": OFF,
                        "STR-08": {"lo": 40000, "hi": 150000},
                        "STR-04": {"params": {"max_words": 250}},
                        "STR-06": {"good": 0.5, "bad": 1.2},
                        "CLR-01": {"lo": 8, "hi": 12},
                        "CLR-04": {"good": 12, "bad": 30},
                        "EVD-01": OFF, "EVD-04": OFF, "INT-04": OFF, "STY-07": OFF, "STY-08": OFF}),
    "fiction": dict(name="Fiction", blurb="Novel, story or narrative chapter. Prose style matters most; no citations or IMRaD.",
                    cats={"STR": 0.6, "STY": 1.4, "EVD": 0.0, "CON": 0.8},
                    rules={"STR-01": OFF, "STR-02": OFF, "STR-03": OFF, "STR-05": OFF, "STR-07": OFF,
                           "STR-08": {"lo": 2000, "hi": 120000, "tol": 0.8},
                           "STR-04": {"params": {"max_words": 300}},
                           "CLR-01": {"lo": 5, "hi": 9},
                           "CLR-04": {"good": 10, "bad": 25},
                           "STY-06": {"good": 3, "bad": 7},
                           "STY-07": OFF, "STY-08": OFF, "STY-09": {"good": 1, "bad": 5},
                           "MEC-03": {"params": {"check_quotes": False}},
                           "EVD-01": OFF, "EVD-02": OFF, "EVD-03": OFF, "EVD-04": OFF, "EVD-05": OFF, "EVD-06": OFF,
                           "CON-01": {"w": 0.5}, "CON-04": {"w": 0.5}, "CON-05": OFF,
                           "INT-03": OFF, "INT-04": OFF}),
    "general": dict(name="General document", blurb="Reports, essays, proposals, documentation. Balanced defaults.",
                    cats={"EVD": 0.6},
                    rules={"STR-01": OFF, "STR-02": OFF, "STR-07": OFF,
                           "STR-08": {"lo": 300, "hi": 30000, "tol": 0.8},
                           "STR-04": {"params": {"max_words": 160}},
                           "CLR-01": {"lo": 8, "hi": 13},
                           "CLR-04": {"good": 15, "bad": 35},
                           "EVD-01": OFF, "EVD-04": OFF, "INT-04": OFF, "STY-07": OFF}),
}


def profile_settings(pid):
    p = PROFILES[pid]
    s = default_settings()
    s["profile"] = pid
    s = _apply(s, p["cats"], p["rules"])
    for r in RULES:
        if r.get("only") is not None and pid not in r["only"]:
            s["rules"][r["id"]]["on"] = False
        ov = (r.get("prof") or {}).get(pid)
        if ov:
            _apply(s, None, {r["id"]: ov})
    return s


def merge_settings(user):
    """Fill anything missing from the chosen profile's defaults and clamp values."""
    pid = user.get("profile") if user.get("profile") in PROFILES else "paper"
    s = profile_settings(pid)
    def num(v, d, lo=None, hi=None):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return d
        if v != v:
            return d
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return round(v, 4)
    s["strictness"] = num(user.get("strictness"), 1.0, 0.6, 1.8)
    s["asof_year"] = int(num(user.get("asof_year"), s["asof_year"], 1900, 2100))
    if user.get("aggregation") in ("rms", "mean"):
        s["aggregation"] = user["aggregation"]
    for k in ("accept", "minor", "major"):
        s["verdict"][k] = num((user.get("verdict") or {}).get(k), s["verdict"][k], 0, 100)
    for k in s["gates"]:
        s["gates"][k] = num((user.get("gates") or {}).get(k), s["gates"][k], 0, 1000)
    for k in s["categories"]:
        s["categories"][k] = num((user.get("categories") or {}).get(k), s["categories"][k], 0, 3)
    for rid, base in s["rules"].items():
        u = (user.get("rules") or {}).get(rid) or {}
        if "on" in u:
            base["on"] = bool(u["on"])
        base["w"] = num(u.get("w"), base["w"], 0, 3)
        if u.get("sev") in SEVERITIES:
            base["sev"] = u["sev"]
        for k in ("good", "bad", "lo", "hi", "tol"):
            if k in base:
                base[k] = num(u.get(k), base[k], 0, 10 ** 7)
        spec = RULE_BY_ID[rid]["params"]
        for pk, pv in (u.get("params") or {}).items():
            if pk not in spec:
                continue
            t = spec[pk]["type"]
            if t == "int":
                base["params"][pk] = int(num(pv, base["params"][pk], spec[pk].get("min"), spec[pk].get("max")))
            elif t == "bool":
                base["params"][pk] = bool(pv)
            elif t == "choice":
                if pv in spec[pk]["choices"]:
                    base["params"][pk] = pv
            elif t in ("words", "sections"):
                if isinstance(pv, str):
                    pv = [w for w in pv.replace(",", "\n").split("\n")]
                if isinstance(pv, list):
                    vals = sorted({str(w).strip() for w in pv if str(w).strip()}, key=str.lower)
                    if t == "sections":
                        vals = [v for v in CANON_SECTIONS if v in {x.lower() for x in vals}]
                    base["params"][pk] = vals[:2000]
    return s


def _examples():
    import examples
    return examples.EX


def catalog():
    return {
        "engine": ENGINE_VERSION,
        "categories": [{"id": c[0], "name": c[1], "q": c[2]} for c in CATEGORIES],
        "rules": [dict({k: r[k] for k in ("id", "cat", "name", "mode", "unit", "desc", "why", "fix", "rel", "params")},
                       ex=_examples().get(r["id"])) for r in RULES],
        "profiles": {pid: {"name": p["name"], "blurb": p["blurb"], "settings": profile_settings(pid)} for pid, p in PROFILES.items()},
        "sections": CANON_SECTIONS,
        "severities": SEVERITIES,
    }
