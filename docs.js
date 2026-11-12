// Blue Pencil docs: renders every rule from the catalog (catalog.json on GitHub Pages, /api/catalog on the Pi).
"use strict";
(async () => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const onPi = location.pathname.includes("/static/");
  document.querySelectorAll("[data-home]").forEach((a) => (a.href = onPi ? "/" : "./"));

  let cat;
  try { const r = await fetch(onPi ? "/api/catalog" : "catalog.json"); if (!r.ok) throw 0; cat = await r.json(); }
  catch (e) { $("#rulelist").innerHTML = `<p class="muted">Couldn't load the rules.</p>`; return; }

  const paper = cat.profiles.paper.settings, profs = Object.entries(cat.profiles);
  const MODE = { lower: "Lower is better", higher: "Higher is better", range: "Within a range" };
  const fmt = (v) => (v == null ? "–" : Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : String(+v.toFixed(2)));
  function target(r) {
    const s = paper.rules[r.id] || {}, u = r.unit;
    if (r.mode === "range") return `${fmt(s.lo)}–${fmt(s.hi)} ${u}; 0 at ${r.rel ? fmt(100 * s.tol) + "%" : fmt(s.tol)} beyond`;
    if (r.mode === "lower") return `full marks ≤ ${fmt(s.good)}, 0 at ≥ ${fmt(s.bad)} ${u}`;
    return `full marks ≥ ${fmt(s.good)}, 0 at ≤ ${fmt(s.bad)} ${u}`;
  }
  const where = (r) => {
    const off = profs.filter(([, p]) => !(p.settings.rules[r.id] || {}).on).map(([, p]) => p.name);
    return off.length ? `Off by default for: ${off.join(", ")}.` : "On for every document type.";
  };
  const ex = (r) => (r.ex && r.ex.length >= 2 ? `<div class="dex"><div class="bad"><span>✗</span>${esc(r.ex[0])}</div><div class="good"><span>✓</span>${esc(r.ex[1])}</div></div>` : "");
  const card = (r) => {
    const s = paper.rules[r.id] || {};
    return `<article class="drule" id="${r.id}" data-cat="${r.cat}" data-text="${esc((r.id + " " + r.name + " " + r.desc + " " + r.why + " " + r.fix).toLowerCase())}">
      <h4><a href="#${r.id}" class="rid">${r.id}</a> ${esc(r.name)}</h4>
      <p><b>Checks:</b> ${esc(r.desc)}</p>
      <p><b>Why it matters:</b> ${esc(r.why)}</p>
      <p><b>How to fix:</b> ${esc(r.fix)}</p>
      ${ex(r)}
      <p class="dmeta">${MODE[r.mode] || r.mode} · measured in ${esc(r.unit)} · default target ${esc(target(r))} · severity ${esc(s.sev || "")} · weight ×${fmt(s.w)} · ${esc(where(r))}</p>
    </article>`;
  };

  $("#rulecount").textContent = `${cat.rules.length} rules in ${cat.categories.length} areas.`;
  $("#cat").innerHTML += cat.categories.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("#rulelist").innerHTML = cat.categories.map((c) => `<section class="dcat" data-cat="${c.id}"><h3 id="area-${c.id}">${esc(c.name)} <small>×${fmt(paper.categories[c.id])}</small></h3><p class="dq">${esc(c.q)}</p>
    ${cat.rules.filter((r) => r.cat === c.id).map(card).join("")}</section>`).join("");
  $("#toc").innerHTML = `<b>Contents</b><a href="#pipeline">From file to verdict</a><a href="#scoring">How scores are calculated</a><a href="#yours">Your own review</a><a href="#metrics">Metrics</a><a href="#rules">Every rule</a>
    ${cat.categories.map((c) => `<a class="sub" href="#area-${c.id}">${esc(c.name)}</a>`).join("")}`;

  function filter() {
    const q = $("#q").value.trim().toLowerCase(), c = $("#cat").value;
    document.querySelectorAll(".drule").forEach((el) => (el.hidden = (c && el.dataset.cat !== c) || (q && !el.dataset.text.includes(q))));
    document.querySelectorAll(".dcat").forEach((sec) => (sec.hidden = ![...sec.querySelectorAll(".drule")].some((el) => !el.hidden)));
  }
  $("#q").addEventListener("input", filter); $("#cat").addEventListener("change", filter);
  if (location.hash) { const t = document.getElementById(location.hash.slice(1)); if (t) { t.classList.add("hit"); t.scrollIntoView(); } }
  window.addEventListener("hashchange", () => { document.querySelectorAll(".drule.hit").forEach((e) => e.classList.remove("hit")); const t = document.getElementById(location.hash.slice(1)); if (t) t.classList.add("hit"); });
})();
