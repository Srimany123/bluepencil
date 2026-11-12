// Blue Pencil review engine worker: runs the Python engine (py/*.py) in Pyodide, off the page's main thread.
/* global importScripts, loadPyodide */
"use strict";
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
const PY_FILES = ["engine.py", "extract.py", "rules.py", "rules_more.py", "measures_more.py", "analysis.py", "lexicon.py", "examples.py", "report.py", "bridge.py"];
const WHEELS = {
  docx: ["python_docx-1.2.0-py3-none-any.whl"],
  xlsx: ["et_xmlfile-2.0.0-py3-none-any.whl", "openpyxl-3.1.5-py2.py3-none-any.whl"],
};
const V = new URL(self.location.href).searchParams.get("v") || "";
const base = new URL(".", self.location.href).href;
const at = (p) => base + p + (V ? "?v=" + V : "");

let py = null, bridge = null;
const extras = new Set();
const say = (msg) => self.postMessage({ progress: msg });

async function fetchText(p) { const r = await fetch(at(p)); if (!r.ok) throw new Error("Couldn't load " + p + " (" + r.status + ")"); return r.text(); }

const ready = (async () => {
  say("Downloading the review engine (about 12 MB the first time, cached after)…");
  importScripts(PYODIDE + "pyodide.js");
  py = await loadPyodide({ indexURL: PYODIDE });
  say("Loading the parser…");
  await py.loadPackage(["lxml"]);
  py.FS.mkdirTree("/bp"); py.FS.mkdirTree("/usr/share/dict"); py.FS.mkdirTree("/work");
  const [srcs, dicts] = await Promise.all([
    Promise.all(PY_FILES.map((f) => fetchText("py/" + f))),
    Promise.all(["american-english", "british-english"].map((f) => fetchText("py/dict/" + f))),
  ]);
  PY_FILES.forEach((f, i) => py.FS.writeFile("/bp/" + f, srcs[i]));
  py.FS.writeFile("/usr/share/dict/american-english", dicts[0]);
  py.FS.writeFile("/usr/share/dict/british-english", dicts[1]);
  say("Loading the dictionaries…");
  py.runPython("import sys; sys.path.insert(0, '/bp')");
  bridge = py.pyimport("bridge");
  bridge.warm();
  say("");
})();

async function need(ext) {
  const want = ext === ".docx" ? "docx" : ext === ".xlsx" || ext === ".xlsm" ? "xlsx" : ext === ".xls" ? "xls" : null;
  if (!want || extras.has(want)) return;
  say("Loading the " + (want === "docx" ? "Word" : "Excel") + " reader…");
  if (want === "xls") await py.loadPackage(["xlrd"]);
  else {
    await py.loadPackage(want === "docx" ? ["micropip", "typing-extensions"] : ["micropip"]);
    const micropip = py.pyimport("micropip");
    for (const w of WHEELS[want]) await micropip.install(at("vendor/wheels/" + w));
  }
  extras.add(want);
}

const J = (s) => JSON.parse(s);
// The page sends a document's blocks only when the worker doesn't already hold it (books are megabytes).
const NEED = { __needRec: true };
const has = (docId, rec) => rec || bridge.cached(docId);
const CMDS = {
  async extract({ name, ext, bytes, pdfText }) {
    await need(ext);
    const path = "/work/upload" + ext;
    py.FS.writeFile(path, new Uint8Array(bytes));
    try { return J(bridge.extract_file(path, name, pdfText || null)); }
    finally { try { py.FS.unlink(path); } catch (e) { /* gone */ } }
  },
  summary({ docId, rec, warnings }) { if (!has(docId, rec)) return NEED; return J(bridge.summary(docId, rec || null, !!warnings)); },
  review({ docId, rec, settings }) { if (!has(docId, rec)) return NEED; return J(bridge.review(docId, rec || null, JSON.stringify(settings || {}), 1.5)); },
  markdown({ rep, rejected, decision }) { return bridge.markdown(JSON.stringify(rep), JSON.stringify(rejected || []), JSON.stringify(decision || null)); },
  reportModel({ rep, rejected, decision }) { return J(bridge.report_model(JSON.stringify(rep), JSON.stringify(rejected || []), JSON.stringify(decision || null))); },
  revised({ docId, rec, settings, reject }) { if (!has(docId, rec)) return NEED; return bridge.revised(docId, rec || null, JSON.stringify(settings), JSON.stringify(reject || [])); },
  mergeSettings({ settings }) { return J(bridge.merge_settings(JSON.stringify(settings || {}))); },
  cached({ docId }) { return bridge.cached(docId); },
  ping() { return true; },
};

self.onmessage = async (ev) => {
  const { id, cmd, args } = ev.data;
  try {
    await ready;
    const result = await CMDS[cmd](args || {});
    self.postMessage({ id, result });
  } catch (e) {
    self.postMessage({ id, error: String((e && e.message) || e).split("\n").filter(Boolean).slice(-1)[0] || "Engine error" });
  }
};
