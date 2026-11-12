# Blue Pencil

A rule-based manuscript reviewer that runs entirely in the browser. Drop in a paper, chapter or book (PDF, Word, OpenDocument, RTF, text, Markdown, LaTeX, HTML, EPUB, Excel or CSV). It checks 134 rules across structure, clarity, style, grammar, mechanics, evidence, consistency and completeness, then gives a verdict with every finding located and explained.

It uses no AI and no server: your files never leave your device, and the same file with the same settings always gives the same score and review ID.

## How it works

- `py/` holds the review engine. It is the same Python as the Raspberry Pi build and runs in [Pyodide](https://pyodide.org) inside a Web Worker (`worker.js`). `py/bridge.py` adapts it to the browser.
- PDFs are read with pdf.js, whose output is rebuilt to match `pdftotext`. Old binary `.doc` files aren't supported; save them as `.docx`.
- `backend.js` answers the page's `/api/...` calls locally. Documents, reports, history and presets are stored in IndexedDB.
- The first visit downloads about 12 MB (Pyodide and lxml). After that it's cached.

## Your own review

- **Looks fine**: on any finding, accept it after checking it yourself. It stops counting toward the score and the verdict caps, giving back its share of the rule's lost points. Undo restores it.
- **Your decision**: record your own verdict (Accept, Minor, Major, Reject) next to the automated recommendation. It shows in history and in the reports.
- **Rewrites**: accept or reject each suggested edit. Your choices are saved with the document and applied to the revised download and the report.
- Each document has one history entry. Re-running it with new settings replaces its previous review.

## Reports and review IDs

- **PDF report** and **Report (.md)** give a professional, action-first report: the verdict, the changes needed (each with the exact passage, where it is, and what to do), ready-to-apply edits, and scores by area.
- Every review has a review ID. Paste it under **Settings from a review ID** to apply that review's exact settings to this paper or a new one. On the same paper, it also restores the findings you accepted.

## Backups and other devices

Click **Save & load**:

- **Download backup** saves your reviews, presets and (optionally) documents to a `.json` file.
- **Load backup…** compares the file with this browser and offers:
  - **Merge**, which keeps everything from both. Review IDs are fingerprints, so the same review on two devices is recognised as one. Clashing preset names are kept under a new name.
  - **Replace**, which overwrites this browser's data with the file.

  Both can be undone from the toast.

A dot next to **Save & load** means something changed since your last backup.

## Build and run locally

```sh
./build.sh                       # writes _site/ (catalog.json from the rules, cache-busting version)
cd _site && python3 -m http.server 8000
```

Pushing to `main` deploys to GitHub Pages through `.github/workflows/pages.yml`. In the repo, set **Settings → Pages → Source** to **GitHub Actions**.

## Updating the engine

The engine files in `py/` are copied from the Pi build (`engine.py`, `extract.py`, `rules.py`, `rules_more.py`, `measures_more.py`, `analysis.py`, `lexicon.py`, `examples.py`) and must stay unchanged. Copy new versions in and rebuild. The dictionaries in `py/dict/` are the SCOWL word lists (`wamerican`/`wbritish`), the same files the Pi uses. Their licence is in `py/dict/COPYING-SCOWL`.
