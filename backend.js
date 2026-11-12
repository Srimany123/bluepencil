// Blue Pencil · in-browser backend.
// The page (app.js) talks to "/api/..." exactly as it does on the Pi. Here those calls are answered locally:
// documents, reports, history and presets live in IndexedDB, and the Python engine runs in a Web Worker (Pyodide).
"use strict";
(() => {
  const V = document.querySelector('meta[name="bp-version"]')?.content || "";
  const realFetch = window.fetch.bind(window);
  const enc = new TextEncoder();
  const MAX_BYTES = 60 * 1024 * 1024;
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const text = (body, type = "text/markdown; charset=utf-8") => new Response(body, { status: 200, headers: { "Content-Type": type } });
  const fail = (msg, status = 400) => json({ error: msg }, status);
  const safeName = (n, dflt) => (n.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 60)) || dflt;
  const validId = (x) => /^[0-9a-f]{16}$/.test(x || "");

  // ------------------------------------------------------------ IndexedDB
  const STORES = ["docs", "reports", "kv"];
  const dbp = new Promise((res, rej) => {
    const r = indexedDB.open("bluepencil", 1);
    r.onupgradeneeded = () => STORES.forEach((s) => r.result.objectStoreNames.contains(s) || r.result.createObjectStore(s));
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
  const done = (req) => new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });
  async function tx(store, mode, fn) {
    const db = await dbp; const t = db.transaction(store, mode); const out = fn(t.objectStore(store));
    await new Promise((res, rej) => { t.oncomplete = res; t.onerror = () => rej(t.error); t.onabort = () => rej(t.error || new Error("Storage aborted")); });
    return out instanceof IDBRequest ? out.result : out;
  }
  const get = (s, k) => tx(s, "readonly", (o) => o.get(k));
  const put = (s, k, v) => tx(s, "readwrite", (o) => { o.put(v, k); });
  const del = (s, k) => tx(s, "readwrite", (o) => { o.delete(k); });
  const keys = (s) => tx(s, "readonly", (o) => o.getAllKeys());
  const all = async (s) => { const ks = await keys(s); const vs = await tx(s, "readonly", (o) => o.getAll()); return ks.map((k, i) => [k, vs[i]]); };
  const clear = (s) => tx(s, "readwrite", (o) => { o.clear(); });
  const kv = async (k, dflt) => (await get("kv", k)) ?? dflt;
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});

  // ------------------------------------------------------------ engine worker
  let worker = null, seq = 0;
  const waiting = new Map();
  let progressMsg = "";
  function progress(msg) {
    progressMsg = msg;
    const start = document.getElementById("view-start"), ds = document.getElementById("drop-status"), rs = document.getElementById("run-state");
    if (start && !start.hidden && ds && busyUpload) { ds.textContent = msg || ds.textContent; ds.classList.remove("err"); }
    else if (rs && msg) rs.textContent = msg;
    const eng = document.getElementById("engine-state"); if (eng) eng.textContent = msg || "";
  }
  function startWorker() {
    if (worker) return;
    worker = new Worker("worker.js" + (V ? "?v=" + V : ""));
    worker.onmessage = (ev) => {
      const m = ev.data;
      if ("progress" in m) { progress(m.progress); return; }
      const w = waiting.get(m.id); if (!w) return; waiting.delete(m.id);
      m.error ? w.rej(new Error(m.error)) : w.res(m.result);
    };
    worker.onerror = (e) => { progress("The review engine failed to start: " + (e.message || "unknown error")); for (const w of waiting.values()) w.rej(new Error("The review engine failed to start. Reload the page to try again.")); waiting.clear(); worker = null; };
  }
  function call(cmd, args, transfer) {
    startWorker();
    return new Promise((res, rej) => { const id = ++seq; waiting.set(id, { res, rej }); worker.postMessage({ id, cmd, args }, transfer || []); });
  }
  // commands that need a document: send its blocks only if the worker doesn't hold it already
  async function withDoc(cmd, docId, args) {
    let r = await call(cmd, { docId, ...args });
    if (r && r.__needRec) {
      const rec = await get("docs", docId);
      if (!rec) throw Object.assign(new Error("That document is no longer stored. Upload it again."), { status: 404 });
      r = await call(cmd, { docId, rec: JSON.stringify(rec), ...args });
    }
    return r;
  }

  // ------------------------------------------------------------ PDF text (pdf.js stands in for pdftotext)
  let pdfjs = null;
  let GAP = Infinity;   // blank line when a gap exceeds GAP × line spacing (pdftotext rarely emits them; Infinity matches it best)
  function loadScript(src) { return new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = () => rej(new Error("Couldn't load the PDF reader. Check your connection.")); document.head.append(s); }); }
  async function pdfText(bytes) {
    if (!pdfjs) {
      await loadScript("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js");
      pdfjs = window.pdfjsLib;
      pdfjs.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    }
    let pdf;
    try { pdf = await pdfjs.getDocument({ data: bytes, isEvalSupported: false }).promise; }
    catch (e) { throw new Error(/password/i.test(e.message) ? "This PDF is password-protected. Remove the password and try again." : "This PDF could not be read: " + e.message); }
    const pages = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      if (i % 10 === 1) progress(`Reading PDF page ${i} of ${pdf.numPages}…`);
      const page = await pdf.getPage(i);
      const tc = await page.getTextContent();
      // rebuild lines like pdftotext: items on one baseline form a line; a much larger gap than the usual
      // line spacing becomes a blank line (pdftotext's block break); each page ends with "\n\n\f"
      const lines = []; let cur = "", curY = null, prevY = null, lastEnd = null, minGap = null, curH = 0, prevH = 0;
      const endLine = () => { lines.push(cur.replace(/\s+$/, "")); prevY = curY; prevH = curH; cur = ""; curY = null; lastEnd = null; };
      for (const it of tc.items) {
        if (!("str" in it)) continue;
        if (!it.str) { if (it.hasEOL && cur) endLine(); continue; }
        const x = it.transform[4], y = it.transform[5], h = Math.abs(it.transform[3]) || it.height || 10;
        if (cur && curY !== null && Math.abs(y - curY) > Math.max(2, 0.5 * h)) endLine();
        if (!cur) {
          if (!it.str.trim()) continue;
          if (prevY !== null) {
            const gap = prevY - y;
            // pdftotext starts a new block (blank line) where a line in larger type follows smaller type, e.g. a heading
            if (gap > 0) { if ((minGap !== null && gap > GAP * minGap) || (prevH && h > 1.1 * prevH)) lines.push(""); minGap = minGap === null ? gap : Math.min(minGap, gap); }
          }
          curY = y; curH = h;
        } else if (lastEnd !== null && !/\s$/.test(cur) && !/^\s/.test(it.str) && x - lastEnd > 0.15 * h) cur += " ";
        cur += it.str; lastEnd = x + (it.width || 0);
        if (it.hasEOL) endLine();
      }
      if (cur) endLine();
      pages.push(lines.length ? lines.join("\n") + "\n\n\f" : "\f");
      page.cleanup();
    }
    await pdf.destroy();
    return pages.join("");
  }

  window.BP_pdfText = pdfText;   // for debugging text extraction from the console

  // ------------------------------------------------------------ documents
  let catalog = null;
  const getCatalog = async () => catalog || (catalog = await (await realFetch("catalog.json" + (V ? "?v=" + V : ""))).json());
  const hexsha = async (bytes) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((b) => b.toString(16).padStart(2, "0")).join("");
  let busyUpload = false;

  async function ingest(bytes, name, displayName) {
    const ext = (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    const cat = await getCatalog();
    if (!cat.formats[ext]) return fail(`“${ext || name}” files aren't supported. Use one of: ${Object.keys(cat.formats).sort().join(", ")}.`);
    if (bytes.byteLength > MAX_BYTES) return fail("That file is larger than 60 MB.", 413);
    const sha = await hexsha(bytes), docId = sha.slice(0, 16);
    if (!(await get("docs", docId))) {
      let pdf = null;
      if (ext === ".pdf") pdf = await pdfText(bytes.slice(0));
      if (!worker || progressMsg) progress(progressMsg || "Starting the review engine…");
      const r = await call("extract", { name, ext, bytes, pdfText: pdf }, [bytes]);
      if (r.error) return fail(r.error, 422);
      await put("docs", docId, { name: displayName || name, sha, blocks: r.blocks, meta: r.meta, uploaded: Math.floor(Date.now() / 1000) });
    }
    progress("");
    return json(await withDoc("summary", docId, { warnings: true }));
  }

  // ------------------------------------------------------------ routes
  async function route(method, path, body, query) {
    let m;
    if (path === "/api/catalog") return json(await getCatalog());
    if (path === "/api/upload" && method === "POST") {
      const f = body && body.get && body.get("file");
      if (!f || !f.name) return fail("Choose a file to upload.");
      busyUpload = true;
      try { return await ingest(await f.arrayBuffer(), f.name.split(/[\\/]/).pop()); } finally { busyUpload = false; }
    }
    if (path === "/api/sample" && method === "POST") {
      busyUpload = true;
      try {
        const r = await realFetch("samples/sample-paper.txt" + (V ? "?v=" + V : ""));
        const out = await ingest(await r.arrayBuffer(), "sample-paper.txt", "Sample: sleep & memory paper.txt");
        if (out.ok) { const j = await out.json(); j.warnings = ["This is a built-in sample with deliberate problems, so you can see every kind of finding."]; return json(j); }
        return out;
      } finally { busyUpload = false; }
    }
    if ((m = path.match(/^\/api\/doc\/(.+)$/))) {
      if (!validId(m[1])) return fail("Bad document id.");
      if (!(await get("docs", m[1]))) return fail("That document is no longer stored. Upload it again.", 404);
      return json(await withDoc("summary", m[1], {}));
    }
    if (path === "/api/review" && method === "POST") {
      const { doc_id: docId, settings } = JSON.parse(body || "{}");
      if (!validId(docId)) return fail("Bad document id.");
      const rec = await get("docs", docId);
      if (!rec) return fail("That document is no longer stored. Upload it again.", 404);
      const rep = await withDoc("review", docId, { settings });
      if (rep.pending) return json(rep, 202);
      const words = rep._words; delete rep._words;
      await saveReport(rep, docId, rec, words);
      return json(rep);
    }
    if ((m = path.match(/^\/api\/report\/([^/]+)$/))) {
      let fp = m[1]; const md = fp.endsWith(".md"); fp = fp.replace(/\.(md|json)$/, "");
      if (!validId(fp)) return fail("Bad review id.");
      const rep = await get("reports", fp);
      if (!rep) return fail("Review not found.", 404);
      if (md) return text(await call("markdown", { rep }));
      return json(rep);
    }
    if ((m = path.match(/^\/api\/revised\/([^/]+)$/))) {
      const fp = m[1]; if (!validId(fp)) return fail("Bad review id.");
      const rep = await get("reports", fp); if (!rep) return fail("Review not found.", 404);
      if (!(await get("docs", rep.doc.doc_id))) return fail("The document is no longer stored.", 404);
      let reject = (query.get("reject") || "").split(",").filter(Boolean);
      if (method === "POST") reject = reject.concat((JSON.parse(body || "{}").reject || []).map(String));
      return text(await withDoc("revised", rep.doc.doc_id, { settings: rep.settings, reject }));
    }
    if (path === "/api/history" && method === "GET") return json(await kv("history", []));
    if ((m = path.match(/^\/api\/history\/(.+)$/)) && method === "DELETE") {
      if (!validId(m[1])) return fail("Bad review id.");
      await put("kv", "history", (await kv("history", [])).filter((h) => h.fp !== m[1]));
      await del("reports", m[1]); await pruneDocs();
      return json({ ok: true });
    }
    if (path === "/api/presets" && method === "GET") return json(await kv("presets", {}));
    if (path === "/api/presets" && method === "POST") {
      const b = JSON.parse(body || "{}"), name = String(b.name || "").trim().slice(0, 60);
      if (!name) return fail("Give the preset a name.");
      const p = await kv("presets", {}); p[name] = await call("mergeSettings", { settings: b.settings || {} });
      await put("kv", "presets", p);
      return json({ ok: true, presets: Object.keys(p).sort() });
    }
    if ((m = path.match(/^\/api\/presets\/(.+)$/)) && method === "DELETE") {
      const p = await kv("presets", {}); delete p[decodeURIComponent(m[1])]; await put("kv", "presets", p);
      return json({ ok: true });
    }
    return fail("Not found.", 404);
  }

  async function saveReport(rep, docId, rec, words) {
    const fp = rep.fingerprint;
    await put("reports", fp, rep);
    let hist = (await kv("history", [])).filter((h) => h.fp !== fp);
    hist.unshift({ fp, doc_id: docId, name: rec.name, profile: rep.settings.profile, strictness: rep.settings.strictness,
      score: rep.overall, verdict: rep.verdict, at: Math.floor(Date.now() / 1000), words: words ?? rep.doc.words });
    await setHistory(hist);
  }
  async function setHistory(hist) {   // keep history readable: newest 100, and drop reports and documents nothing points at
    hist.sort((a, b) => b.at - a.at);
    const keep = hist.slice(0, 100);
    for (const old of hist.slice(100)) await del("reports", old.fp);
    await put("kv", "history", keep);
    await pruneDocs();
  }
  async function pruneDocs() {
    const hist = await kv("history", []), live = new Set(hist.map((h) => h.doc_id)), recent = Date.now() / 1000 - 86400 * 2;
    for (const [id, d] of await all("docs")) if (!live.has(id) && (d.uploaded || 0) < recent) await del("docs", id);
  }

  // ------------------------------------------------------------ fetch shim
  window.fetch = async (input, opts = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const i = url.pathname.indexOf("/api/");
    if (url.origin !== location.origin || i < 0) return realFetch(input, opts);
    try { return await route((opts.method || "GET").toUpperCase(), url.pathname.slice(i), opts.body, url.searchParams); }
    catch (e) { return fail(e.message || String(e), e.status || 500); }
  };
  // report links (<a href="/api/report/…">) become local downloads
  document.addEventListener("click", async (ev) => {
    const a = ev.target.closest && ev.target.closest('a[href*="/api/report/"]'); if (!a) return;
    ev.preventDefault();
    const url = new URL(a.getAttribute("href"), location.href), md = url.pathname.endsWith(".md");
    const fp = url.pathname.split("/").pop().replace(/\.(md|json)$/, "");
    const rep = await get("reports", fp); if (!rep) return;
    const safe = safeName(rep.doc.name, "review");
    if (md) download(await call("markdown", { rep }), `${safe}-review-${fp.slice(0, 8)}.md`, "text/markdown");
    else download(JSON.stringify(rep, null, 1), `${safe}-review-${fp.slice(0, 8)}.json`, "application/json");
  });
  function download(data, name, type) {
    const a = document.createElement("a"); a.href = URL.createObjectURL(data instanceof Blob ? data : new Blob([data], { type }));
    a.download = name; document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  // ------------------------------------------------------------ backup: save file download / load / merge
  const toast = (msg, undo) => {
    const t = document.getElementById("toast"); t.replaceChildren(msg);
    if (undo) { const b = document.createElement("button"); b.type = "button"; b.className = "toastbtn"; b.textContent = "Undo"; b.onclick = () => { t.hidden = true; undo(); }; t.append(" ", b); }
    t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), undo ? 8000 : 2600);
  };
  async function snapshot(withDocs) {
    const history = await kv("history", []), presets = await kv("presets", {});
    const reports = Object.fromEntries(await all("reports"));
    const docs = {};
    if (withDocs) for (const [id, d] of await all("docs")) docs[id] = d;
    return { app: "blue-pencil", kind: "backup", format: 1, exportedAt: Date.now(), history, presets, reports, docs };
  }
  async function restore(snap) {
    for (const s of STORES) await clear(s);
    for (const [k, v] of Object.entries(snap.docs || {})) await put("docs", k, v);
    for (const [k, v] of Object.entries(snap.reports || {})) await put("reports", k, v);
    await put("kv", "presets", snap.presets || {}); await put("kv", "history", snap.history || []);
    const lastExport = await kv("lastExport", 0); if (lastExport) await put("kv", "lastExport", lastExport);
  }
  function readBackup(textData) {
    const j = JSON.parse(textData);
    if (!j || j.app !== "blue-pencil" || j.kind !== "backup") throw new Error("not a backup");
    const ok = (o) => o && typeof o === "object" && !Array.isArray(o);
    return { history: Array.isArray(j.history) ? j.history.filter((h) => h && validId(h.fp)) : [], presets: ok(j.presets) ? j.presets : {},
      reports: ok(j.reports) ? Object.fromEntries(Object.entries(j.reports).filter(([k, v]) => validId(k) && ok(v))) : {},
      docs: ok(j.docs) ? Object.fromEntries(Object.entries(j.docs).filter(([k, v]) => validId(k) && ok(v) && Array.isArray(v.blocks))) : {}, exportedAt: j.exportedAt || 0 };
  }
  const sameSettings = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function mergeSnap(file, local) {
    // review IDs are fingerprints of (file, settings, engine), so the same review on two devices has the same ID
    const out = { history: [], presets: { ...local.presets }, reports: { ...file.reports, ...local.reports }, docs: { ...file.docs, ...local.docs } };
    const byFp = new Map();
    for (const h of [...file.history, ...local.history]) { const o = byFp.get(h.fp); if (!o || h.at > o.at) byFp.set(h.fp, h); }
    out.history = [...byFp.values()].filter((h) => out.reports[h.fp]).sort((a, b) => b.at - a.at);
    const renamed = [];
    for (const [name, s] of Object.entries(file.presets)) {
      if (!(name in out.presets)) { out.presets[name] = s; continue; }
      if (sameSettings(out.presets[name], s)) continue;
      let n = 2, alt; do alt = `${name} (${n++})`; while (alt in out.presets && !sameSettings(out.presets[alt], s));
      if (!(alt in out.presets)) { out.presets[alt] = s; renamed.push(alt); }
    }
    out.renamed = renamed;
    return out;
  }
  const kb = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  const stats = (s) => ({ reviews: s.history.length, docs: Object.keys(s.docs || {}).length, presets: Object.keys(s.presets).length,
    last: s.history.reduce((a, h) => Math.max(a, h.at * 1000), 0) });
  const when = (t) => t ? new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "–";

  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) { if (v == null || v === false) continue; if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else if (k === "class") n.className = v; else n.setAttribute(k, v === true ? "" : v); }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k.nodeType ? k : document.createTextNode(String(k)));
    return n;
  }
  let dlg, bodyEl, fileIn;
  function ensureDialog() {
    if (dlg) return;
    dlg = h("dialog", { id: "saveDlg", "aria-labelledby": "saveTitle" }, h("h3", { id: "saveTitle" }, "Save & load"), bodyEl = h("div", { class: "dlg-body" }));
    fileIn = h("input", { type: "file", accept: ".json,application/json", hidden: true, onchange: async (e) => {
      const f = e.target.files[0]; e.target.value = ""; if (!f) return;
      try { compare(readBackup(await f.text()), f.name); } catch (err) { toast("That file isn't a Blue Pencil backup"); }
    } });
    document.body.append(dlg, fileIn);
  }
  const table = (rows) => h("div", { class: "htable-wrap" }, h("table", { class: "htable" },
    h("thead", null, h("tr", null, h("th", null, ""), h("th", { class: "num" }, "Reviews"), h("th", { class: "num" }, "Documents"), h("th", { class: "num" }, "Presets"), h("th", null, "Latest review"))),
    h("tbody", null, rows.map(([label, s]) => { const t = stats(s); return h("tr", null, h("th", { scope: "row" }, label), h("td", { class: "num" }, t.reviews), h("td", { class: "num" }, t.docs), h("td", { class: "num" }, t.presets), h("td", null, when(t.last))); }))));
  async function openDialog() {
    ensureDialog();
    const local = await snapshot(true), size = enc.encode(JSON.stringify(local)).length, lastExport = await kv("lastExport", 0), lastChange = await kv("lastChange", 0);
    let withDocs = true;
    const sizeNote = h("span", { class: "muted" }, kb(size));
    const lite = (await snapshot(false));
    bodyEl.replaceChildren(
      h("p", { class: "muted" }, "Reviews, documents and presets are saved in this browser. To continue on another device, download a backup and load it there. Loading can merge two backups, because the same file reviewed with the same settings always gets the same review ID."),
      table([["This browser", local]]),
      h("p", { class: "muted small" }, lastExport ? (lastExport >= lastChange ? `Your last backup (${when(lastExport)}) is up to date.` : `Changed since your last backup (${when(lastExport)}).`) : "You haven't downloaded a backup from this browser yet."),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: true, onchange: (e) => { withDocs = e.target.checked; sizeNote.textContent = kb(enc.encode(JSON.stringify(withDocs ? local : lite)).length); } }),
        " Include documents (needed to reopen or re-run reviews on the other device) · ", sizeNote),
      h("div", { class: "dlg-actions" },
        h("button", { type: "button", class: "btn ghost", onclick: () => dlg.close() }, "Close"),
        h("button", { type: "button", class: "btn ghost", onclick: () => fileIn.click() }, "Load backup…"),
        h("button", { type: "button", class: "btn", onclick: async () => {
          const snap = withDocs ? local : lite;
          download(JSON.stringify(snap), `blue-pencil-backup-${new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-")}.json`, "application/json");
          await put("kv", "lastExport", Date.now()); refreshBadge(); toast("Backup downloaded"); openDialog();
        } }, "Download backup")));
    if (!dlg.open) dlg.showModal();
  }
  async function compare(file, name) {
    ensureDialog();
    const local = await snapshot(true), merged = mergeSnap(file, local), localEmpty = !local.history.length && !Object.keys(local.presets).length;
    const nothingNew = stats(merged).reviews === stats(local).reviews && Object.keys(merged.docs).length === Object.keys(local.docs).length && !merged.renamed.length && Object.keys(merged.presets).length === Object.keys(local.presets).length;
    const apply = async (snap, label) => {
      const before = local; await restore(snap); await put("kv", "lastChange", Date.now());
      dlg.close(); await refreshApp();
      toast(label, async () => { await restore(before); await refreshApp(); toast("Undone"); });
    };
    bodyEl.replaceChildren(
      h("p", null, h("b", null, name), file.exportedAt ? h("span", { class: "muted" }, " · saved " + when(file.exportedAt)) : null),
      table([["Backup file", file], ["This browser", local], localEmpty ? null : ["After merge", merged]].filter(Boolean)),
      nothingNew ? h("p", { class: "ok" }, "This browser already has everything in that backup.") :
        h("p", { class: "muted small" }, localEmpty ? "This browser has nothing saved yet, so loading copies the backup in." :
          "Merge keeps every review and document from both. A preset with the same name but different settings is kept under a new name. Replace throws away what this browser has. Both can be undone right after."),
      Object.keys(file.docs).length ? null : h("p", { class: "muted small" }, "This backup has no documents, so its reviews can be read and downloaded but not re-run until you upload the files again."),
      h("div", { class: "dlg-actions" },
        h("button", { type: "button", class: "btn ghost", onclick: openDialog }, "Back"),
        nothingNew ? null : localEmpty ? h("button", { type: "button", class: "btn", onclick: () => apply(file, "Backup loaded") }, "Load backup") : [
          h("button", { type: "button", class: "btn ghost", onclick: () => apply(file, "Replaced this browser's reviews with the backup") }, "Replace"),
          h("button", { type: "button", class: "btn", onclick: () => apply(merged, "Merged the backup into this browser" + (merged.renamed.length ? ` (renamed presets: ${merged.renamed.join(", ")})` : "")) }, "Merge")]));
    if (!dlg.open) dlg.showModal();
  }
  async function refreshApp() { if (window.BP_refresh) await window.BP_refresh(); refreshBadge(); }
  async function refreshBadge() {
    const b = document.getElementById("nav-save"); if (!b) return;
    const lastExport = await kv("lastExport", 0), lastChange = await kv("lastChange", 0);
    b.classList.toggle("stale", lastChange > lastExport);
    b.title = lastChange > lastExport ? "Reviews changed since your last backup" : "Save & load reviews";
  }
  // anything that changes stored reviews or presets marks the backup stale
  const markChange = () => put("kv", "lastChange", Date.now()).then(refreshBadge);
  const origRoute = route;
  route = async (method, path, ...rest) => { const r = await origRoute(method, path, ...rest); if (method !== "GET" && r.ok && r.status === 200 && !/\/api\/(upload|sample|revised)/.test(path)) markChange(); return r; };

  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("nav-save"); if (b) b.addEventListener("click", openDialog);
    refreshBadge();
    window.addEventListener("dragover", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
    window.addEventListener("drop", async (e) => {   // drop a backup anywhere except the upload box
      const f = e.dataTransfer && e.dataTransfer.files[0]; if (!f || !/\.json$/i.test(f.name) || e.target.closest("#drop")) return;
      e.preventDefault();
      try { compare(readBackup(await f.text()), f.name); } catch (err) { toast("That file isn't a Blue Pencil backup"); }
    });
    // warm the engine in the background so the first upload is quick
    (window.requestIdleCallback || ((f) => setTimeout(f, 300)))(() => call("ping").catch(() => {}));
  });
})();
