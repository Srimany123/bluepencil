// Blue Pencil · professional PDF report, built in the browser with pdfmake from /api/report/<id>.model.
"use strict";
(() => {
  const PDFMAKE = "https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.2.10/";
  const C = { ink: "#18202e", ink2: "#4a5468", ink3: "#6f798c", rule: "#dde2ea", pencil: "#2f62c9", soft: "#eef3fc", hl: "#fff1b8",
    accept: "#1d7f55", minor: "#9a6a12", major: "#c4561f", reject: "#bf3434", critical: "#bf3434", note: "#6f798c", del: "#bf3434", add: "#1d7f55" };
  const SEVC = { critical: C.critical, major: C.major, minor: C.minor, note: C.note };
  let loading = null;
  function load() {
    if (window.pdfMake && window.pdfMake.vfs) return Promise.resolve();
    const s = (src) => new Promise((res, rej) => { const e = document.createElement("script"); e.src = src; e.onload = res; e.onerror = () => rej(new Error("Couldn't load the PDF maker. Check your connection.")); document.head.append(e); });
    return loading || (loading = s(PDFMAKE + "pdfmake.min.js").then(() => s(PDFMAKE + "vfs_fonts.min.js")));
  }

  const hr = (w = 515) => ({ canvas: [{ type: "line", x1: 0, y1: 0, x2: w, y2: 0, lineWidth: 0.6, lineColor: C.rule }], margin: [0, 6, 0, 8] });
  const quote = (q) => !q ? null : { text: [{ text: "“" + q[0] }, q[1] ? { text: q[1], background: C.hl, bold: true } : "", { text: q[2] + "”" }], italics: true, color: C.ink2, fontSize: 9, margin: [0, 2, 0, 0] };
  const pill = (sev) => ({ text: " " + sev.toUpperCase() + " ", color: "#fff", background: SEVC[sev] || C.ink3, fontSize: 7, bold: true, characterSpacing: 0.6 });

  function itemBlock(x, i) {
    const rows = x.locations.map((l) => [
      { text: l.where, bold: true, fontSize: 8.5, color: C.ink2 },
      { stack: [{ text: l.issue, fontSize: 9.5 }, quote(l.quote), l.action ? { text: [{ text: "Do: ", color: C.pencil, bold: true }, l.action], fontSize: 9.5, color: C.ink, margin: [0, 2, 0, 0] } : null].filter(Boolean) },
    ]);
    if (x.more) rows.push([{ text: "", fontSize: 8 }, { text: `…and ${x.more} more place${x.more === 1 ? "" : "s"} in the document.`, italics: true, fontSize: 8.5, color: C.ink3 }]);
    return {
      unbreakable: x.locations.length <= 3,
      margin: [0, 0, 0, 12],
      stack: [
        { columns: [{ width: "*", text: [{ text: `${i}.  `, color: C.ink3 }, { text: x.title, bold: true }], fontSize: 11.5 }, { width: "auto", text: [pill(x.severity)], margin: [0, 2, 0, 0] }] },
        { text: [{ text: "Problem  ", bold: true, color: C.ink2 }, x.problem], fontSize: 9.5, margin: [0, 4, 0, 0] },
        { text: [{ text: "Fix  ", bold: true, color: C.pencil }, x.fix], fontSize: 9.5, margin: [0, 2, 0, 4] },
        rows.length ? { table: { widths: [110, "*"], body: rows }, layout: { hLineWidth: (k) => (k === 0 ? 0 : 0.5), vLineWidth: () => 0, hLineColor: () => C.rule, paddingTop: () => 4, paddingBottom: () => 4, paddingLeft: () => 0, paddingRight: () => 6 } } : null,
      ].filter(Boolean),
    };
  }
  const heading = (t, sub) => ({ text: [t, sub ? { text: "  " + sub, color: C.ink3, fontSize: 10, bold: false } : ""], style: "h2", margin: [0, 14, 0, 6] });

  function definition(m) {
    const v = m.verdict, vc = C[v.key] || C.ink;
    const content = [
      { text: "MANUSCRIPT REVIEW", color: C.pencil, bold: true, fontSize: 8.5, characterSpacing: 1.6 },
      { text: m.title, fontSize: 20, bold: true, margin: [0, 4, 0, 2] },
      { text: [m.facts.join(" · "), m.facts.length ? "  ·  " : "", m.date], color: C.ink3, fontSize: 9 },
      hr(),
      { table: { widths: [150, "*"], body: [[
          { stack: [
              { text: v.by_reviewer ? "REVIEWER'S DECISION" : "RECOMMENDATION", fontSize: 7.5, bold: true, color: "#dfe7f7", characterSpacing: 1 },
              { text: v.label, fontSize: 17, bold: true, color: "#fff", margin: [0, 3, 0, 6] },
              { text: [{ text: String(m.score), fontSize: 26, bold: true }, { text: " / 100", fontSize: 10 }], color: "#fff" },
              v.engine_label ? { text: "Automated recommendation: " + v.engine_label, fontSize: 7.5, color: "#dfe7f7", margin: [0, 6, 0, 0] } : null,
            ].filter(Boolean), fillColor: vc, margin: [10, 10, 10, 10] },
          { stack: [
              { text: m.headline, fontSize: 12, bold: true, margin: [0, 0, 0, 6] },
              v.note ? { text: v.note, italics: true, fontSize: 9.5, color: C.ink2, margin: [0, 0, 0, 6] } : null,
              m.summary.length ? { ul: m.summary, fontSize: 9.5, color: C.ink, markerColor: C.pencil } : null,
            ].filter(Boolean), fillColor: C.soft, margin: [12, 10, 10, 10] },
        ]] }, layout: "noBorders" },
    ];
    // a section heading always shares a block with what follows it, so it is never stranded at a page bottom
    const withHeading = (h, first) => ({ stack: [h, first], unbreakable: true });
    if (m.required.length) m.required.forEach((x, i) => content.push(i ? itemBlock(x, i + 1) : withHeading(heading("Required changes", `${m.required.length}`), itemBlock(x, 1))));
    if (m.improve.length) {
      m.improve.forEach((x, i) => content.push(i ? itemBlock(x, i + 1) : withHeading(heading("Recommended improvements", `${m.improve.length}`), itemBlock(x, 1))));
      if (m.more_improve) content.push({ text: `${m.more_improve} smaller improvements are not listed.`, italics: true, fontSize: 8.5, color: C.ink3 });
    }
    if (m.edits.length) {
      const h = heading("Ready-to-apply edits", `${m.edits.length + m.more_edits}`);
      const body = [[{ ...h, colSpan: 4, margin: [0, 14, 0, 2] }, {}, {}, {}], ["Where", "Original", "Revised", "Why"].map((t) => ({ text: t, bold: true, fontSize: 8, color: C.ink3 }))];
      m.edits.forEach((e) => body.push([
        { text: e.where, fontSize: 8, color: C.ink2 },
        { text: [e.pre, { text: e.old.trim() ? e.old : "␣", decoration: "lineThrough", color: C.del }, e.post], fontSize: 8.5 },
        { text: [e.pre, e.new ? { text: e.new, bold: true, color: C.add } : "", e.post], fontSize: 8.5 },
        { text: e.why, fontSize: 8, color: C.ink3 },
      ]));
      content.push({ table: { headerRows: 2, keepWithHeaderRows: 1, widths: [80, "*", "*", 72], body, dontBreakRows: true }, layout: { hLineWidth: (k) => (k < 2 ? 0 : k === 2 ? 0.8 : 0.4), vLineWidth: () => 0, hLineColor: () => C.rule, paddingTop: () => 3, paddingBottom: () => 3, paddingLeft: (c) => (c === 0 ? 0 : 4) } });
      if (m.more_edits) content.push({ text: `…and ${m.more_edits} more in the revised document.`, italics: true, fontSize: 8.5, color: C.ink3, margin: [0, 4, 0, 0] });
    }
    if (m.scores.length) {
      content.push({ unbreakable: true, stack: [heading("Scores by area"), { table: { widths: [170, 30, "*"], body: m.scores.map((s) => [
        { text: s.area, fontSize: 9.5 }, { text: String(s.score), fontSize: 9.5, bold: true, alignment: "right" },
        { canvas: [{ type: "rect", x: 0, y: 3, w: 250, h: 6, r: 3, color: "#eceff4" }, { type: "rect", x: 0, y: 3, w: Math.max(2, 2.5 * s.score), h: 6, r: 3, color: s.score >= 85 ? C.accept : s.score >= 70 ? C.minor : s.score >= 50 ? C.major : C.reject }] },
      ]) }, layout: { hLineWidth: () => 0, vLineWidth: () => 0, paddingTop: () => 3, paddingBottom: () => 3 } }] });
    }
    if (m.strengths.length) content.push({ unbreakable: true, stack: [heading("Checks passed cleanly"), { text: m.strengths.join(" · "), fontSize: 9.5, color: C.ink2 }] });
    return {
      pageSize: "A4", pageMargins: [40, 48, 40, 54], content,
      info: { title: "Review: " + m.title, subject: "Manuscript review", creator: "Blue Pencil" },
      defaultStyle: { font: "Roboto", fontSize: 10, color: C.ink, lineHeight: 1.2 },
      styles: { h2: { fontSize: 13.5, bold: true, color: C.ink } },
      footer: (page, pages) => ({ margin: [40, 18, 40, 0], columns: [
        { text: `Review ID ${m.review_id}`, fontSize: 7.5, color: C.ink3 },
        { text: `${page} / ${pages}`, alignment: "right", fontSize: 7.5, color: C.ink3 }] }),
    };
  }

  window.BP_pdf = async function (fp, name) {
    const [mr] = await Promise.all([fetch(`/api/report/${fp}.model`), load()]);
    if (!mr.ok) throw new Error("Couldn't build the report.");
    const m = await mr.json();
    await new Promise((res) => window.pdfMake.createPdf(definition(m)).download(name || `${m.title}-review.pdf`, res));
  };
  window.BP_pdfDefinition = definition;   // for tests
})();
