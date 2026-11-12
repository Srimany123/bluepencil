"""Turn an uploaded file into a list of blocks.

A block is {"k": kind, "t": text, "l": heading level, "p": page}.
Kinds: title, h (heading), para, cap (figure/table caption), ref (reference
entry), tbl (table row), code. Every format ends up in this one shape so the
review rules never care where the text came from.
"""
import csv
import io
import os
import re
import subprocess
import tempfile
import unicodedata
import zipfile

from lxml import etree
from lxml import html as lhtml

FORMATS = {
    ".pdf": "PDF", ".docx": "Word", ".doc": "Word 97-2003", ".odt": "OpenDocument text",
    ".rtf": "Rich text", ".txt": "Plain text", ".md": "Markdown", ".markdown": "Markdown",
    ".html": "HTML", ".htm": "HTML", ".epub": "EPUB", ".xlsx": "Excel", ".xlsm": "Excel",
    ".xls": "Excel 97-2003", ".csv": "CSV", ".tsv": "TSV", ".tex": "LaTeX",
}


class ExtractError(Exception):
    pass


def B(kind, text, level=0, page=None):
    return {"k": kind, "t": text, "l": level, "p": page}


SECTION_WORDS = (
    r"abstract|executive summary|introduction|background|motivation|overview|related work|literature review|"
    r"prior work|theory|theoretical framework|methods?|methodology|materials and methods|experimental setup|"
    r"experimental design|study design|approach|experiments?|results?|findings|results and discussion|"
    r"discussion|evaluation|analysis|implementation|conclusions?|concluding remarks|summary|limitations|"
    r"future work|references|bibliography|works cited|literature cited|reference list|sources|"
    r"acknowledge?ments?|appendix(?: [a-z0-9]+)?|appendices|keywords|index terms|funding|data availability|"
    r"data and code availability|conflicts? of interest|competing interests?|declarations?|ethics(?: statement)?|"
    r"contents|table of contents|preface|foreword|prologue|epilogue|afterword|glossary|index|"
    r"objectives?|scope|case stud(?:y|ies)|recommendations?|notes|author contributions"
)
SECTION_RE = re.compile(r"^(?:(?:\d+(?:\.\d+)*|[IVXLC]+|[A-Z])[.)]?\s+)?(?:" + SECTION_WORDS + r")\s*:?$", re.I)
NUM_HEAD_RE = re.compile(
    r"^(?:(chapter|part|book|section|appendix)\s+([0-9]+|[ivxlcdm]+|[a-z]|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve)\b[.:]?(\s+.*)?"
    r"|(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+[A-Z][^.!?]{0,90}"
    r"|([IVXLC]{1,5})\.\s+[A-Z][^.!?]{0,90})$",
    re.I,
)
CAPTION_RE = re.compile(r"^(fig\.?|figure|table|tab\.|chart|exhibit|plate|plot|graph)\s*(\d+|[IVX]+)\b", re.I)
REF_HEAD_RE = re.compile(r"^(?:\d+\.?\s+)?(references|bibliography|works cited|literature cited|reference list|sources|citations)\s*:?$", re.I)
REF_START_RE = re.compile(r"^\s*(\[\d{1,4}\]|\d{1,4}\.\s|\d{1,4}\)\s|[A-Z][A-Za-z'’\-]+,\s+(?:[A-Z]\.|[A-Z][a-z]+)|[A-Z][A-Za-z'’\-]+\s+[A-Z]{1,3}[,.\s])")


def guess_heading(line):
    """Return a heading level (1-4) if a lone line looks like a heading, else 0."""
    s = line.strip()
    if not s or len(s) > 120:
        return 0
    words = s.split()
    if len(words) > 14 or s[0].islower():
        return 0
    if SECTION_RE.match(s):
        m = re.match(r"^(\d+(?:\.\d+)*)", s)
        return min(4, m.group(1).count(".") + 1) if m else 1
    if re.match(r"^(chapter|part|book|section|appendix|letter|act|scene)\s+([0-9]+|[ivxlcdm]+|[a-z]|one|two|three|four|five|six|"
                r"seven|eight|nine|ten|eleven|twelve|[a-z]+-?[a-z]*)\.?$", s, re.I) and len(words) <= 3:
        return 1
    if s[-1] in ".,;" and not re.match(r"^\d+\.$", words[0]):
        return 0
    m = NUM_HEAD_RE.match(s)
    if m:
        if m.group(1):
            return 1
        if m.group(4):
            if len(words) < 2 or re.search(r"\d{3,}", s[len(m.group(4)):]):
                return 0
            return min(4, m.group(4).count(".") + 1)
        if m.group(5):
            return 1
    letters = [c for c in s if c.isalpha()]
    if s[0] in "“\"‘'_([" :
        return 0
    if len(letters) >= 4 and len(words) <= 8 and all(c.isupper() for c in letters) and not s.endswith("."):
        return 1
    return 0


def clean(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    return text


def join_lines(lines):
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and ln[:1].islower():
            out = out[:-1] + ln
        elif out:
            out += " " + ln
        else:
            out = ln
    return out


def text_to_blocks(text, pages=None, keep_spacing=False):
    """Plain text -> blocks. `pages` is an optional list of page numbers per line."""
    lines = text.split("\n")
    if pages is None:
        pages = [None] * len(lines)
    chunks, cur, cur_pages = [], [], []
    for ln, pg in zip(lines, pages):
        if ln.strip():
            cur.append(ln)
            cur_pages.append(pg)
        elif cur:
            chunks.append((cur, cur_pages))
            cur, cur_pages = [], []
    if cur:
        chunks.append((cur, cur_pages))

    blocks, in_refs, ref_level, ref_style = [], False, 1, None
    for ls, ps in chunks:
        page = ps[0]
        # a heading glued to the first line of a paragraph (common in PDFs)
        if len(ls) > 1:
            lvl = guess_heading(ls[0])
            if lvl and (SECTION_RE.match(ls[0].strip()) or NUM_HEAD_RE.match(ls[0].strip())) and len(ls[0].split()) <= 10:
                blocks.append(B("h", ls[0].strip(), lvl, page))
                in_refs, ref_level = _ref_state(blocks[-1], in_refs, ref_level)
                ls, ps = ls[1:], ps[1:]
        if len(ls) == 1 and guess_heading(ls[0]):
            blocks.append(B("h", ls[0].strip(), guess_heading(ls[0]), page))
            in_refs, ref_level = _ref_state(blocks[-1], in_refs, ref_level)
            continue
        if in_refs:
            entry, entry_page = [], page
            if ref_style is None:
                ref_style = "brk" if re.match(r"^\s*\[\d{1,4}\]", ls[0]) else "num" if re.match(r"^\s*\d{1,4}[.)]\s", ls[0]) else "any"
            starts = {"brk": re.compile(r"^\s*\[\d{1,4}\]"), "num": re.compile(r"^\s*\d{1,4}[.)]\s")}.get(ref_style, REF_START_RE)
            for ln, pg in zip(ls, ps):
                if starts.match(ln) and entry:
                    blocks.append(B("ref", join_lines(entry), 0, entry_page))
                    entry, entry_page = [], pg
                entry.append(ln)
            if entry:
                blocks.append(B("ref", join_lines(entry), 0, entry_page))
            continue
        t = join_lines(ls) if not keep_spacing else " ".join(l.strip() for l in ls)
        kind = "cap" if CAPTION_RE.match(t) else "para"
        blocks.append(B(kind, t, 0, page))
    return blocks


def _ref_state(hblock, in_refs, ref_level):
    if REF_HEAD_RE.match(hblock["t"]):
        return True, hblock["l"]
    if in_refs and hblock["l"] <= ref_level:
        return False, ref_level
    return in_refs, ref_level


def mark_refs(blocks):
    """For structured formats: paragraphs under a References heading become ref entries."""
    in_refs, ref_level = False, 1
    for b in blocks:
        if b["k"] == "h":
            in_refs, ref_level = _ref_state(b, in_refs, ref_level)
        elif in_refs and b["k"] == "para":
            b["k"] = "ref"
    return blocks


# ---------------------------------------------------------------- formats

def from_pdf(path):
    try:
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise ExtractError("The PDF took too long to read (over 3 minutes).")
    if out.returncode != 0:
        raise ExtractError("pdftotext could not read this PDF: " + out.stderr.decode("utf8", "replace")[:200])
    raw = clean(out.stdout.decode("utf8", "replace"))
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    if sum(len(p.strip()) for p in pages) < 200:
        raise ExtractError("This PDF has no text layer (it looks like scanned images). Export it with text, or OCR it first.")
    # strip running headers / footers: lines repeated on many pages
    def norm(l):
        return re.sub(r"\d+", "#", l.strip().lower())
    counts = {}
    for p in pages:
        ls = [l for l in p.split("\n") if l.strip()]
        for l in set(ls[:2] + ls[-2:]):
            counts[norm(l)] = counts.get(norm(l), 0) + 1
    repeated = {k for k, v in counts.items() if len(pages) >= 4 and v >= max(3, 0.4 * len(pages))}
    lines, pnums = [], []
    for i, p in enumerate(pages, 1):
        ls = p.split("\n")
        nonempty = [j for j, l in enumerate(ls) if l.strip()]
        edge = set(nonempty[:2] + nonempty[-2:])
        for j, l in enumerate(ls):
            s = l.strip()
            if j in edge and (norm(l) in repeated or re.fullmatch(r"(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?", s, re.I)):
                continue
            lines.append(l)
            pnums.append(i)
        lines.append("")
        pnums.append(i)
    lines, pnums = _pdf_paragraphs(lines, pnums)
    return text_to_blocks("\n".join(lines), pnums), {"pages": len(pages)}


def _pdf_paragraphs(lines, pnums):
    """pdftotext often emits no blank line between paragraphs. Rebuild breaks from line shape:
    a short line ending a sentence closes a paragraph; a short numbered/named line is a heading."""
    lens = sorted(len(l.strip()) for l in lines if len(l.strip()) > 20)
    if not lens:
        return lines, pnums
    med = lens[len(lens) // 2]
    out, op = [], []
    prev = ""
    for l, p in zip(lines, pnums):
        s = l.strip()
        if not s:
            out.append(""); op.append(p); prev = ""; continue
        strong = (SECTION_RE.match(s) or NUM_HEAD_RE.match(s) or REF_HEAD_RE.match(s)) and guess_heading(s)
        if strong and len(s) < 0.75 * med:
            out += ["", s, ""]; op += [p, p, p]; prev = ""; continue
        if prev and re.search(r"[.!?:\"”)]$", prev) and len(prev) < 0.8 * med:
            out.append(""); op.append(p)
        elif prev and re.match(r"^(Keywords|Index Terms|Key words)\b", prev, re.I):
            out.append(""); op.append(p)
        elif re.match(r"^\s*\[\d{1,4}\]\s", s) or CAPTION_RE.match(s) and len(prev) < 0.9 * med:
            out.append(""); op.append(p)
        out.append(l); op.append(p)
        prev = s
    return out, op


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def from_docx(path):
    import docx
    try:
        d = docx.Document(path)
    except Exception as e:
        raise ExtractError("This Word file could not be opened: %s" % e)
    body = d.element.body
    blocks, styled = [], 0
    style_names = {}
    for s in d.styles:
        try:
            style_names[s.style_id] = s.name or ""
        except Exception:
            pass
    def ptext(p):
        parts = []
        for el in p.iter(W + "t", W + "tab", W + "br", W + "cr"):
            if el.tag == W + "t":
                parts.append(el.text or "")
            else:
                parts.append(" ")
        return clean("".join(parts)).strip()
    def pstyle(p):
        ps = p.find(W + "pPr")
        sid, outline = "", None
        if ps is not None:
            st = ps.find(W + "pStyle")
            if st is not None:
                sid = st.get(W + "val") or ""
            ol = ps.find(W + "outlineLvl")
            if ol is not None:
                try:
                    outline = int(ol.get(W + "val")) + 1
                except (TypeError, ValueError):
                    pass
        return style_names.get(sid, sid), outline
    for el in body.iterchildren():
        if el.tag == W + "p":
            t = ptext(el)
            if not t:
                continue
            name, outline = pstyle(el)
            low = name.lower()
            m = re.match(r"heading\s*(\d)", low)
            if low == "title":
                blocks.append(B("title", t, 0)); styled += 1
            elif m or (outline and outline < 9 and len(t.split()) < 20):
                blocks.append(B("h", t, int(m.group(1)) if m else outline)); styled += 1
            elif "caption" in low or CAPTION_RE.match(t) and len(t.split()) < 60:
                blocks.append(B("cap", t, 0))
            else:
                blocks.append(B("para", t, 0))
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                cells = []
                for tc in tr.iter(W + "tc"):
                    cells.append(" ".join(ptext(p) for p in tc.iter(W + "p")).strip())
                row = " | ".join(c for c in cells if c)
                if row:
                    blocks.append(B("tbl", row, 0))
    if not styled:  # author never used heading styles: fall back to guessing
        for b in blocks:
            if b["k"] == "para" and guess_heading(b["t"]):
                b["k"], b["l"] = "h", guess_heading(b["t"])
    xml = etree.tostring(body)
    meta = {
        "tracked_changes": xml.count(b"<w:ins ") + xml.count(b"<w:del "),
        "comments": 0,
        "track_capable": True,
    }
    try:
        with zipfile.ZipFile(path) as z:
            if "word/comments.xml" in z.namelist():
                meta["comments"] = z.read("word/comments.xml").count(b"<w:comment ")
    except zipfile.BadZipFile:
        pass
    return mark_refs(blocks), meta


def from_doc(path):
    try:
        out = subprocess.run(["antiword", "-w", "0", path], capture_output=True, timeout=120)
    except FileNotFoundError:
        raise ExtractError("antiword is not installed, so .doc files can't be read. Save the file as .docx.")
    if out.returncode != 0:
        raise ExtractError("This .doc file could not be read. Save it as .docx and try again.")
    return text_to_blocks(clean(out.stdout.decode("utf8", "replace"))), {}


TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def from_odt(path):
    try:
        with zipfile.ZipFile(path) as z:
            root = etree.fromstring(z.read("content.xml"))
    except Exception as e:
        raise ExtractError("This OpenDocument file could not be opened: %s" % e)
    def otext(el):
        out = []
        def walk(e):
            tag = etree.QName(e).localname if isinstance(e.tag, str) else ""
            if tag == "annotation" or tag == "tracked-changes":
                return
            if e.text and tag not in ("s", "tab"):
                out.append(e.text)
            if tag == "s":
                out.append(" " * int(e.get("{%s}c" % TEXT_NS, "1")))
            if tag in ("tab", "line-break"):
                out.append(" ")
            for c in e:
                walk(c)
                if c.tail:
                    out.append(c.tail)
        walk(el)
        return clean("".join(out)).strip()
    blocks = []
    for el in root.iter("{%s}h" % TEXT_NS, "{%s}p" % TEXT_NS):
        if any(etree.QName(a).localname == "annotation" for a in el.iterancestors()):
            continue
        t = otext(el)
        if not t:
            continue
        in_table = any(etree.QName(a).localname == "table-cell" for a in el.iterancestors())
        if etree.QName(el).localname == "h":
            blocks.append(B("h", t, int(el.get("{%s}outline-level" % TEXT_NS, "1"))))
        elif in_table:
            blocks.append(B("tbl", t, 0))
        else:
            style = (el.get("{%s}style-name" % TEXT_NS) or "").lower()
            blocks.append(B("cap" if "caption" in style or CAPTION_RE.match(t) else "para", t, 0))
    meta = {
        "tracked_changes": len(root.findall(".//{%s}changed-region" % TEXT_NS)),
        "comments": len(root.findall(".//{%s}annotation" % OFFICE_NS)),
        "track_capable": True,
    }
    return mark_refs(blocks), meta


def html_to_blocks(tree, blocks):
    for el in tree.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        tag = tag.split("}")[-1].lower()
        if tag in ("script", "style", "nav"):
            continue
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = clean(" ".join(el.itertext())).strip()
            if t:
                blocks.append(B("h", re.sub(r"\s+", " ", t), int(tag[1])))
        elif tag in ("p", "li", "blockquote", "figcaption", "dd", "dt"):
            if any((a.tag if isinstance(a.tag, str) else "").split("}")[-1].lower() in ("p", "li", "blockquote") for a in el.iterancestors()):
                continue
            t = re.sub(r"\s+", " ", clean(" ".join(el.itertext()))).strip()
            if t:
                kind = "cap" if tag == "figcaption" or CAPTION_RE.match(t) else "para"
                blocks.append(B(kind, t, 0))
        elif tag == "tr":
            cells = [re.sub(r"\s+", " ", " ".join(c.itertext())).strip() for c in el if isinstance(c.tag, str)]
            row = " | ".join(c for c in cells if c)
            if row:
                blocks.append(B("tbl", clean(row), 0))
        elif tag == "pre":
            blocks.append(B("code", clean("".join(el.itertext())), 0))
    return blocks


def from_html(path):
    with open(path, "rb") as f:
        data = f.read()
    try:
        tree = lhtml.fromstring(data)
    except Exception as e:
        raise ExtractError("This HTML file could not be parsed: %s" % e)
    blocks = html_to_blocks(tree, [])
    if sum(len(b["t"].split()) for b in blocks if b["k"] != "code") < 30:
        body = tree.find(".//body")
        blocks = text_to_blocks(clean((body if body is not None else tree).text_content()))
    t = tree.find(".//title")
    if t is not None and t.text and not any(b["k"] == "h" for b in blocks[:3]):
        blocks.insert(0, B("title", clean(t.text).strip(), 0))
    return mark_refs(blocks), {}


def from_epub(path):
    try:
        z = zipfile.ZipFile(path)
        cont = etree.fromstring(z.read("META-INF/container.xml"))
        opf_path = cont.find(".//{*}rootfile").get("full-path")
        opf = etree.fromstring(z.read(opf_path))
    except Exception as e:
        raise ExtractError("This EPUB could not be opened: %s" % e)
    base = os.path.dirname(opf_path)
    manifest = {i.get("id"): i.get("href") for i in opf.iter("{*}item")}
    blocks = []
    for ir in opf.iter("{*}itemref"):
        href = manifest.get(ir.get("idref"))
        if not href:
            continue
        name = os.path.normpath(os.path.join(base, href)).replace("\\", "/")
        try:
            tree = lhtml.fromstring(z.read(name))
        except Exception:
            continue
        html_to_blocks(tree, blocks)
    return mark_refs(blocks), {}


def from_markdown(text):
    blocks, para, in_code, code = [], [], False, []
    def flush():
        if para:
            t = join_lines(para)
            t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
            t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
            t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
            t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", t)
            t = re.sub(r"`([^`]+)`", r"\1", t)
            t = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", t)
            t = re.sub(r"^>\s?", "", t)
            blocks.append(B("cap" if CAPTION_RE.match(t) else "para", t, 0))
            para.clear()
    for ln in text.split("\n"):
        if ln.strip().startswith("```"):
            flush()
            if in_code:
                blocks.append(B("code", "\n".join(code), 0)); code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(ln); continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
        if m:
            flush(); blocks.append(B("h", m.group(2), len(m.group(1)))); continue
        if re.match(r"^\s*([-*_]\s*){3,}$", ln) or not ln.strip():
            flush(); continue
        if re.match(r"^\s*\|.*\|\s*$", ln):
            flush()
            if not re.match(r"^\s*\|[\s:|-]+\|\s*$", ln):
                blocks.append(B("tbl", " | ".join(c.strip() for c in ln.strip().strip("|").split("|")), 0))
            continue
        if re.match(r"^\s*(?:[-*+]|\d+\.)\s+", ln) and para:
            flush()
        para.append(ln)
    flush()
    return mark_refs(blocks)


def from_latex(text):
    text = re.sub(r"(?<!\\)%.*", "", text)
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, re.S)
    body = m.group(1) if m else text
    lv = {"part": 1, "chapter": 1, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}
    if "\\chapter" in body:
        lv.update(section=2, subsection=3, subsubsection=4)
    body = re.sub(r"\\begin\{abstract\}", "\n\nAbstract\n\n", body)
    body = re.sub(r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}",
                  lambda m: "\n\n@@H%d@@%s\n\n" % (lv[m.group(1)], m.group(2)), body)
    body = re.sub(r"\\caption\{([^}]*)\}", r"\n\nFigure: \1\n\n", body)
    body = re.sub(r"\\cite[pt]?\{([^}]*)\}", lambda m: "[" + m.group(1) + "]", body)
    body = re.sub(r"\\ref\{[^}]*\}", "1", body)
    body = re.sub(r"\\(emph|textbf|textit|underline|texttt)\{([^}]*)\}", r"\2", body)
    body = re.sub(r"\\begin\{(equation|align|figure|table|tabular)\*?\}.*?\\end\{\1\*?\}", " ", body, flags=re.S)
    body = re.sub(r"\$[^$]*\$", "x", body)
    body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", body)
    body = body.replace("{", "").replace("}", "").replace("~", " ").replace("``", "“").replace("''", "”")
    blocks = []
    for b in text_to_blocks(clean(body)):
        m = re.match(r"^@@H(\d)@@(.*)", b["t"])
        if m:
            blocks.append(B("h", m.group(2).strip(), int(m.group(1))))
        else:
            blocks.append(b)
    return mark_refs(blocks), {"approximate": True}


def from_rtf(data):
    s = data.decode("latin-1", "replace")
    s = re.sub(r"\{\\\*[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", s)
    s = re.sub(r"\{\\(fonttbl|colortbl|stylesheet|info)[\s\S]*?\}\s*\}", "", s)
    s = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), s)
    s = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), s)
    s = re.sub(r"\\(par|line)\b ?", "\n\n", s)
    s = re.sub(r"\\tab\b ?", " ", s)
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", s)
    s = s.replace("{", "").replace("}", "").replace("\\\\", "\\")
    return text_to_blocks(clean(s), keep_spacing=True)


def rows_to_blocks(sheet_name, rows, blocks):
    blocks.append(B("h", "Sheet: %s" % sheet_name, 1))
    for row in rows:
        cells = ["" if c is None else clean(str(c)).strip() for c in row]
        short = []
        for c in cells:
            if not c:
                continue
            if len(c.split()) >= 12:
                if short:
                    blocks.append(B("tbl", " | ".join(short), 0)); short = []
                for part in re.split(r"\n\s*\n", c):
                    if part.strip():
                        blocks.append(B("para", re.sub(r"\s+", " ", part).strip(), 0))
            else:
                short.append(c)
        if short:
            blocks.append(B("tbl", " | ".join(short), 0))


def from_xlsx(path):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise ExtractError("This Excel file could not be opened: %s" % e)
    blocks = []
    for ws in wb.worksheets:
        rows_to_blocks(ws.title, ws.iter_rows(values_only=True), blocks)
    return blocks, {"sheets": len(wb.worksheets)}


def from_xls(path):
    import xlrd
    try:
        wb = xlrd.open_workbook(path)
    except Exception as e:
        raise ExtractError("This .xls file could not be opened: %s" % e)
    blocks = []
    for sh in wb.sheets():
        rows_to_blocks(sh.name, (sh.row_values(i) for i in range(sh.nrows)), blocks)
    return blocks, {"sheets": wb.nsheets}


def from_csv(text, delim=None):
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=delim or ",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if delim == "\t" else csv.excel
    blocks = []
    rows_to_blocks("data", csv.reader(io.StringIO(text), dialect), blocks)
    return blocks


def decode(data):
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return clean(data.decode(enc))
        except UnicodeDecodeError:
            continue
    return clean(data.decode("utf-8", "replace"))


def extract(path, filename):
    ext = os.path.splitext(filename.lower())[1]
    if ext not in FORMATS:
        raise ExtractError("Unsupported file type %r. Supported: %s" % (ext or filename, ", ".join(sorted(FORMATS))))
    meta = {}
    if ext == ".pdf":
        blocks, meta = from_pdf(path)
    elif ext == ".docx":
        blocks, meta = from_docx(path)
    elif ext == ".doc":
        blocks, meta = from_doc(path)
    elif ext == ".odt":
        blocks, meta = from_odt(path)
    elif ext in (".html", ".htm"):
        blocks, meta = from_html(path)
    elif ext == ".epub":
        blocks, meta = from_epub(path)
    elif ext in (".xlsx", ".xlsm"):
        blocks, meta = from_xlsx(path)
    elif ext == ".xls":
        blocks, meta = from_xls(path)
    else:
        with open(path, "rb") as f:
            data = f.read()
        if ext == ".rtf":
            blocks = from_rtf(data)
        elif ext in (".md", ".markdown"):
            blocks = from_markdown(decode(data))
        elif ext == ".tex":
            blocks, meta = from_latex(decode(data))
        elif ext in (".csv", ".tsv"):
            blocks = from_csv(decode(data), "\t" if ext == ".tsv" else None)
        else:
            blocks = text_to_blocks(decode(data), keep_spacing=True)
    meta["format"] = FORMATS[ext]
    meta["ext"] = ext
    meta["spacing_reliable"] = ext in (".txt", ".md", ".markdown", ".docx", ".odt", ".rtf")
    blocks = [b for b in blocks if b["t"].strip()]
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv") and not any(b["k"] == "h" for b in blocks):
        for b in blocks:
            if b["k"] == "para" and guess_heading(b["t"]):
                b["k"], b["l"] = "h", guess_heading(b["t"])
        mark_refs(blocks)
    # first heading-like line of a plain document becomes the title
    if blocks and not any(b["k"] == "title" for b in blocks) and ext not in (".xlsx", ".xlsm", ".xls", ".csv", ".tsv"):
        first = blocks[0]
        if first["k"] in ("h", "para") and len(first["t"].split()) <= 25 and not first["t"].endswith(".") and not SECTION_RE.match(first["t"]):
            first["k"], first["l"] = "title", 0
    if not blocks:
        raise ExtractError("No readable text was found in this file.")
    return blocks, meta
