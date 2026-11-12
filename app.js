"use strict";
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const fmt = (v, nd = 1) => v == null ? "–" : (Math.abs(v - Math.round(v)) < 1e-9 ? Math.round(v).toLocaleString() : Number(v).toLocaleString(undefined, { maximumFractionDigits: nd, minimumFractionDigits: 0 }));
  const VLABEL = { accept: "Accept", minor: "Minor revision", major: "Major revision", reject: "Reject" };
  const SEVS = ["critical", "major", "minor", "note"];
  const SEVRANK = { critical: 0, major: 1, minor: 2, note: 3 };
  const TABS = [["summary", "Summary"], ["findings", "Findings"], ["rewrites", "Rewrites"], ["map", "Map & flow"], ["metrics", "Metrics"]];
  const freshFilters = (sort = "rule") => ({ sev: new Set(SEVS), q: "", cat: "", rule: "", sec: "", para: null, rep: false, sort });

  const S = {
    cat: null, doc: null, settings: null, report: null, history: [], presets: {},
    seq: 0, timer: null, open: new Set(), tab: "summary", f: freshFilters(), showAll: new Set(),
    rejected: new Set(), revRule: "", heatMetric: "q", sel: null,
  };

  // ------------------------------------------------------------------ api
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    let body = null;
    try { body = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((body && body.error) || (r.status === 524 || r.status === 502 ? "The review took too long; try again." : `Request failed (${r.status}).`));
    return body;
  }
  const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 2600);
  }

  // ------------------------------------------------------------------ colour helpers (from theme tokens)
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const hex = (h) => { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map((c) => c + c).join(""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
  const mix = (a, b, t) => { const A = hex(a), B = hex(b); return `rgb(${A.map((v, i) => Math.round(v + (B[i] - v) * Math.max(0, Math.min(1, t)))).join(",")})`; };
  function diverge(v, lo, mid, hi) {
    if (v == null) return cssVar("--sunk");
    const bad = cssVar("--heat-bad"), neu = cssVar("--heat-mid"), good = cssVar("--heat-good");
    return v <= mid ? mix(bad, neu, (v - lo) / (mid - lo)) : mix(neu, good, (v - mid) / (hi - mid));
  }
  const seqc = (t, hue = "--heat-good") => mix(cssVar("--heat-zero"), cssVar(hue), t);

  // ------------------------------------------------------------------ views
  function show(view) {
    $("#view-start").hidden = view !== "start";
    $("#view-review").hidden = view !== "review";
    $("#view-history").hidden = view !== "history";
    if (view === "history") renderHistory();
    if (view === "start") renderRecent();
    window.scrollTo({ top: 0 });
  }
  function setupDrop() {
    const drop = $("#drop"), input = $("#file");
    ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (ev) => { const f = ev.dataTransfer.files[0]; if (f) upload(f); });
    input.addEventListener("change", () => { if (input.files[0]) upload(input.files[0]); input.value = ""; });
    $("#sample").addEventListener("click", async () => {
      status("Loading the sample…");
      try { openDoc(await api("/api/sample", { method: "POST" })); } catch (e) { status(e.message, true); }
    });
  }
  function status(msg, err) { const el = $("#drop-status"); el.textContent = msg; el.classList.toggle("err", !!err); }
  async function upload(file) {
    status(`Reading ${file.name} (${(file.size / 1024).toFixed(0)} KB)…`);
    const fd = new FormData(); fd.append("file", file);
    try { const doc = await api("/api/upload", { method: "POST", body: fd }); status(""); openDoc(doc); }
    catch (e) { status(e.message, true); }
  }
  function openDoc(doc, settings) {
    S.doc = doc;
    S.settings = settings ? clone(settings) : clone(S.cat.profiles[doc.suggested_profile || "paper"].settings);
    if (!settings) S.settings.asof_year = new Date().getFullYear();
    S.report = null; S.showAll.clear(); S.open.clear(); S.rejected.clear(); S.sel = null; S.f = freshFilters(); S.tab = S.pendingTab || "summary"; S.pendingTab = null;
    show("review");
    renderSettings();
    $("#report").innerHTML = `<div class="card"><p class="empty-note">Reviewing ${esc(doc.name)}…</p></div>`;
    runNow();
  }
  function renderRecent() {
    $("#recent").innerHTML = S.history.length ? `<h3>Recent reviews</h3>` + historyTable(S.history.slice(0, 5)) : "";
  }

  // ------------------------------------------------------------------ settings rail
  const getPath = (o, p) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
  function setPath(o, p, v) { const ks = p.split("."); let a = o; for (const k of ks.slice(0, -1)) a = a[k]; a[ks[ks.length - 1]] = v; }
  const strictLabel = (k) => k < 0.8 ? "Lenient" : k < 1.2 ? "Standard" : k < 1.6 ? "Strict" : "Very strict";

  function renderSettings() {
    const s = S.settings, d = S.doc, cat = S.cat, prof = cat.profiles[s.profile];
    const rulesByCat = {};
    cat.rules.forEach((r) => (rulesByCat[r.cat] = rulesByCat[r.cat] || []).push(r));
    const v = s.verdict, bandW = (a, b) => Math.max(0, b - a);
    const presetNames = Object.keys(S.presets).sort();
    $("#settings").innerHTML = `
      <div class="grp"><h4>Document</h4>
        <div class="docline"><div class="docname">${esc(d.name)}</div>
          <div class="docmeta">${esc(d.format || "")}${d.pages ? ` · ${d.pages} pages` : ""} · ${fmt(d.words)} words · ${fmt(d.headings)} headings · ${fmt(d.references)} references</div>
          ${d.present.length ? `<div class="docmeta">Found: ${d.present.map(esc).join(", ")}</div>` : ""}</div>
        ${(d.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("")}</div>
      <div class="grp"><h4>Review as</h4>
        <div class="seg" role="group" aria-label="Document type">
          ${["paper", "chapter", "book", "fiction", "general"].filter((id) => cat.profiles[id]).map((id) => `<button type="button" data-profile="${id}" aria-pressed="${id === s.profile}">${esc(cat.profiles[id].name)}</button>`).join("")}
        </div>
        <p class="blurb">${esc(prof.blurb)}${d.suggested_profile === s.profile ? "" : ` <span class="muted">(The file looks like a ${esc(cat.profiles[d.suggested_profile].name.toLowerCase())}.)</span>`}</p></div>
      <div class="grp"><h4>Strictness &amp; scoring</h4>
        <div class="row"><label for="st-k">${strictLabel(s.strictness)}</label><span class="big-val" id="st-kv">×${s.strictness.toFixed(2)}</span></div>
        <input type="range" id="st-k" min="0.6" max="1.8" step="0.05" value="${s.strictness}" data-path="strictness" data-type="num">
        <div class="stops"><span data-k="0.6">Lenient</span><span data-k="1">Standard</span><span data-k="1.4">Strict</span><span data-k="1.8">Very strict</span></div>
        <div class="row"><label for="agg">Category score</label><select id="agg" data-path="aggregation" data-type="str">
          <option value="rms" ${s.aggregation !== "mean" ? "selected" : ""}>Weighted RMS</option>
          <option value="mean" ${s.aggregation === "mean" ? "selected" : ""}>Weighted average</option></select></div>
        <p class="blurb">Strictness scales every threshold. RMS makes a few badly failing rules count more than many perfect ones.</p></div>
      <div class="grp"><h4>Verdict bands</h4>
        <div class="bands" aria-hidden="true" id="bandbar"><span class="b-reject" style="flex:${bandW(0, v.major)}"></span><span class="b-major" style="flex:${bandW(v.major, v.minor)}"></span><span class="b-minor" style="flex:${bandW(v.minor, v.accept)}"></span><span class="b-accept" style="flex:${bandW(v.accept, 100)}"></span></div>
        <div class="trio">
          <label for="v-accept">Accept at or above<input type="number" id="v-accept" min="0" max="100" step="1" value="${v.accept}" data-path="verdict.accept" data-type="num"></label>
          <label for="v-minor">Minor revision from<input type="number" id="v-minor" min="0" max="100" step="1" value="${v.minor}" data-path="verdict.minor" data-type="num"></label>
          <label for="v-major">Major revision from<input type="number" id="v-major" min="0" max="100" step="1" value="${v.major}" data-path="verdict.major" data-type="num"></label>
        </div><p class="blurb">Below the major threshold the verdict is Reject.</p></div>
      <div class="grp"><h4>Hard limits</h4>
        <div class="row"><label for="g-ca">Critical findings allowed for Accept</label><input type="number" id="g-ca" min="0" step="1" value="${s.gates.max_critical_accept}" data-path="gates.max_critical_accept" data-type="num"></div>
        <div class="row"><label for="g-cm">Critical findings allowed for Minor revision</label><input type="number" id="g-cm" min="0" step="1" value="${s.gates.max_critical_minor}" data-path="gates.max_critical_minor" data-type="num"></div>
        <div class="row"><label for="g-fl">Any category below this caps at Major</label><input type="number" id="g-fl" min="0" max="100" step="1" value="${s.gates.category_floor}" data-path="gates.category_floor" data-type="num"></div>
        <div class="row"><label for="g-yr">Review year (for reference age)</label><input type="number" id="g-yr" min="1900" max="2100" step="1" value="${s.asof_year}" data-path="asof_year" data-type="num"></div></div>
      <div class="grp"><h4>Category weights</h4>
        ${cat.categories.map((c) => `<div class="catw"><label for="cw-${c.id}">${esc(c.name)}</label>
            <input type="range" id="cw-${c.id}" min="0" max="3" step="0.1" value="${s.categories[c.id]}" data-path="categories.${c.id}" data-type="num">
            <output id="cwv-${c.id}">${Number(s.categories[c.id]).toFixed(1)}</output></div>`).join("")}
        <p class="blurb">0 leaves a category out of the score.</p></div>
      <div class="grp"><h4>Rules · ${cat.rules.length}</h4>
        <input type="search" id="rule-q" placeholder="Find a rule" aria-label="Find a rule">
        ${cat.categories.map((c) => `
          <details class="rules-cat"${S.open.has("rc-" + c.id) ? " open" : ""} data-open="rc-${c.id}">
            <summary><span>${esc(c.name)}</span><span class="muted">${rulesByCat[c.id].filter((r) => s.rules[r.id].on).length}/${rulesByCat[c.id].length} on</span></summary>
            <div class="rail-actions"><button type="button" class="btn quiet small" data-all-on="${c.id}">All on</button><button type="button" class="btn quiet small" data-all-off="${c.id}">All off</button></div>
            ${rulesByCat[c.id].map((r) => ruleControls(r, s.rules[r.id])).join("")}
          </details>`).join("")}</div>
      <div class="grp"><h4>Presets</h4>
        <div class="row"><select id="preset-sel" class="grow" aria-label="Saved presets"><option value="">Load a saved preset…</option>${presetNames.map((n) => `<option>${esc(n)}</option>`).join("")}</select>
          <button type="button" class="btn quiet small" id="preset-del" ${presetNames.length ? "" : "disabled"}>Delete</button></div>
        <div class="row"><input type="text" id="preset-name" class="grow" placeholder="Preset name, e.g. Nature submission" maxlength="60"><button type="button" class="btn small" id="preset-save">Save</button></div>
        <div class="rail-actions"><button type="button" class="btn quiet small" id="reset">Reset to ${esc(prof.name)} defaults</button></div></div>`;
    updateRuleChips();
  }

  function ruleControls(r, rc) {
    const id = r.id, p = `rules.${id}`;
    const th = r.mode === "range"
      ? `<div class="row"><label for="${id}-lo">Target from</label><input type="number" id="${id}-lo" step="any" value="${rc.lo}" data-path="${p}.lo" data-type="num"></div>
         <div class="row"><label for="${id}-hi">Target to</label><input type="number" id="${id}-hi" step="any" value="${rc.hi}" data-path="${p}.hi" data-type="num"></div>
         <div class="row"><label for="${id}-tol">Zero at ${r.rel ? "(fraction beyond edge)" : "(units beyond edge)"}</label><input type="number" id="${id}-tol" step="any" min="0" value="${rc.tol}" data-path="${p}.tol" data-type="num"></div>`
      : `<div class="row"><label for="${id}-good">Full marks ${r.mode === "lower" ? "at or below" : "at or above"} <span class="muted">(${esc(r.unit)})</span></label><input type="number" id="${id}-good" step="any" value="${rc.good}" data-path="${p}.good" data-type="num"></div>
         <div class="row"><label for="${id}-bad">Zero ${r.mode === "lower" ? "at or above" : "at or below"}</label><input type="number" id="${id}-bad" step="any" value="${rc.bad}" data-path="${p}.bad" data-type="num"></div>`;
    const params = Object.entries(r.params).map(([k, spec]) => {
      const v = rc.params[k], pid = `${id}-p-${k}`, pp = `${p}.params.${k}`;
      if (spec.type === "int") return `<div class="row"><label for="${pid}">${esc(spec.label)}</label><input type="number" id="${pid}" step="1" min="${spec.min ?? ""}" max="${spec.max ?? ""}" value="${v}" data-path="${pp}" data-type="num"></div>`;
      if (spec.type === "bool") return `<div class="row"><label for="${pid}">${esc(spec.label)}</label><input type="checkbox" id="${pid}" ${v ? "checked" : ""} data-path="${pp}" data-type="bool"></div>`;
      if (spec.type === "choice") return `<div class="row"><label for="${pid}">${esc(spec.label)}</label><select id="${pid}" data-path="${pp}" data-type="str">${spec.choices.map((c) => `<option ${c === v ? "selected" : ""}>${c}</option>`).join("")}</select></div>`;
      if (spec.type === "words") return `<label for="${pid}">${esc(spec.label)}</label><textarea id="${pid}" data-path="${pp}" data-type="words" spellcheck="false">${esc(v.join("\n"))}</textarea>`;
      if (spec.type === "sections") return `<div><div class="muted">${esc(spec.label)}</div><div class="secgrid">${S.cat.sections.map((c) => `<label><input type="checkbox" data-path="${pp}" data-type="section" data-sec="${c}" ${v.includes(c) ? "checked" : ""}>${esc(c)}</label>`).join("")}</div></div>`;
      return "";
    }).join("");
    return `<details class="rule${rc.on ? "" : " off"}" id="rc-${id}"${S.open.has("r-" + id) ? " open" : ""} data-open="r-${id}" data-rname="${esc((r.name + " " + r.id + " " + r.desc).toLowerCase())}">
        <summary><input type="checkbox" aria-label="Use rule ${esc(r.name)}" ${rc.on ? "checked" : ""} data-path="${p}.on" data-type="bool">
          <span class="rname">${esc(r.name)}</span><span class="chip" data-chip="${id}"></span></summary>
        <div class="rule-body"><p>${esc(r.desc)}</p>${exHTML(r)}
          <div class="row"><label for="${id}-w">Weight in category</label><input type="number" id="${id}-w" min="0" max="3" step="0.1" value="${rc.w}" data-path="${p}.w" data-type="num"></div>
          <div class="row"><label for="${id}-sev">Severity of its findings</label><select id="${id}-sev" data-path="${p}.sev" data-type="str">${S.cat.severities.map((x) => `<option ${x === rc.sev ? "selected" : ""}>${x}</option>`).join("")}</select></div>
          ${th}${params}</div></details>`;
  }

  function updateRuleChips() {
    const rep = S.report;
    $$("[data-chip]").forEach((el) => {
      const r = rep && rep.rules.find((x) => x.id === el.dataset.chip);
      el.className = "chip";
      if (!r) { el.textContent = ""; return; }
      if (r.status === "off") { el.textContent = "off"; return; }
      if (r.status === "na") { el.textContent = "n/a"; return; }
      el.textContent = fmt(r.score, 0);
      el.classList.add(r.score >= 90 ? "good" : r.score >= 60 ? "warn" : "bad");
    });
  }

  function onSettingInput(ev) {
    const el = ev.target, path = el.dataset.path;
    if (!path) return;
    const type = el.dataset.type;
    let val;
    if (type === "num") { if (el.value === "") return; val = Number(el.value); if (!isFinite(val)) return; }
    else if (type === "bool") val = el.checked;
    else if (type === "words") val = el.value.split(/\n|,/).map((w) => w.trim()).filter(Boolean);
    else if (type === "section") {
      const cur = new Set(getPath(S.settings, path));
      el.checked ? cur.add(el.dataset.sec) : cur.delete(el.dataset.sec);
      val = S.cat.sections.filter((c) => cur.has(c));
    } else val = el.value;
    setPath(S.settings, path, val);
    if (path === "strictness") { $("#st-kv").textContent = "×" + val.toFixed(2); $('label[for="st-k"]').textContent = strictLabel(val); }
    if (path.startsWith("categories.")) $("#cwv-" + path.split(".")[1]).textContent = val.toFixed(1);
    if (path.startsWith("verdict.")) {
      const v = S.settings.verdict, sp = $$("#bandbar span");
      [[0, v.major], [v.major, v.minor], [v.minor, v.accept], [v.accept, 100]].forEach(([a, b], i) => (sp[i].style.flex = Math.max(0, b - a)));
    }
    if (path.endsWith(".on")) el.closest(".rule").classList.toggle("off", !val);
    schedule(ev.type === "input" && (type === "num" || type === "words") ? 650 : 250);
  }

  function setupRail() {
    const box = $("#settings");
    box.addEventListener("input", (ev) => {
      if (ev.target.id === "rule-q") {
        const q = ev.target.value.trim().toLowerCase();
        $$(".rule", box).forEach((r) => (r.hidden = !!q && !r.dataset.rname.includes(q)));
        $$(".rules-cat", box).forEach((c) => { if (q) c.open = $$(".rule", c).some((r) => !r.hidden); });
        return;
      }
      onSettingInput(ev);
    });
    box.addEventListener("change", (ev) => { if (ev.target.type === "checkbox" || ev.target.tagName === "SELECT") onSettingInput(ev); });
    box.addEventListener("click", async (ev) => {
      const t = ev.target;
      if (t.matches("summary input[type=checkbox]")) { ev.stopPropagation(); return; }
      if (t.dataset.allOn || t.dataset.allOff) {
        const c = t.dataset.allOn || t.dataset.allOff, on = !!t.dataset.allOn;
        S.cat.rules.filter((r) => r.cat === c).forEach((r) => (S.settings.rules[r.id].on = on));
        S.open.add("rc-" + c); renderSettings(); runNow(); return;
      }
      if (t.dataset.profile) {
        const keep = { strictness: S.settings.strictness, verdict: S.settings.verdict, gates: S.settings.gates, asof_year: S.settings.asof_year, aggregation: S.settings.aggregation };
        S.settings = Object.assign(clone(S.cat.profiles[t.dataset.profile].settings), clone(keep));
        renderSettings(); runNow();
      }
      if (t.dataset.k) {
        S.settings.strictness = Number(t.dataset.k);
        $("#st-k").value = t.dataset.k; $("#st-kv").textContent = "×" + Number(t.dataset.k).toFixed(2);
        $('label[for="st-k"]').textContent = strictLabel(Number(t.dataset.k)); runNow();
      }
      if (t.id === "reset") {
        S.settings = clone(S.cat.profiles[S.settings.profile].settings); S.settings.asof_year = new Date().getFullYear();
        renderSettings(); runNow(); toast("Settings reset to profile defaults");
      }
      if (t.id === "preset-save") {
        const name = $("#preset-name").value.trim();
        if (!name) { $("#preset-name").focus(); toast("Type a name for the preset first"); return; }
        try { await post("/api/presets", { name, settings: S.settings }); S.presets = await api("/api/presets"); renderSettings(); toast(`Saved preset “${name}”`); }
        catch (e) { toast(e.message); }
      }
      if (t.id === "preset-del") {
        const name = $("#preset-sel").value;
        if (!name) { toast("Pick a preset to delete"); return; }
        if (t.dataset.armed !== name) { t.dataset.armed = name; t.textContent = "Confirm delete"; return; }
        await api("/api/presets/" + encodeURIComponent(name), { method: "DELETE" });
        S.presets = await api("/api/presets"); renderSettings(); toast(`Deleted preset “${name}”`);
      }
    });
    box.addEventListener("change", (ev) => {
      if (ev.target.id === "preset-sel" && ev.target.value) { S.settings = clone(S.presets[ev.target.value]); renderSettings(); runNow(); toast(`Loaded preset “${ev.target.value}”`); }
    });
    box.addEventListener("toggle", (ev) => { const k = ev.target.dataset && ev.target.dataset.open; if (k) ev.target.open ? S.open.add(k) : S.open.delete(k); }, true);
  }

  // ------------------------------------------------------------------ running reviews
  function schedule(ms) { clearTimeout(S.timer); S.timer = setTimeout(runNow, ms); setState("Changes pending…", true); }
  function setState(msg, busy) { const el = $("#run-state"); el.textContent = msg; el.classList.toggle("busy", !!busy); }

  async function runNow() {
    clearTimeout(S.timer);
    const my = ++S.seq;
    setState("Reviewing…", true);
    try {
      let rep;
      for (;;) {
        rep = await post("/api/review", { doc_id: S.doc.doc_id, settings: S.settings });
        if (my !== S.seq) return;
        if (!rep.pending) break;
        setState(`Measuring ${rep.done} of ${rep.total} rules…`, true);
      }
      S.report = rep;
      setState(`Up to date · ${rep.elapsed_s}s`);
      history.replaceState(null, "", "#review=" + rep.fingerprint);
      renderReport(); updateRuleChips();
      S.history = await api("/api/history").catch(() => S.history);
    } catch (e) {
      if (my !== S.seq) return;
      setState("Failed");
      $("#report").innerHTML = `<div class="card"><p class="empty-note">${esc(e.message)}</p></div>`;
    }
  }

  // ------------------------------------------------------------------ report shell
  function renderReport() {
    const r = S.report, s = r.settings, vb = s.verdict, totals = r.severity_totals;
    const pinX = Math.max(0, Math.min(100, r.overall)), rv = r.revisions || { total: 0 };
    const nf = SEVS.reduce((a, k) => a + totals[k], 0);
    $("#report").innerHTML = `
      <section class="card verdict" aria-label="Verdict">
        <div><div class="eyebrow">Recommendation</div><div class="stamp v-${r.verdict}">${esc(r.verdict_label)}</div></div>
        <div><div class="eyebrow">Overall score</div>
          <div class="scorebig">${fmt(r.overall)}<small> / 100</small></div>
          <div class="vscale" aria-hidden="true">
            <div class="bands"><span class="b-reject" style="flex:${vb.major}"></span><span class="b-major" style="flex:${Math.max(0, vb.minor - vb.major)}"></span><span class="b-minor" style="flex:${Math.max(0, vb.accept - vb.minor)}"></span><span class="b-accept" style="flex:${Math.max(0, 100 - vb.accept)}"></span></div>
            <div class="pin" style="left:${pinX}%"></div>
            <div class="ticks"><span style="left:0">0</span><span style="left:${vb.major}%">${fmt(vb.major)}</span><span style="left:${vb.minor}%">${fmt(vb.minor)}</span><span style="left:${vb.accept}%">${fmt(vb.accept)}</span><span style="left:100%">100</span></div>
          </div></div>
        <ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <div class="metaline">
          <span>${esc(S.cat.profiles[s.profile].name)} · ${strictLabel(s.strictness)} ×${s.strictness.toFixed(2)} · ${s.aggregation === "mean" ? "average" : "RMS"}</span>
          <span class="sevs">${SEVS.map((k) => `<span class="sev ${k}">${totals[k]} ${k}</span>`).join("")}</span>
          <span title="Same file + same settings + engine ${esc(r.engine)} always gives this ID">Review ID <span class="mono">${esc(r.fingerprint)}</span></span>
          <span class="actions"><a class="btn quiet small" href="/api/report/${r.fingerprint}.md">Report (.md)</a><a class="btn quiet small" href="/api/report/${r.fingerprint}?download=1">JSON</a></span>
        </div>
      </section>
      <nav class="tabs" role="tablist" aria-label="Report sections">
        ${TABS.map(([id, label]) => `<button type="button" role="tab" aria-selected="${S.tab === id}" data-tab="${id}">${label}${id === "rewrites" ? ` <span class="count">+${rv.total}</span>` : id === "findings" ? ` <span class="count">${nf}</span>` : ""}</button>`).join("")}
      </nav>
      <div id="tabpanel" role="tabpanel"></div>
      <p class="foot">Engine ${esc(r.engine)} · ${S.cat.rules.length} rules · reviewed in ${r.elapsed_s}s in your browser · same file + same settings → same review ID.</p>`;
    renderTab();
  }
  function renderTab() {
    const fn = { summary: tabSummary, findings: tabFindings, rewrites: tabRewrites, map: tabMap, metrics: tabMetrics }[S.tab];
    $("#tabpanel").innerHTML = fn();
    if (S.tab === "map") drawCharts();
    $$("[role=tab]").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === S.tab));
  }
  function goTab(t) { S.tab = t; renderTab(); $(".tabs").scrollIntoView({ behavior: "smooth", block: "start" }); }
  const exHTML = (meta) => meta && meta.ex ? `<div class="ex"><span class="ex-bad">✗ ${esc(meta.ex[0])}</span><span class="ex-good">✓ ${esc(meta.ex[1])}</span></div>` : "";
  const ruleMeta = () => Object.fromEntries(S.cat.rules.map((x) => [x.id, x]));

  // ------------------------------------------------------------------ summary
  function tabSummary() {
    const r = S.report, vb = r.settings.verdict;
    return `
      <section class="card" aria-label="Scores by category"><div class="eyebrow">Scores by category</div>
        <div class="cats">${r.categories.map((c) => `
          <div class="catrow${c.score == null ? " na" : ""}" data-fcat="${c.id}" role="button" tabindex="0" title="${esc(c.q)}">
            <span>${esc(c.name)}<small>×${Number(c.w).toFixed(1)}</small></span>
            <span class="bar"><span class="fill" style="width:${c.score ?? 0}%"></span><span class="tick" style="left:${vb.minor}%"></span><span class="tick" style="left:${vb.accept}%"></span></span>
            <span class="num">${c.score == null ? "n/a" : fmt(c.score, 0)}</span></div>`).join("")}</div>
        <div class="legend">Ticks mark Minor revision (${fmt(vb.minor)}) and Accept (${fmt(vb.accept)}). Click a category to see its findings.</div></section>
      <div class="split">
        <section class="card"><div class="eyebrow">Required revisions</div>${todoList(r.required, "Nothing blocking. No critical or major rule is failing.")}</section>
        <section class="card"><div class="eyebrow">Suggested improvements</div>${todoList(r.suggested, "No minor issues worth mentioning.")}</section>
      </div>
      ${r.strengths.length ? `<section class="card"><div class="eyebrow">Strengths</div><ul class="strengths">${r.strengths.map((x) => `<li><b>${esc(x.name)}.</b> ${esc(x.detail)}</li>`).join("")}</ul></section>` : ""}`;
  }
  function todoList(items, empty) {
    if (!items.length) return `<p class="empty-note">${esc(empty)}</p>`;
    const meta = ruleMeta();
    return `<ol class="todo">${items.map((x) => `<li>
        <span class="t-name"><button type="button" data-frule="${x.id}">${esc(x.name)}</button> ${x.crit ? `<span class="sev critical">${x.crit} critical</span>` : ""}</span>
        <span class="lost" title="Points of overall score this rule costs">−${fmt(x.lost, 1)} pts</span>
        <span class="t-detail">${esc(x.detail)}</span><span class="t-fix">${esc(x.fix)}</span>
        ${meta[x.id] && meta[x.id].ex ? `<span class="t-ex">${exHTML(meta[x.id])}</span>` : ""}</li>`).join("")}</ol>`;
  }

  // ------------------------------------------------------------------ findings
  function changeIndex() {
    const m = new Map();
    ((S.report.revisions || {}).changes || []).forEach((c) => m.set(`${c.rule}:${c.b}:${c.fa}`, c));
    return m;
  }
  function allFindings() {
    const out = [];
    S.report.rules.forEach((r) => (r.findings || []).forEach((f) => out.push({ r, f })));
    return out;
  }
  const anyFilter = () => { const F = S.f; return !!(F.q || F.cat || F.rule || F.sec || F.para != null || F.rep || F.sev.size < 4); };
  function filtered(ci) {
    const F = S.f, q = F.q.toLowerCase();
    return allFindings().filter(({ r, f }) =>
      F.sev.has(f.sev) && (!F.cat || r.cat === F.cat) && (!F.rule || r.id === F.rule) && (!F.sec || f.sec === F.sec) &&
      (F.para == null || f.b === F.para) && (!F.rep || ci.has(`${r.id}:${f.b}:${f.as}`)) &&
      (!q || (f.msg + " " + (f.x || "") + " " + (f.sec || "") + " " + r.name).toLowerCase().includes(q)));
  }
  function tabFindings() {
    const r = S.report, F = S.f, totals = r.severity_totals, ci = changeIndex();
    const secs = [...new Set(allFindings().map(({ f }) => f.sec).filter(Boolean))];
    const scored = r.rules.filter((x) => x.status === "scored" && x.n);
    const list = filtered(ci);
    let body;
    if (F.sort === "rule") {
      const byRule = new Map();
      list.forEach((x) => { if (!byRule.has(x.r.id)) byRule.set(x.r.id, []); byRule.get(x.r.id).push(x); });
      const meta = ruleMeta(), af = anyFilter();
      body = S.cat.categories.map((c) => {
        const rs = r.rules.filter((x) => x.cat === c.id && (!af || byRule.has(x.id)));
        if (!rs.length) return "";
        const cs = r.categories.find((x) => x.id === c.id);
        return `<div class="fcat"><h3>${esc(c.name)} <span class="num">${cs.score == null ? "n/a" : fmt(cs.score, 0) + " / 100"}</span></h3>
          <p class="q">${esc(c.q)}</p>${rs.map((x) => ruleCard(x, meta[x.id], byRule.get(x.id) || [], ci)).join("")}</div>`;
      }).join("") || `<p class="empty-note">No findings match these filters.</p>`;
    } else {
      const sorted = list.slice().sort((a, b) => F.sort === "severity"
        ? (SEVRANK[a.f.sev] - SEVRANK[b.f.sev]) || ((a.f.b ?? 1e9) - (b.f.b ?? 1e9))
        : ((a.f.b ?? 1e9) - (b.f.b ?? 1e9)) || ((a.f.as ?? 0) - (b.f.as ?? 0)) || a.r.id.localeCompare(b.r.id));
      const lim = S.showAll.has("flat") ? sorted.length : 150;
      body = sorted.length ? `<ul class="flist">${sorted.slice(0, lim).map(({ r, f }) => findingHTML(f, r, ci.get(`${r.id}:${f.b}:${f.as}`))).join("")}</ul>
        ${sorted.length > lim ? `<button type="button" class="btn quiet small more" data-more="flat">Show all ${sorted.length}</button>` : ""}` : `<p class="empty-note">No findings match these filters.</p>`;
    }
    const selPara = F.para != null ? (r.heat.paras.find((p) => p.b === F.para) || {}).para : null;
    return `<section class="card" aria-label="Findings">
      <div class="ftools">
        ${SEVS.map((k) => `<button type="button" class="sevtoggle" data-sev="${k}" aria-pressed="${F.sev.has(k)}">${k} · ${totals[k]}</button>`).join("")}
        <input type="search" id="fq" placeholder="Search findings" value="${esc(F.q)}" aria-label="Search findings">
      </div>
      <div class="ftools">
        <select id="f-cat" aria-label="Category"><option value="">All categories</option>${S.cat.categories.map((c) => `<option value="${c.id}" ${F.cat === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
        <select id="f-rule" aria-label="Rule"><option value="">All rules</option>${scored.filter((x) => !F.cat || x.cat === F.cat).map((x) => `<option value="${x.id}" ${F.rule === x.id ? "selected" : ""}>${x.id} ${esc(x.name)} (${x.n})</option>`).join("")}</select>
        <select id="f-sec" aria-label="Section"><option value="">All sections</option>${secs.map((x) => `<option ${F.sec === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <select id="f-sort" aria-label="Order"><option value="rule" ${F.sort === "rule" ? "selected" : ""}>Group by rule</option><option value="position" ${F.sort === "position" ? "selected" : ""}>In document order</option><option value="severity" ${F.sort === "severity" ? "selected" : ""}>Most severe first</option></select>
        <label class="inl"><input type="checkbox" id="f-rep" ${F.rep ? "checked" : ""}> Only with a rewrite</label>
        ${selPara != null ? `<button type="button" class="chip clear" data-clear-para="1">¶ ${selPara} ✕</button>` : ""}
        ${anyFilter() ? `<button type="button" class="link" data-clear-all="1">Clear filters</button>` : ""}
        <span class="muted">${list.length.toLocaleString()} shown</span>
      </div>${body}</section>`;
  }
  function threshText(x, meta) {
    const e = x.eff; if (!e) return "";
    const u = meta.unit;
    if (x.mode === "range") return `Measured ${fmt(x.value, 2)} ${u} · target ${fmt(e.lo)}–${fmt(e.hi)} · zero at ${meta.rel ? fmt(e.tol * 100, 0) + "% beyond" : fmt(e.tol, 2) + " beyond"} the edge`;
    if (x.mode === "lower") return `Measured ${fmt(x.value, 2)} ${u} · full marks ≤ ${fmt(e.good, 2)} · zero ≥ ${fmt(e.bad, 2)}`;
    return `Measured ${fmt(x.value, 2)} ${u} · full marks ≥ ${fmt(e.good, 2)} · zero ≤ ${fmt(e.bad, 2)}`;
  }
  function ruleCard(x, meta, items, ci) {
    const id = "rr-" + x.id;
    if (x.status !== "scored") {
      return `<details class="rcard ${x.status}" id="${id}"><summary><span class="rid">${x.id}</span><span class="rtitle">${esc(x.name)}</span>
        <span class="rright"><span class="chip">${x.status === "off" ? "off" : "n/a"}</span></span>
        <span class="rdetail">${x.status === "off" ? "Switched off for this review." : esc(x.detail || "Not applicable.")}</span></summary>
        <div class="rbody"><p class="why">${esc(meta.desc)}</p>${exHTML(meta)}</div></details>`;
    }
    const lim = S.showAll.has(x.id) ? items.length : 12;
    const cls = x.score >= 90 ? "good" : x.score >= 60 ? "warn" : "bad";
    const sevc = SEVS.filter((k) => x.sevc[k]).map((k) => `<span class="sev ${k}">${x.sevc[k]}</span>`).join("");
    const open = S.open.has(id) || !!S.f.rule;
    return `<details class="rcard" id="${id}"${open ? " open" : ""} data-open="${id}">
      <summary><span class="rid">${x.id}</span><span class="rtitle">${esc(x.name)}</span>
        <span class="rright">${x.rewrites ? `<span class="chip plus" title="Automatic rewrites available">+${x.rewrites}</span>` : ""}${sevc}<span class="chip ${cls}">${fmt(x.score, 0)}</span></span>
        <span class="rdetail">${esc(x.detail)}</span></summary>
      <div class="rbody">
        <div class="thresh">${esc(threshText(x, meta))} · weight ${fmt(x.w, 1)} · costs ${fmt(x.lost, 2)} pts</div>
        <p class="why"><b>Why it matters.</b> ${esc(meta.why)} <b>Fix.</b> ${esc(meta.fix)}</p>${exHTML(meta)}
        ${items.length ? `<ul class="flist">${items.slice(0, lim).map(({ f }) => findingHTML(f, null, ci.get(`${x.id}:${f.b}:${f.as}`))).join("")}</ul>` : `<p class="empty-note">${x.n ? "No findings match the filters." : "No individual findings."}</p>`}
        ${items.length > lim ? `<button type="button" class="btn quiet small more" data-more="${x.id}">Show all ${items.length}</button>` : ""}
        ${x.n > (x.findings || []).length ? `<p class="empty-note">Showing the first ${x.findings.length} of ${x.n} findings.</p>` : ""}
      </div></details>`;
  }
  function findingHTML(f, r, ch) {
    const where = [f.page ? `p. ${f.page}` : "", f.para ? `¶ ${f.para}` : "", f.sec ? `§ ${f.sec}` : ""].filter(Boolean).join(" · ");
    let ex = "";
    if (f.x) {
      const hs = f.hs || 0, he = f.he || 0;
      ex = he > hs ? esc(f.x.slice(0, hs)) + "<mark>" + esc(f.x.slice(hs, he)) + "</mark>" + esc(f.x.slice(he)) : esc(f.x);
    }
    const diff = ch ? `<div class="mini-diff" aria-label="Suggested change"><div class="dl del"><span class="g">−</span><span>${esc(ch.old) || "<em>(space)</em>"}</span></div><div class="dl add"><span class="g">+</span><span>${ch.new ? esc(ch.new) : "<em>(deleted)</em>"}</span></div></div>` : "";
    return `<li><div class="fhead"><span class="sev ${f.sev}">${f.sev}</span>${r ? `<span class="chip">${r.id}</span>` : ""}<span class="msg">${esc(f.msg)}</span>${where ? `<span class="where">${esc(where)}</span>` : ""}</div>
      ${ex ? `<p class="excerpt">${ex}</p>` : ""}${diff}${f.fix ? `<div class="ffix">→ ${esc(f.fix)}</div>` : ""}</li>`;
  }

  // ------------------------------------------------------------------ rewrites (git-style diff)
  function tabRewrites() {
    const rv = S.report.revisions;
    if (!rv || !rv.total) return `<section class="card"><div class="eyebrow">Rewrites</div><p class="empty-note">No automatic rewrites with these rules. Rewrites come from rules with an exact fix: wordy phrases, spelling, a/an, doubled words, confused words, contractions, spacing, dashes, Latin abbreviations and more.</p></section>`;
    const names = Object.fromEntries(S.cat.rules.map((r) => [r.id, r.name]));
    const rules = [...new Set(rv.changes.map((c) => c.rule))].sort();
    const byB = new Map();
    rv.changes.filter((c) => !S.revRule || c.rule === S.revRule).forEach((c) => { if (!byB.has(c.b)) byB.set(c.b, []); byB.get(c.b).push(c); });
    const accepted = rv.changes.filter((c) => !S.rejected.has(c.id)).length;
    const blocks = [...byB.entries()].slice(0, S.showAll.has("rev") ? 1e9 : 60);
    return `<section class="card" aria-label="Rewrites">
      <div class="rev-head">
        <div><div class="eyebrow">Suggested rewrites</div>
          <p class="rev-sum"><b>${rv.total.toLocaleString()}</b> change${rv.total === 1 ? "" : "s"} in <b>${rv.blocks.toLocaleString()}</b> paragraph${rv.blocks === 1 ? "" : "s"} · <span class="add-t">+${accepted} accepted</span> · <span class="del-t">−${rv.changes.length - accepted} rejected</span>${rv.shown_blocks < rv.blocks ? ` · the first ${rv.shown_blocks} paragraphs are shown; the download applies all ${rv.total}` : ""}</p></div>
        <div class="rev-actions">
          <select id="rev-rule" aria-label="Filter by rule"><option value="">All rules</option>${rules.map((id) => `<option value="${id}" ${S.revRule === id ? "selected" : ""}>${id} ${esc(names[id] || "")}</option>`).join("")}</select>
          <button type="button" class="btn quiet small" data-rev-all="accept">Accept all</button>
          <button type="button" class="btn quiet small" data-rev-all="reject">Reject all</button>
          <button type="button" class="btn small" id="rev-dl">Download revised (.md)</button>
        </div></div>
      <p class="blurb">Like a git diff: <span class="del-t">− original</span> above, <span class="add-t">+ revised</span> below with every ticked change applied. Untick a change to keep your wording; each change names its rule and reason.</p>
      ${blocks.map(([b, cs]) => diffBlock(b, cs, rv, names)).join("")}
      ${byB.size > blocks.length ? `<button type="button" class="btn quiet small more" data-more="rev">Show all ${byB.size} paragraphs</button>` : ""}
    </section>`;
  }
  function diffBlock(b, cs, rv, names) {
    const text = rv.texts[String(b)] || "", meta = rv.meta[String(b)] || {};
    const all = rv.changes.filter((c) => c.b === b).sort((a, z) => a.a - z.a);
    const live = all.filter((c) => !S.rejected.has(c.id));
    let del = "", add = "", pos = 0;
    for (const c of live) {
      if (c.a < pos) continue;
      const pre = esc(text.slice(pos, c.a));
      del += pre + `<del>${esc(text.slice(c.a, c.z)) || "&nbsp;"}</del>`;
      add += pre + (c.new ? `<ins>${esc(c.new)}</ins>` : "");
      pos = c.z;
    }
    del += esc(text.slice(pos)); add += esc(text.slice(pos));
    const where = [meta.page ? `p. ${meta.page}` : "", meta.para ? `¶ ${meta.para}` : "", meta.sec ? `§ ${meta.sec}` : ""].filter(Boolean).join(" · ");
    return `<div class="diff">
      <div class="diff-head"><span class="mono">@@ ${esc(where || "paragraph")} @@</span><span class="muted">${live.length}/${all.length} applied</span></div>
      <div class="dl del"><span class="g">−</span><p>${del}</p></div>
      <div class="dl add"><span class="g">+</span><p>${live.length ? add : esc(text)}</p></div>
      <ul class="clist">${cs.map((c) => `<li class="${S.rejected.has(c.id) ? "rej" : ""}">
          <label><input type="checkbox" data-chg="${esc(c.id)}" ${S.rejected.has(c.id) ? "" : "checked"}> <span class="chip">${c.rule}</span> <span class="sev ${c.sev}">${c.sev}</span></label>
          <span class="cw"><del>${esc(c.old) || "␣"}</del> → <ins>${c.new ? esc(c.new) : "(delete)"}</ins></span>
          <span class="creason"><b>${esc(names[c.rule] || c.name)}.</b> ${esc(c.reason)} <span class="muted">Why: ${esc(c.why)}</span></span>
        </li>`).join("")}</ul></div>`;
  }
  async function downloadRevised() {
    try {
      const r = await fetch(`/api/revised/${S.report.fingerprint}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reject: [...S.rejected] }) });
      if (!r.ok) throw new Error("Could not build the revised document.");
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (S.doc.name.replace(/\.[^.]+$/, "") || "document") + "-revised.md";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      toast(`Revised document downloaded (${S.report.revisions.total - S.rejected.size} changes applied)`);
    } catch (e) { toast(e.message); }
  }

  // ------------------------------------------------------------------ map & flow
  const HEAT = {
    q: { label: "Quality", desc: "100 = no findings; falls with severity-weighted findings per 100 words", col: (v) => diverge(v, 0, 70, 100), fmt: (v) => fmt(v, 0) },
    int: { label: "Interest", desc: "Proxy for reader interest: new ideas, sentence variety, specific detail, active voice (percentile in this document)", col: (v) => v == null ? cssVar("--sunk") : seqc(v / 100), fmt: (v) => fmt(v, 0) },
    coh: { label: "Cohesion", desc: "Share of key words carried over from the previous paragraph", col: (v) => v == null ? cssVar("--sunk") : seqc(Math.min(1, v / 0.3)), fmt: (v) => v == null ? "–" : fmt(100 * v, 0) + "%" },
    grade: { label: "Reading grade", desc: "Flesch-Kincaid grade per paragraph; darker = harder", col: (v) => v == null ? cssVar("--sunk") : seqc(Math.min(1, v / 20), "--heat-bad"), fmt: (v) => fmt(v, 1) },
    asl: { label: "Sentence length", desc: "Average words per sentence; darker = longer", col: (v) => v == null ? cssVar("--sunk") : seqc(Math.min(1, v / 40), "--heat-bad"), fmt: (v) => fmt(v, 1) },
  };
  function tabMap() {
    const h = S.report.heat, m = HEAT[S.heatMetric], cats = S.cat.categories;
    const maxCell = Math.max(1, ...h.sections.flatMap((x) => Object.values(x.cats)));
    const sel = S.sel != null ? h.paras.find((p) => p.b === S.sel) : null;
    const legend = S.heatMetric === "q"
      ? `<span>Needs work</span><span class="lg" style="background:linear-gradient(90deg,${diverge(0, 0, 70, 100)},${diverge(70, 0, 70, 100)},${diverge(100, 0, 70, 100)})"></span><span>Strong</span>`
      : `<span>Low</span><span class="lg" style="background:linear-gradient(90deg,${m.col(0)},${m.col({ int: 100, coh: 0.3, grade: 20, asl: 40 }[S.heatMetric])})"></span><span>High</span>`;
    return `
      <section class="card" aria-label="Paragraph heat map">
        <div class="map-head"><div><div class="eyebrow">Paragraph heat map</div><p class="blurb">${h.paras.length.toLocaleString()} ${h.binned ? "groups of paragraphs" : "paragraphs"} in reading order. ${esc(m.desc)}. Click a cell to inspect it.</p></div>
          <div class="seg" role="group" aria-label="Heat metric">${Object.entries(HEAT).map(([k, v]) => `<button type="button" data-heat="${k}" aria-pressed="${S.heatMetric === k}">${v.label}</button>`).join("")}</div></div>
        <div class="heat" id="heat">${h.paras.map((p) => `<button type="button" class="cell${S.sel === p.b ? " on" : ""}" data-cell="${p.b}" style="background:${m.col(p[S.heatMetric])};flex-grow:${Math.max(1, Math.round(Math.sqrt(p.w)))}" title="¶ ${p.para}${p.sec ? " · " + esc(p.sec) : ""}: ${m.label} ${m.fmt(p[S.heatMetric])}, ${p.n} finding(s)" aria-label="Paragraph ${p.para}, ${m.label} ${m.fmt(p[S.heatMetric])}"></button>`).join("")}</div>
        <div class="heat-legend">${legend}<span class="muted">Wider cells are longer paragraphs.</span></div>
        ${sel ? paraDetail(sel) : `<p class="empty-note">Select a paragraph to see its metrics, its findings and how it should read.</p>`}
      </section>
      <section class="card" aria-label="Interest and thought flow">
        <div class="eyebrow">Interest and thought flow</div>
        <p class="blurb">Top: the interest curve; blue dots are peaks, orange dots lulls. Bottom: cohesion with the previous paragraph; orange dots are topic jumps. Hover to read values, click to inspect.</p>
        <div class="chartwrap"><div class="chart" data-key="int"></div><div class="chart" data-key="coh"></div><div class="tip" id="chart-tip" hidden></div></div>
        <div class="twocol">
          <div><div class="eyebrow">Peaks of interest</div>${paraList(h.peaks, "int")}</div>
          <div><div class="eyebrow">Lulls</div>${paraList(h.lulls, "int")}</div>
          <div><div class="eyebrow">Strongest paragraphs</div>${paraList(h.best, "q")}</div>
          <div><div class="eyebrow">Paragraphs that need the most work</div>${paraList(h.worst, "q")}</div>
        </div></section>
      <section class="card" aria-label="Writing sequence">
        <div class="eyebrow">Writing sequence</div>
        <p class="blurb">Sections in reading order. The colour is section quality; the link between two sections shows how much vocabulary they share. A thin orange link marks a jump in the line of thought.</p>
        ${h.sections.length ? `<div class="seqflow">${h.sections.map((x, i) => `${i ? seqLink(h.seq[i - 1]) : ""}
          <button type="button" class="seqbox" data-fsec="${esc(x.title)}" style="flex-basis:${Math.round(Math.max(110, Math.min(240, 70 + Math.sqrt(x.w) * 4)))}px">
            <span class="sq" style="background:${diverge(x.q, 0, 70, 100)}"></span><b>${esc(x.title)}</b><small>${fmt(x.w)} words · quality ${fmt(x.q, 0)}${x.int != null ? ` · interest ${fmt(x.int, 0)}` : ""}</small></button>`).join("")}</div>` : `<p class="empty-note">No sections detected.</p>`}
      </section>
      <section class="card" aria-label="Report map">
        <div class="eyebrow">Report map: findings per 1,000 words by section and category</div>
        ${h.sections.length ? `<div class="tbl-wrap"><table class="matrix"><thead><tr><th>Section</th><th class="num">Words</th><th class="num">Quality</th><th class="num">Interest</th>${cats.map((c) => `<th class="num" title="${esc(c.name)}">${c.id}</th>`).join("")}</tr></thead><tbody>
          ${h.sections.map((x) => `<tr data-fsec="${esc(x.title)}"><td style="padding-left:${8 + (x.level - 1) * 14}px">${esc(x.title)}</td><td class="num">${fmt(x.w)}</td>
            <td class="num"><span class="qdot" style="background:${diverge(x.q, 0, 70, 100)}"></span>${fmt(x.q, 0)}</td><td class="num">${x.int == null ? "–" : fmt(x.int, 0)}</td>
            ${cats.map((c) => { const v = x.cats[c.id] || 0; return `<td class="num hc" style="background:${v ? seqc(Math.sqrt(v / maxCell), "--heat-bad") : "transparent"}" title="${esc(c.name)}: ${fmt(v, 1)} per 1,000 words">${v ? fmt(v, 0) : ""}</td>`; }).join("")}</tr>`).join("")}
        </tbody></table></div>
        <div class="legend">${cats.map((c) => `${c.id} = ${esc(c.name)}`).join(" · ")}. Darker cells mean more findings per 1,000 words. Click a row to see that section's findings.</div>` : `<p class="empty-note">No sections detected.</p>`}
      </section>`;
  }
  function seqLink(v) {
    const weak = v != null && v < 0.04;
    return `<span class="link-w" title="${v == null ? "–" : fmt(100 * v, 0) + "% shared vocabulary"}"><span style="height:${v == null ? 1 : Math.max(1, Math.min(8, Math.round(v * 40)))}px;background:${weak ? cssVar("--heat-bad") : cssVar("--ink-3")}"></span><small>${v == null ? "" : fmt(100 * v, 0) + "%"}</small></span>`;
  }
  function paraList(ps, key) {
    if (!ps.length) return `<p class="empty-note">Not enough paragraphs.</p>`;
    return `<ul class="plist">${ps.map((p) => `<li><button type="button" data-cell="${p.b}"><span class="mono">¶ ${p.para}${p.para_end ? "–" + p.para_end : ""}</span> <span class="chip">${key === "int" ? "interest" : "quality"} ${fmt(p[key], 0)}</span></button><span class="pex">${esc(p.x)}…</span></li>`).join("")}</ul>`;
  }
  function paraDetail(p) {
    const ci = changeIndex(), meta = ruleMeta();
    const items = allFindings().filter(({ f }) => f.b === p.b);
    const topRules = [...new Set(items.map(({ r }) => r.id))].slice(0, 3);
    return `<div class="pdetail">
      <div class="pd-head"><b>¶ ${p.para}${p.para_end ? "–" + p.para_end : ""}</b>${p.sec ? ` · § ${esc(p.sec)}` : ""}${p.page ? ` · p. ${p.page}` : ""}
        <span class="chips">${Object.entries(HEAT).map(([k, v]) => `<span class="chip">${v.label} ${v.fmt(p[k])}</span>`).join("")}<span class="chip">${p.w} words</span></span>
        <button type="button" class="btn quiet small" data-para-findings="${p.b}">Open in Findings</button></div>
      <p class="excerpt">${esc(p.x)}…</p>
      ${items.length ? `<ul class="flist">${items.slice(0, 8).map(({ r, f }) => findingHTML(f, r, ci.get(`${r.id}:${f.b}:${f.as}`))).join("")}</ul>${items.length > 8 ? `<p class="empty-note">${items.length - 8} more in Findings.</p>` : ""}` : `<p class="empty-note">No findings in this paragraph.</p>`}
      ${topRules.some((id) => meta[id] && meta[id].ex) ? `<div class="eyebrow" style="margin-top:10px">How it should read</div>${topRules.map((id) => meta[id] && meta[id].ex ? `<div class="ex"><span class="muted">${esc(meta[id].name)}</span><span class="ex-bad">✗ ${esc(meta[id].ex[0])}</span><span class="ex-good">✓ ${esc(meta[id].ex[1])}</span></div>` : "").join("")}` : ""}
    </div>`;
  }
  function drawCharts() {
    const h = S.report.heat, ps = h.paras;
    const peaks = new Set(h.peaks.map((p) => p.b)), lulls = new Set(h.lulls.map((p) => p.b));
    const tip = $("#chart-tip"), wrap = $(".chartwrap");
    $$(".chart").forEach((box) => {
      const key = box.dataset.key, W = 1000, H = key === "int" ? 180 : 120, padL = 44, padR = 14, padT = 18, padB = 24, n = ps.length;
      if (n < 2) { box.innerHTML = `<p class="empty-note">Not enough paragraphs to chart.</p>`; return; }
      const maxY = key === "int" ? 100 : 0.5;
      const X = (i) => padL + (W - padL - padR) * i / (n - 1);
      const Y = (v) => padT + (H - padT - padB) * (1 - Math.min(maxY, v) / maxY);
      let d = "", pen = false;
      ps.forEach((p, i) => { const v = p[key]; if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1); pen = true; });
      const ticks = key === "int" ? [0, 25, 50, 75, 100] : [0, 0.1, 0.2, 0.3, 0.4, 0.5];
      const ink2 = cssVar("--ink-2"), ink3 = cssVar("--ink-3"), rule = cssVar("--rule"), line = cssVar("--pencil"), bad = cssVar("--heat-bad"), surf = cssVar("--surface");
      const dots = ps.map((p, i) => {
        const v = p[key]; if (v == null) return "";
        if (key === "int" && peaks.has(p.b)) return `<circle cx="${X(i)}" cy="${Y(v)}" r="5" fill="${line}" stroke="${surf}" stroke-width="2"/><text x="${X(i)}" y="${Math.max(11, Y(v) - 9)}" text-anchor="middle" font-size="11" fill="${ink2}">¶${p.para}</text>`;
        if (key === "int" && lulls.has(p.b)) return `<circle cx="${X(i)}" cy="${Y(v)}" r="4.5" fill="${bad}" stroke="${surf}" stroke-width="2"/>`;
        if (key === "coh" && v < 0.03) return `<circle cx="${X(i)}" cy="${Y(v)}" r="4" fill="${bad}" stroke="${surf}" stroke-width="2"/>`;
        return "";
      }).join("");
      box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${key === "int" ? "Interest" : "Cohesion"} by paragraph">
        ${ticks.map((t) => `<line x1="${padL}" x2="${W - padR}" y1="${Y(t)}" y2="${Y(t)}" stroke="${rule}" stroke-width="1"/><text x="${padL - 6}" y="${Y(t) + 4}" text-anchor="end" font-size="11" fill="${ink3}">${key === "int" ? t : Math.round(t * 100) + "%"}</text>`).join("")}
        <text x="${padL}" y="${H - 6}" font-size="11" fill="${ink3}">¶ ${ps[0].para}</text>
        <text x="${W / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="${ink3}">${key === "int" ? "Interest (percentile)" : "Cohesion with previous paragraph"}</text>
        <text x="${W - padR}" y="${H - 6}" text-anchor="end" font-size="11" fill="${ink3}">¶ ${ps[n - 1].para_end || ps[n - 1].para}</text>
        <path d="${d}" fill="none" stroke="${line}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>${dots}
        <line class="xh" x1="0" x2="0" y1="${padT}" y2="${H - padB}" stroke="${ink2}" stroke-width="1" visibility="hidden"/>
        <rect class="hit" x="${padL}" y="0" width="${W - padL - padR}" height="${H}" fill="transparent"/></svg>`;
      const svg = $("svg", box), hit = $(".hit", box), xh = $(".xh", box);
      const idxAt = (ev) => { const r = svg.getBoundingClientRect(); const x = (ev.clientX - r.left) * W / r.width; return Math.max(0, Math.min(n - 1, Math.round((x - padL) / (W - padL - padR) * (n - 1)))); };
      hit.addEventListener("mousemove", (ev) => {
        const i = idxAt(ev), p = ps[i];
        xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
        tip.hidden = false;
        tip.innerHTML = `<b>¶ ${p.para}${p.para_end ? "–" + p.para_end : ""}</b>${p.sec ? ` · ${esc(p.sec)}` : ""}<br>Interest ${HEAT.int.fmt(p.int)} · Cohesion ${HEAT.coh.fmt(p.coh)}<br>Quality ${fmt(p.q, 0)} · Grade ${fmt(p.grade, 1)} · ${p.w} words`;
        const wr = wrap.getBoundingClientRect(), br = box.getBoundingClientRect();
        tip.style.left = Math.min(wr.width - 230, Math.max(0, ev.clientX - wr.left + 14)) + "px";
        tip.style.top = (br.top - wr.top + 6) + "px";
      });
      hit.addEventListener("mouseleave", () => { xh.setAttribute("visibility", "hidden"); tip.hidden = true; });
      hit.addEventListener("click", (ev) => { S.sel = ps[idxAt(ev)].b; renderTab(); const d = $(".pdetail"); if (d) d.scrollIntoView({ behavior: "smooth", block: "nearest" }); });
    });
  }

  // ------------------------------------------------------------------ metrics
  function tabMetrics() {
    const st = S.report.stats, rd = st.readability || {};
    const histMax = Math.max(1, ...st.hist), histLbl = ["1–9", "10–19", "20–29", "30–39", "40–49", "50+"];
    const stat = (v, k) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
    return `
      <section class="card"><div class="eyebrow">Document profile</div>
        <div class="stats">${stat(fmt(st.words), "words of body text")}${stat(fmt(st.sentences), "sentences")}${stat(fmt(st.paragraphs), "paragraphs")}${stat(fmt(st.headings), "headings")}
          ${stat(fmt(st.references), "references")}${stat(fmt(st.citations), `citations (${esc(st.cite_style)})`)}${stat(fmt(st.figures) + " / " + fmt(st.tables), "figures / tables")}${stat(st.pages ? fmt(st.pages) : "–", "pages")}</div></section>
      <section class="card"><div class="eyebrow">Readability and style metrics</div>
        <div class="tbl-wrap"><table class="mtable"><tbody>${Object.entries(rd).map(([k, v]) => `<tr><th>${esc(k)}</th><td class="num">${esc(typeof v === "number" ? fmt(v, 2) : v)}</td></tr>`).join("")}</tbody></table></div>
        <p class="legend">Grade indices estimate the US school grade needed to read the text. Flesch reading ease: 60–70 is plain English, under 30 is very hard. LIX over 50 is hard. MATTR measures vocabulary variety (higher = more varied).</p></section>
      <section class="card"><div class="twocol">
        <div><div class="eyebrow">Sentence length (words)</div>
          <div class="hist">${st.hist.map((n, i) => `<div style="height:${(100 * n) / histMax}%" title="${histLbl[i]} words: ${n} sentence(s)"></div>`).join("")}</div>
          <div class="hist-axis">${histLbl.map((l) => `<span>${l}</span>`).join("")}</div>
          <div class="eyebrow" style="margin-top:16px">Most used terms</div>
          <div class="terms">${st.top_terms.map(([w, n]) => `<span class="chip">${esc(w)} ${n}</span>`).join("")}</div></div>
        <div><div class="eyebrow">Outline as detected</div>
          ${S.doc.outline.length ? `<ul class="outline">${S.doc.outline.map((h) => `<li style="padding-left:${(h.l - 1) * 14}px"><span>${esc(h.t)}</span><span class="w">${fmt(h.w)} w</span></li>`).join("")}</ul>` : `<p class="empty-note">No headings detected.</p>`}</div>
      </div></section>
      <section class="card"><div class="eyebrow">Every rule's measurement</div>
        <div class="tbl-wrap"><table class="mtable wide"><thead><tr><th>Rule</th><th>Cat.</th><th class="num">Value</th><th>Unit</th><th class="num">Score</th><th class="num">Findings</th><th class="num">Rewrites</th><th class="num">Costs</th></tr></thead><tbody>
          ${S.report.rules.map((x) => `<tr class="${x.status !== "scored" ? "dim" : ""}"><th><button type="button" class="link" data-frule="${x.id}">${x.id} ${esc(x.name)}</button></th><td>${x.cat}</td>
            <td class="num">${x.status === "scored" ? fmt(x.value, 2) : x.status === "off" ? "off" : "n/a"}</td><td>${esc(x.unit)}</td>
            <td class="num">${x.status === "scored" ? fmt(x.score, 0) : ""}</td><td class="num">${x.n ?? ""}</td><td class="num">${x.rewrites ? "+" + x.rewrites : ""}</td><td class="num">${x.lost ? fmt(x.lost, 2) : ""}</td></tr>`).join("")}
        </tbody></table></div></section>`;
  }

  // ------------------------------------------------------------------ report events
  function setupReport() {
    const box = $("#report");
    box.addEventListener("click", (ev) => {
      const t = ev.target;
      const tab = t.closest("[data-tab]"); if (tab) { S.tab = tab.dataset.tab; renderTab(); return; }
      const fc = t.closest("[data-fcat]"); if (fc) { S.f = freshFilters(S.f.sort); S.f.cat = fc.dataset.fcat; goTab("findings"); return; }
      const fr = t.closest("[data-frule]"); if (fr) { S.f = freshFilters("rule"); S.f.rule = fr.dataset.frule; goTab("findings"); return; }
      const fs = t.closest("[data-fsec]"); if (fs) { S.f = freshFilters(S.f.sort); S.f.sec = fs.dataset.fsec; goTab("findings"); return; }
      const sv = t.closest("[data-sev]"); if (sv) { const k = sv.dataset.sev; S.f.sev.has(k) ? S.f.sev.delete(k) : S.f.sev.add(k); renderTab(); return; }
      const mo = t.closest("[data-more]"); if (mo) { S.showAll.add(mo.dataset.more); renderTab(); return; }
      if (t.closest("[data-clear-para]")) { S.f.para = null; renderTab(); return; }
      if (t.closest("[data-clear-all]")) { S.f = freshFilters(S.f.sort); renderTab(); return; }
      const hm = t.closest("[data-heat]"); if (hm) { S.heatMetric = hm.dataset.heat; renderTab(); return; }
      const cell = t.closest("[data-cell]"); if (cell) { S.sel = Number(cell.dataset.cell); S.tab = "map"; renderTab(); const d = $(".pdetail"); if (d) d.scrollIntoView({ behavior: "smooth", block: "nearest" }); return; }
      const pf = t.closest("[data-para-findings]"); if (pf) { S.f = freshFilters("position"); S.f.para = Number(pf.dataset.paraFindings); goTab("findings"); return; }
      const ra = t.closest("[data-rev-all]");
      if (ra) { S.report.revisions.changes.filter((c) => !S.revRule || c.rule === S.revRule).forEach((c) => ra.dataset.revAll === "reject" ? S.rejected.add(c.id) : S.rejected.delete(c.id)); renderTab(); return; }
      if (t.id === "rev-dl") downloadRevised();
    });
    box.addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.dataset.chg) { t.checked ? S.rejected.delete(t.dataset.chg) : S.rejected.add(t.dataset.chg); const y = window.scrollY; renderTab(); window.scrollTo({ top: y }); return; }
      if (t.id === "f-cat") { S.f.cat = t.value; S.f.rule = ""; renderTab(); }
      if (t.id === "f-rule") { S.f.rule = t.value; renderTab(); }
      if (t.id === "f-sec") { S.f.sec = t.value; renderTab(); }
      if (t.id === "f-sort") { S.f.sort = t.value; renderTab(); }
      if (t.id === "f-rep") { S.f.rep = t.checked; renderTab(); }
      if (t.id === "rev-rule") { S.revRule = t.value; renderTab(); }
    });
    box.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && ev.target.matches("[data-fcat]")) ev.target.click(); });
    box.addEventListener("input", (ev) => {
      if (ev.target.id === "fq") {
        S.f.q = ev.target.value; clearTimeout(S.qt);
        S.qt = setTimeout(() => { const pos = ev.target.selectionStart; renderTab(); const q = $("#fq"); if (q) { q.focus(); q.setSelectionRange(pos, pos); } }, 250);
      }
    });
    box.addEventListener("toggle", (ev) => { const k = ev.target.dataset && ev.target.dataset.open; if (k) ev.target.open ? S.open.add(k) : S.open.delete(k); }, true);
  }

  // ------------------------------------------------------------------ history
  function historyTable(rows) {
    return `<div class="htable-wrap"><table class="htable"><thead><tr><th>When</th><th>Document</th><th>As</th><th class="num">Score</th><th>Verdict</th><th></th></tr></thead><tbody>
      ${rows.map((h) => `<tr><td>${new Date(h.at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</td>
        <td>${esc(h.name)}<div class="muted mono" style="font-size:11px">${fmt(h.words)} words · ${esc(h.fp)}</div></td>
        <td>${esc((S.cat.profiles[h.profile] || {}).name || h.profile)} · ×${Number(h.strictness).toFixed(2)}</td>
        <td class="num">${fmt(h.score)}</td><td><span class="vchip v-${h.verdict}">${VLABEL[h.verdict]}</span></td>
        <td><button type="button" class="btn quiet small" data-open-fp="${h.fp}">Open</button> <button type="button" class="btn quiet small" data-del-fp="${h.fp}">Delete</button></td></tr>`).join("")}
    </tbody></table></div>`;
  }
  async function renderHistory() {
    S.history = await api("/api/history").catch(() => []);
    $("#history-list").innerHTML = S.history.length ? historyTable(S.history) : `<p class="empty-note">No reviews yet. Upload a file to start.</p>`;
  }
  async function openFromHistory(fp) {
    try { const rep = await api("/api/report/" + fp); const doc = await api("/api/doc/" + rep.doc.doc_id); openDoc(doc, rep.settings); }
    catch (e) { toast(e.message); }
  }
  function setupHistory() {
    document.addEventListener("click", async (ev) => {
      const o = ev.target.closest("[data-open-fp]"); if (o) { openFromHistory(o.dataset.openFp); return; }
      const d = ev.target.closest("[data-del-fp]");
      if (d) {
        if (d.dataset.armed !== "1") { d.dataset.armed = "1"; d.textContent = "Confirm"; return; }
        await api("/api/history/" + d.dataset.delFp, { method: "DELETE" });
        S.history = await api("/api/history");
        if (!$("#view-history").hidden) renderHistory(); else renderRecent();
        toast("Review deleted");
      }
    });
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    setupDrop(); setupRail(); setupReport(); setupHistory();
    $("#nav-new").addEventListener("click", () => { history.replaceState(null, "", location.pathname); show("start"); });
    $("#nav-history").addEventListener("click", () => show("history"));
    $("#brand").addEventListener("click", (ev) => { ev.preventDefault(); history.replaceState(null, "", location.pathname); show("start"); });
    if (window.matchMedia("(max-width: 900px)").matches) $("#rail-wrap").open = false;
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (S.report) renderTab(); });
    try {
      [S.cat, S.history, S.presets] = await Promise.all([api("/api/catalog"), api("/api/history"), api("/api/presets")]);
      $("#rule-count").textContent = S.cat.rules.length;
      $("#host").textContent = `manuscript review · in your browser · ${S.cat.rules.length} rules`;
      renderRecent();
      const m = location.hash.match(/^#review=([0-9a-f]{16})(?:&tab=(\w+))?$/);
      if (m) { if (m[2] && TABS.some(([id]) => id === m[2])) S.pendingTab = m[2]; openFromHistory(m[1]); }
    } catch (e) { status("Could not start Blue Pencil: " + e.message, true); }
  }
  // backend.js calls this after a backup is loaded or merged
  window.BP_refresh = async () => {
    [S.history, S.presets] = await Promise.all([api("/api/history"), api("/api/presets")]);
    if (!$("#view-history").hidden) renderHistory(); else renderRecent();
    if (S.doc && !$("#view-review").hidden) renderSettings();
  };
  boot();
})();
