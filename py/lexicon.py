"""Word lists used by the style rules. Plain data, easy to extend."""

WORDY = {
    "in order to": "to", "in order for": "for", "due to the fact that": "because", "owing to the fact that": "because",
    "in light of the fact that": "because", "on account of the fact that": "because", "given the fact that": "because",
    "despite the fact that": "although", "in spite of the fact that": "although", "regardless of the fact that": "although",
    "the fact that": "that (or rephrase)", "at this point in time": "now", "at the present time": "now",
    "at this moment in time": "now", "in the near future": "soon", "in the event that": "if", "in the case that": "if",
    "in cases where": "when", "for the purpose of": "for / to", "with the purpose of": "to", "with regard to": "about",
    "with respect to": "about / for", "in regard to": "about", "in relation to": "about", "in terms of": "in / for (or rephrase)",
    "a large number of": "many", "a great number of": "many", "a significant number of": "many", "a small number of": "a few",
    "the majority of": "most", "a majority of": "most", "a sufficient number of": "enough", "a total of": "(delete)",
    "has the ability to": "can", "have the ability to": "can", "is able to": "can", "are able to": "can",
    "has the capacity to": "can", "is capable of": "can", "it is possible that": "may / might", "there is a possibility that": "may",
    "it is important to note that": "(delete)", "it should be noted that": "(delete)", "it is worth noting that": "(delete)",
    "it is interesting to note that": "(delete)", "it is clear that": "(delete, or show it)", "there is no doubt that": "doubtless",
    "make a decision": "decide", "made a decision": "decided", "make an assumption": "assume", "make an attempt": "try",
    "perform an analysis": "analyze", "performed an analysis": "analyzed", "conduct an investigation": "investigate",
    "conducted an investigation": "investigated", "give consideration to": "consider", "take into consideration": "consider",
    "came to the conclusion": "concluded", "come to the conclusion": "conclude", "reach a conclusion": "conclude",
    "provide an explanation": "explain", "make reference to": "refer to", "have an effect on": "affect", "has an effect on": "affects",
    "is dependent on": "depends on", "are dependent on": "depend on", "is indicative of": "indicates", "are indicative of": "indicate",
    "prior to": "before", "subsequent to": "after", "in advance of": "before", "in close proximity to": "near",
    "in the vicinity of": "near", "on a daily basis": "daily", "on a regular basis": "regularly", "on a weekly basis": "weekly",
    "until such time as": "until", "in a timely manner": "promptly", "with the exception of": "except",
    "each and every": "each", "first and foremost": "first", "basic fundamentals": "fundamentals", "end result": "result",
    "final outcome": "outcome", "past history": "history", "future plans": "plans", "completely eliminate": "eliminate",
    "absolutely essential": "essential", "advance planning": "planning", "close proximity": "proximity", "added bonus": "bonus",
    "brief summary": "summary", "join together": "join", "combine together": "combine", "repeat again": "repeat",
    "revert back": "revert", "return back": "return", "new innovation": "innovation", "true fact": "fact",
    "whether or not": "whether", "as to whether": "whether", "the question as to whether": "whether",
    "in the process of": "(delete)", "serves to": "(delete)", "is used to": "(rephrase)", "a variety of": "various",
    "a number of": "several / some", "in excess of": "more than", "along the lines of": "like", "by means of": "by",
    "by virtue of": "by / because of", "for the reason that": "because", "in view of the fact that": "because",
    "in the absence of": "without", "is in agreement with": "agrees with", "are in agreement with": "agree with",
    "at all times": "always", "in all cases": "always", "is found to be": "is", "was found to be": "was",
}

HEDGES = [
    "very", "really", "quite", "rather", "extremely", "highly", "truly", "incredibly", "clearly", "obviously", "evidently",
    "of course", "basically", "essentially", "actually", "literally", "somewhat", "fairly", "pretty much", "kind of", "sort of",
    "arguably", "seemingly", "apparently", "perhaps", "possibly", "presumably", "virtually", "practically", "totally",
    "completely", "absolutely", "definitely", "certainly", "undoubtedly", "needless to say", "it goes without saying",
    "simply", "just", "a bit", "a little", "relatively", "fundamentally", "substantially", "tremendously",
]

CLICHES = [
    "at the end of the day", "think outside the box", "outside the box", "low-hanging fruit", "paradigm shift",
    "game changer", "game-changer", "in today's world", "in today’s world", "in this day and age", "since the dawn of time",
    "since time immemorial", "the bottom line", "few and far between", "last but not least", "move the needle",
    "a perfect storm", "tip of the iceberg", "plays a crucial role", "plays a vital role", "plays a pivotal role",
    "plays an important role", "play a crucial role", "play a vital role", "play a pivotal role", "play an important role",
    "a double-edged sword", "double-edged sword", "the elephant in the room", "at the heart of", "a wake-up call",
    "only time will tell", "a level playing field", "best of both worlds", "cutting-edge", "state-of-the-art",
    "groundbreaking", "revolutionize", "revolutionise", "delve into", "delves into", "a testament to", "in the realm of",
    "navigate the complexities", "rich tapestry", "ever-evolving", "ever-changing landscape", "fast-paced world",
    "push the envelope", "raise the bar", "the lion's share", "the lion’s share", "it is what it is", "needle in a haystack",
    "in a nutshell", "first things first", "back to square one", "the writing on the wall", "nowadays",
]

INCLUSIVE = {
    "mankind": "humankind / humanity", "manpower": "workforce / staff", "man-made": "artificial / synthetic",
    "man-hours": "person-hours / work hours", "chairman": "chair / chairperson", "chairmen": "chairs",
    "policeman": "police officer", "policemen": "police officers", "fireman": "firefighter", "firemen": "firefighters",
    "businessman": "businessperson / executive", "businessmen": "businesspeople", "spokesman": "spokesperson",
    "foreman": "supervisor", "layman": "layperson", "laymen": "laypeople", "salesman": "salesperson",
    "workmanlike": "competent", "he or she": "they", "he/she": "they", "s/he": "they", "his or her": "their",
    "his/her": "their", "him or her": "them", "whitelist": "allowlist", "blacklist": "blocklist / denylist",
    "whitelisted": "allowlisted", "blacklisted": "blocklisted", "master/slave": "primary/replica", "slave": "replica / secondary",
    "sanity check": "quick check / coherence check", "dummy value": "placeholder value", "crippled": "impaired / disabled",
    "the elderly": "older adults", "elderly people": "older adults", "addicts": "people with substance use disorders",
    "committed suicide": "died by suicide", "suffers from": "has / lives with", "wheelchair-bound": "wheelchair user",
    "confined to a wheelchair": "uses a wheelchair", "normal people": "typical people / people without the condition",
    "illegal immigrant": "undocumented immigrant", "illegal immigrants": "undocumented immigrants",
}

ABBR = {
    "e.g", "i.e", "et al", "al", "fig", "figs", "eq", "eqs", "no", "nos", "vol", "vols", "pp", "p", "dr", "mr", "mrs", "ms",
    "prof", "sr", "jr", "vs", "etc", "cf", "approx", "dept", "est", "inc", "ltd", "co", "corp", "st", "ch", "chap", "sec",
    "ref", "refs", "ed", "eds", "tab", "u.s", "u.k", "ph.d", "a.m", "p.m", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "min", "max", "resp", "viz", "ca", "rev", "gen", "gov", "col", "lt", "sgt", "capt",
    "mt", "ft", "in", "lb", "lbs", "oz", "op", "cit", "ibid", "trans", "repr", "n.d", "esp", "incl", "dept", "univ", "assn",
    "e", "i", "v", "nr", "art", "para", "paras", "ser", "suppl", "ave", "blvd", "rd",
}

STOP = set("""a about above after again against all almost also although always am among an and another any anyone
anything are around as at away back be became because become becomes been before being below between both but by
can cannot could did do does doing done down during each either else enough even ever every few for from further
get gets given go goes had has have having he her here hers herself him himself his how however i if in into is it
its itself just less let like made make makes many may me might more most much must my myself near need neither
never no nor not now of off often on once one only onto or other others our ours ourselves out over own per perhaps
put quite rather really said same say says see seem seemed seems several shall she should since so some something
still such than that the their theirs them themselves then there therefore these they this those though through thus
to together too toward towards under until up upon us use used uses using very via was we well were what when where
whether which while who whom whose why will with within without would yet you your yours yourself yourselves also
however thus hence first second third new may might upon whereas whose""".split())

LY_EXCEPT = set("""only early family apply reply supply likely unlikely july italy belly ally rely fly ugly holy daily
weekly monthly yearly hourly nightly quarterly lonely lovely friendly costly deadly elderly silly jolly bully lily
rally sally tally folly jelly comply imply multiply anomaly assembly monopoly melancholy butterfly firefly dragonfly
homily curly burly surly hilly chilly frilly wily oily kindly sly shyly drily wryly fully belly gully reliably assembly
underbelly respectively""".split())

PASSIVE_IRREG = """known built done made shown seen given taken found written chosen drawn driven grown held kept left lost
meant paid put read run said sent set sold spent told thought understood won begun broken brought bought caught fed felt
fought forgotten forgiven frozen got gotten hidden hit hurt laid led lent lit overcome proven rung risen shaken shot shut
spoken spun spread stolen struck sworn swept taught thrown torn woken worn wound withdrawn undertaken upheld beaten bitten
blown born borne bound bred dealt dug fallen fled flown forbidden hung sought split stuck stung understood overseen
undergone outdone rewritten mistaken foreseen""".split()

ADJ_PARTICIPLES = set("""mixed interested tired excited worried pleased surprised satisfied concerned married supposed
bored scared confused frightened amazed annoyed ashamed delighted disappointed embarrassed exhausted relaxed shocked
convinced determined prepared qualified skilled talented complicated sophisticated detailed limited varied advanced
experienced""".split())

NOT_ACRONYMS = {"TODO", "TBD", "TBA", "FIXME", "XXX", "XXXX", "TKTK", "NB", "PS", "OK"}

TECH_WORDS = set("""dataset datasets online offline email emails website websites webpage workflow workflows metadata
preprocessing preprocess preprocessed postprocessing username usernames login logout setup backend frontend runtime
codebase toolkit toolchain smartphone smartphones app apps blog blogs podcast podcasts timestamp timestamps lifecycle
pipeline pipelines benchmark benchmarks benchmarking hyperparameter hyperparameters parameterize parameterized
tokenization tokenize tokenizer embeddings embedding finetune finetuning pretrained pretraining dropout softmax
convolutional backpropagation multimodal crowdsourcing crowdsourced subsample subsampling resample resampling
covariate covariates heteroscedasticity multicollinearity overfitting underfitting regressor regressors bayesian
stakeholder stakeholders roadmap roadmaps healthcare wellbeing nonlinear nonparametric multivariate univariate bivariate
spreadsheet spreadsheets internet cybersecurity blockchain microcontroller microcontrollers firmware hardcoded
""".split())
