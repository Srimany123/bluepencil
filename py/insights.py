"""Extra metrics for the online view (not the client report): writing-style signals that tend to separate
human and AI-generated prose, plus flow averages. Computed after the review, so review IDs are unaffected.

These are indicators, not proof of authorship. Edited human text can look "AI-like" and edited AI text
can look human; short texts give unstable values.
"""
import math
import re

import analysis

# words large language models overuse relative to human writing (frequent in published comparisons)
AI_WORDS = set("""delve delves delving tapestry intricate intricacies pivotal underscore underscores underscoring showcase showcases
showcasing multifaceted nuanced realm landscape foster fosters fostering leverage leverages leveraging seamless seamlessly robust
testament notably crucial comprehensive vibrant embark embarks navigate navigating meticulous meticulously paramount holistic
synergy transformative unwavering commendable noteworthy invaluable profound interplay endeavor endeavour bolster bolsters
elucidate streamline streamlines elevate elevates harness harnessing captivating enduring resonate resonates""".split())
STOCK = [r"it is (?:important|worth|crucial) to (?:note|remember|consider)", r"it'?s worth noting", r"plays? an? (?:crucial|pivotal|vital|key) role",
         r"in today'?s (?:fast-paced|digital|modern|ever-changing)", r"a testament to", r"delve (?:into|deeper)", r"rich tapestry",
         r"navigat\w+ the complexit", r"in the realm of", r"serves? as a (?:powerful|valuable|vital)", r"a wide (?:range|array|variety) of",
         r"ever-(?:evolving|changing)", r"not only\b.{3,80}\bbut also", r"in (?:summary|conclusion),", r"overall,", r"stands? as a",
         r"(?:shed|sheds) light on", r"the importance of\b", r"(?:unlock|unlocks|unlocking) the (?:potential|power)", r"at the heart of"]
STOCK_RE = re.compile(r"\b(?:%s)" % "|".join(STOCK), re.I)
TRANSITIONS = re.compile(r"^(?:moreover|furthermore|additionally|in addition|overall|in conclusion|notably|importantly|ultimately|"
                         r"consequently|thus|hence|indeed|in summary|as a result|subsequently)\b", re.I)


def _clamp(x):
    return max(0.0, min(1.0, x))


def _cv(xs):
    xs = [x for x in xs if x > 0]
    if len(xs) < 3:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) / m if m else None


def ai_signals(doc, rep):
    W = max(1, doc.words)
    sents = doc.sents
    prose_text = " ".join(doc.blocks[b]["t"] for b in doc.prose)
    words = [w.lower() for x in sents for w in x["w"]]
    sig = []

    def add(key, name, value, shown, lean, weight, human, meaning):
        sig.append({"key": key, "name": name, "value": value, "shown": shown, "lean": None if lean is None else round(lean, 2),
                    "weight": weight, "human": human, "meaning": meaning})

    cv = _cv([x["n"] for x in sents])
    add("burst", "Sentence-length variety (burstiness)", cv, "–" if cv is None else "%.2f" % cv,
        None if cv is None else _clamp((0.55 - cv) / 0.3), 3, "0.5 or more",
        "How much sentence lengths vary (standard deviation ÷ mean). People mix short and long sentences; "
        "generated text tends to keep them evenly sized, so a low value leans AI.")
    pcv = _cv([sum(doc.sents[k]["n"] for k in doc.sents_by_block[b]) for b in doc.prose])
    add("para", "Paragraph-length variety", pcv, "–" if pcv is None else "%.2f" % pcv,
        None if pcv is None else _clamp((0.45 - pcv) / 0.3), 1.5, "0.4 or more",
        "The same idea for paragraphs. Generated text often produces paragraphs of near-identical size.")
    hits = [w for w in words if w in AI_WORDS]
    rate = 1000.0 * len(hits) / W
    top = sorted(set(hits), key=lambda w: -hits.count(w))[:6]
    add("vocab", "AI-favoured vocabulary", rate, "%.1f per 1,000 words%s" % (rate, (" (" + ", ".join(top) + ")") if top else ""),
        _clamp(rate / 6.0), 3, "under 1.5 per 1,000",
        "Words that language models use far more often than people do (delve, pivotal, tapestry, underscore, seamless, robust…).")
    stock = STOCK_RE.findall(prose_text)
    srate = 1000.0 * len(stock) / W
    add("stock", "Stock phrases", srate, "%.1f per 1,000 words" % srate, _clamp(srate / 3.0), 2, "under 0.5 per 1,000",
        "Set phrases typical of generated text: “it is important to note”, “plays a crucial role”, “a testament to”, “not only … but also”.")
    tr = sum(1 for x in sents if TRANSITIONS.match(x["t"].lstrip()))
    trate = 100.0 * tr / max(1, len(sents))
    add("trans", "Formulaic sentence openers", trate, "%.1f%% of sentences" % trate, _clamp((trate - 3) / 9.0), 1.5, "under 4%",
        "Sentences that open with Moreover, Furthermore, Additionally, Overall, In conclusion and similar signposts.")
    dashes = prose_text.count("—")
    drate = 1000.0 * dashes / W
    add("dash", "Em dashes", drate, "%.1f per 1,000 words" % drate, _clamp((drate - 2) / 6.0), 1, "under 3 per 1,000",
        "Heavy em-dash use is a common trait of recent model output. Some writers use them a lot too, so the weight is low.")
    leftovers = next((r["n"] for r in rep["rules"] if r["id"] == "INT-09" and r["status"] == "scored"), 0)
    add("left", "Chatbot leftovers", leftovers, "%d found" % leftovers, 1.0 if leftovers else 0.0, 4 if leftovers else 1, "none",
        "Pasted assistant text such as “As an AI language model” or “I hope this helps”. A single one is strong evidence.")
    usable = [s for s in sig if s["lean"] is not None]
    tw = sum(s["weight"] for s in usable)
    index = round(100.0 * sum(s["lean"] * s["weight"] for s in usable) / tw) if tw else None
    for s in usable:
        s["contrib"] = round(100.0 * s["lean"] * s["weight"] / tw, 1) if tw else 0
    band = None if index is None else ("Mostly human-typical" if index < 30 else "Mixed signals" if index < 60 else "Many AI-typical traits")
    return {"index": index, "band": band, "signals": sig, "reliable": doc.words >= 300,
            "note": "Indicators, not proof. Human writing that was heavily edited, or written to a template, can score high; "
                    "edited AI text can score low. Under 300 words the values are unstable."}


def flow_summary(doc):
    rows = analysis.flow(doc)
    coh = [r["coh"] for r in rows if r["coh"] is not None and r["same"]]
    nov = [r["nov"] for r in rows if r["w"] >= 15]
    breaks = [{"para": doc.ordinal[r["b"]], "sec": doc.sec_of[r["b"]], "coh": r["coh"]} for r in rows
              if r["coh"] is not None and r["same"] and r["coh"] < 0.04 and r["w"] >= 25][:12]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    return {"cohesion": avg(coh), "novelty": avg(nov), "breaks": breaks,
            "grade_spread": round(max((r["grade"] for r in rows if r["w"] >= 15), default=0) - min((r["grade"] for r in rows if r["w"] >= 15), default=0), 1)}


def compute(doc, rep):
    try:
        return {"ai": ai_signals(doc, rep), "flow": flow_summary(doc)}
    except Exception as e:   # never let an extra metric break a review
        return {"error": str(e)}
