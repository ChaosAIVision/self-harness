// ── Self-Harness Dashboard SPA ──────────────────────────────────────
const app = document.getElementById("app");
const api = async (path, opts) => {
  const r = await fetch("/api" + path, opts);
  if (!r.ok) { let d = {}; try { d = await r.json(); } catch (e) {} throw new Error(d.error || r.status); }
  return r.json();
};
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
const pct = (x) => (x == null ? "–" : (x * 100).toFixed(0) + "%");
const signed = (x) => (x == null ? "–" : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%");
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])));
const rateClass = (r) => (r >= 0.7 ? "g" : r >= 0.5 ? "y" : "r");
const go = (h) => { location.hash = h; };
const STEP_TYPES = ["làm_dịu", "làm_rõ", "làm_hài_lòng"];
const STEP_LABEL = { 1: "dịu", 2: "rõ", 3: "hl", 4: "dịu", 5: "rõ", 6: "hl", 7: "dịu", 8: "rõ", 9: "hl" };
const TYPE_COLOR = { "làm_dịu": "#d95926", "làm_rõ": "#9085e9", "làm_hài_lòng": "#199e70", overall: "#3987e5" };

// ── Router ──
const routes = [];
function route(re, handler) { routes.push({ re, handler }); }
async function render() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const seg = hash === "/" ? "" : hash.split("/")[1];
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("active", a.dataset.seg === seg));
  for (const { re, handler } of routes) {
    const m = hash.match(re);
    if (m) { app.innerHTML = `<div class="loading">Loading…</div>`; try { await handler(...m.slice(1)); } catch (e) { app.innerHTML = `<div class="panel"><span class="pill bad">Error</span> ${esc(e.message)}</div>`; } return; }
  }
  app.innerHTML = `<div class="panel">Not found</div>`;
}
window.addEventListener("hashchange", render);

// ── SVG line chart ──
function lineChart(versions, series) {
  const W = 640, H = 240, pad = { l: 40, r: 120, t: 16, b: 30 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const n = versions.length;
  const x = (i) => pad.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v) => pad.t + ih - v * ih;
  let g = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">`;
  for (let t = 0; t <= 100; t += 25) { const yy = y(t / 100); g += `<line x1="${pad.l}" y1="${yy}" x2="${pad.l + iw}" y2="${yy}" stroke="#262b33"/><text x="${pad.l - 6}" y="${yy + 3}" text-anchor="end">${t}%</text>`; }
  versions.forEach((v, i) => { g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(v)}</text>`; });
  let ly = pad.t + 4;
  for (const s of series) {
    const pts = s.data.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    g += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
    s.data.forEach((v, i) => { g += `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="${s.color}"/>`; });
    g += `<text x="${pad.l + iw + 10}" y="${ly}" fill="${s.color}">${esc(s.name)}</text>`; ly += 16;
  }
  return g + "</svg>";
}

// ── Version gating (nothing shows until a version is picked) ──
function versionBar(view, current, versions) {
  const opts = (versions || []).map(v => `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(v)}</option>`).join("");
  return `<span class="vselect">version <select onchange="go(this.value ? '#/${view}/' + this.value : '#/${view}')"><option value="">— chọn version —</option>${opts}</select></span>`;
}
function pickVersionEmpty(what) {
  return `<div class="panel muted" style="text-align:center;padding:44px">Chọn một version ở trên để xem ${esc(what)}.</div>`;
}

// ── Pipeline (pick a version → its round detail) ──
route(/^\/pipeline(?:\/(.+))?$/, async (version) => {
  const vs = await api("/versions").catch(() => ({ versions: [], current: null }));
  // Default to the current harness version when no version is in the URL
  const selected = version || vs.current || "";
  const header = `<div class="row"><div class="grow"><h1>Pipeline</h1></div>
      ${versionBar("pipeline", selected, vs.versions || [])}
      <button onclick="go('#/runner')">Run Pipeline</button></div>`;
  if (!version) {
    // Auto-redirect to current version if available, else show picker
    if (vs.current && (vs.versions || []).includes(vs.current)) {
      go(`#/pipeline/${vs.current}`);
      return;
    }
    app.innerHTML = header + pickVersionEmpty("chi tiết pipeline của version đó"); return;
  }
  app.innerHTML = header + `<h2 style="margin:16px 0 4px">Round — ${esc(version)}</h2>` + await roundDetailBody(version);
});

// ── Round Detail ──
route(/^\/round\/(.+)$/, async (version) => {
  app.innerHTML = `<span class="back" onclick="go('#/pipeline')">← Pipeline</span>
    <h1>Round Detail — ${esc(version)}</h1>` + await roundDetailBody(version);
});
async function roundDetailBody(version) {
  const [d, pd] = [await api("/rounds/" + version), await api("/prompts/" + version).catch(() => null)];
  const ts = d.traces_summary;
  let steps = `<table><tr><th>Step</th><th>Type</th><th>Matched</th><th class="num">Rate</th></tr>`;
  for (let sid = 1; sid <= 9; sid++) {
    const s = ts.by_step[String(sid)]; if (!s) continue;
    steps += `<tr><td>${sid}</td><td>${esc(s.step_type)}</td><td>${s.matched}/${s.total}</td><td class="num"><span class="cell ${rateClass(s.rate)}">${pct(s.rate)}</span></td></tr>`;
  }
  steps += "</table>";
  const clusters = (d.failures.clusters || []).map(c => `<li><b>[${esc(c.step_type)}]</b> ${esc(c.pattern)} <span class="muted">(n=${c.count})</span></li>`).join("");
  const props = (d.proposals || []).map(p => `<li><b>${esc(p.proposal_id)}</b>: ${esc(p.target_failure)}<br><span class="muted">${esc((p.proposed_change || "").slice(0, 160))}…</span><br><span class="muted">insertion: ${esc(p.insertion_point)}</span></li>`).join("");
  let val = "";
  if (d.validation && d.validation.length) {
    val = `<table><tr><th>Candidate</th><th class="num">Δ in</th><th class="num">Δ out</th><th>Result</th></tr>` +
      d.validation.map(v => `<tr><td>${esc(v.proposal_id)}</td><td class="num">${signed(v.delta_in)}</td><td class="num">${signed(v.delta_out)}</td><td><span class="pill ${v.accepted ? "ok" : "bad"}">${v.accepted ? "ACCEPT" : "REJECT"}</span></td></tr>`).join("") + "</table>";
  }
  const rfl = d.reflection;
  return `
    <div class="cards" style="grid-template-columns:repeat(3,1fr)">
      <div class="card"><div class="big">${ts.total}</div><div class="lbl">calls evaluated</div></div>
      <div class="card"><div class="big">${ts.passed}</div><div class="lbl">passed</div></div>
      <div class="card"><div class="big">${ts.failed}</div><div class="lbl">failed</div></div>
    </div>
    <div class="timeline" style="margin-top:18px">
      <div class="step"><h3>Per-step agreement</h3>${steps}</div>
      <div class="step"><h3>Mined Failures (${(d.failures.clusters || []).length})</h3><ul>${clusters || '<li class="muted">none</li>'}</ul></div>
      <div class="step"><h3>Proposals (${(d.proposals || []).length})</h3><ul>${props || '<li class="muted">none</li>'}</ul></div>
      ${val ? `<div class="step"><h3>Validation</h3>${val}</div>` : ""}
      ${rfl ? `<div class="step reflection"><h3>Reflection</h3>
        <div class="line"><span class="k">Worked</span> ${esc(rfl.what_worked)}</div>
        <div class="line"><span class="k">Failed</span> ${esc(rfl.what_failed)}</div>
        <div class="line"><span class="k">Persists</span> ${esc(rfl.what_persists)}</div>
        <div class="line"><span class="k">Lesson</span> ${esc(rfl.lesson)}</div>
        <div class="line"><span class="k">Avoid next</span> ${esc(rfl.avoid_next)}</div></div>` : ""}
    </div>
    ${pd && pd.diff ? `<div class="panel"><h2>Prompt Diff (${esc(pd.previous_version)} → ${esc(version)})</h2><pre class="diff">${renderDiff(pd.diff)}</pre></div>` : ""}`;
}
function renderDiff(diff) {
  return diff.split("\n").map(l => {
    const c = l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@") ? "hdr" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
    return c ? `<span class="${c}">${esc(l)}</span>` : esc(l);
  }).join("\n");
}

// ── Memory (pick a version → its reflections) ──
route(/^\/memory(?:\/(.+))?$/, async (version) => {
  const vs = await api("/versions").catch(() => ({ versions: [] }));
  const header = `<span class="back" onclick="go('#/pipeline')">← Pipeline</span>
    <div class="row"><div class="grow"><h1>Episodic Memory</h1></div>${versionBar("memory", version || "", vs.versions || [])}</div>`;
  if (!version) { app.innerHTML = header + pickVersionEmpty("reflection của version đó"); return; }
  const all = await api("/reflections");
  const rfls = all.filter(r => r.to_version === version || r.from_version === version || (r.round_id || "").includes(version));
  const tracker = {};
  for (const r of rfls) for (const fs of (r.failure_statuses || [])) {
    const k = fs.pattern.slice(0, 40);
    tracker[k] = tracker[k] || { pattern: fs.pattern, step_type: fs.step_type, first: fs.first_seen, rounds: 0, attempts: new Set() };
    tracker[k].rounds++; (fs.attempts || []).forEach(a => tracker[k].attempts.add(a));
  }
  const cards = rfls.slice().reverse().map(r => `
    <div class="panel"><h2>${esc(r.round_id)} <span class="muted" style="font-weight:400">${pct(r.baseline_in)} → ${pct(r.final_in)} held_in</span></h2>
      <h3 style="margin:6px 0">Proposals</h3>
      <ul>${(r.proposals_tried || []).map(p => `<li><span class="pill ${p.accepted ? "ok" : "bad"}">${p.accepted ? "KEPT" : "CUT"}</span> ${esc(p.target_failure)} <span class="muted">(${signed(p.delta_out)} out)</span></li>`).join("") || '<li class="muted">none</li>'}</ul>
      <div class="reflection">
        <div class="line"><span class="k">Persists</span> ${esc(r.what_persists)}</div>
        <div class="line"><span class="k">Lesson</span> ${esc(r.lesson)}</div>
        <div class="line"><span class="k">Avoid next</span> ${esc(r.avoid_next)}</div>
      </div></div>`).join("");
  const trackerRows = Object.values(tracker).map(t => `<tr><td>${esc(t.pattern.slice(0, 46))}</td><td>${esc(t.first)}</td><td class="num">${t.rounds}</td><td>${[...t.attempts].join(", ") || "–"}</td></tr>`).join("");
  app.innerHTML = header +
    `<p class="sub">${rfls.length} reflection record(s) liên quan ${esc(version)}</p>
    ${cards || '<div class="panel muted">Không có reflection cho version này.</div>'}
    ${trackerRows ? `<div class="panel"><h2>Failure Pattern Tracker</h2><table><tr><th>Pattern</th><th>First seen</th><th class="num">Rounds</th><th>Attempts</th></tr>${trackerRows}</table></div>` : ""}`;
});

// ── Review (pick a version → its disagreements) ──
let reviewState = { items: [], idx: 0, pending: [], version: null, allVersions: [] };
route(/^\/review(?:\/(.+))?$/, async (version) => {
  if (!version) {
    const vs = await api("/versions").catch(() => ({ versions: [] }));
    app.innerHTML = `<span class="back" onclick="go('#/pipeline')">← Pipeline</span>
      <div class="row"><div class="grow"><h1>Review</h1></div>${versionBar("review", "", vs.versions || [])}</div>
      ${pickVersionEmpty("các disagreement của version đó")}`;
    return;
  }
  await loadReview(version);
});
async function loadReview(version) {
  const vs = await api("/versions").catch(() => ({ versions: [], current: null }));
  const q = await api(`/review/queue?version=${encodeURIComponent(version)}`);
  const res = await api(`/review/items?version=${encodeURIComponent(version)}&limit=1000`);
  reviewState = { items: res.items, idx: 0, pending: [], version, allVersions: vs.versions || [] };
  renderReview(q);
}
function renderReview(q) {
  const s = reviewState;
  const total = s.items.length;
  const it = s.items[s.idx];
  const done = q.reviewed + s.pending.length;
  const bar = total ? Math.round((s.idx / total) * 100) : 0;
  if (!it) {
    app.innerHTML = `<span class="back" onclick="go('#/pipeline')">← Pipeline</span>
      <div class="row"><div class="grow"><h1>Review</h1></div>${versionBar("review", s.version, s.allVersions)}</div>
      <div class="panel"><p>All ${total} disagreements viewed. ${s.pending.length} annotation(s) staged.</p>
      ${s.pending.length ? `<button onclick="submitReview()">Save ${s.pending.length} annotation(s)</button>` : '<p class="muted">Nothing to submit.</p>'}</div>`;
    return;
  }
  app.innerHTML = `
    <span class="back" onclick="go('#/pipeline')">← Pipeline</span>
    <div class="row"><div class="grow"><h1>Review</h1></div>${versionBar("review", s.version, s.allVersions)}<div class="muted">${s.idx + 1} / ${total}</div></div>
    <div class="progress" style="margin:8px 0 18px"><div style="width:${bar}%"></div></div>
    <div class="panel">
      <div class="row"><b>Call ${esc(it.task_id)}</b><span class="pill neutral">${esc(it.source)}</span><span class="pill neutral">step ${it.step_id} · ${esc(it.step_type)}</span>
        ${it.existing_feedback ? '<span class="pill ok">already reviewed</span>' : ""}</div>
      <h3>Transcript</h3><div class="transcript">${esc(it.transcript_excerpt) || '<span class="muted">(not available)</span>'}</div>
      <div class="row" style="margin-top:12px;align-items:stretch">
        <div class="evi grow"><b>Model → ${esc(it.model_said)}</b><br><span class="muted">${esc(it.model_evidence)}</span></div>
        <div class="evi grow"><b>GT → ${esc(it.ground_truth)}</b><br><span class="muted">${esc(it.gt_evidence)}</span></div>
      </div>
      ${it.model_thinking ? `<details style="margin-top:10px"><summary style="cursor:pointer;color:var(--muted);font-family:var(--mono);font-size:11px;letter-spacing:.04em">model thinking ▾</summary><div class="evi" style="margin-top:6px;white-space:pre-wrap;line-height:1.5">${esc(it.model_thinking)}</div></details>` : ""}
      <h3>Your judgment</h3>
      <div class="review-opts" id="opts">
        <label><input type="radio" name="act" value="agree" checked> Agree with GT (${esc(it.ground_truth)})</label>
        <label><input type="radio" name="act" value="override_s"> Override → S</label>
        <label><input type="radio" name="act" value="override_u"> Override → U</label>
        <label><input type="radio" name="act" value="ambiguous"> Ambiguous / borderline</label>
        <label><input type="radio" name="act" value="gt_wrong"> GT is wrong (model right)</label>
      </div>
      <h3>Reasoning note</h3><textarea id="note" placeholder="Why? (injected into miner context)"></textarea>
      <div class="row" style="margin-top:12px">
        <button class="ghost" onclick="reviewNav(-1)" ${s.idx === 0 ? "disabled" : ""}>← Previous</button>
        <button class="ghost" onclick="reviewNav(1)">Skip</button>
        <div class="grow"></div>
        <button onclick="reviewSubmitOne()">Submit & Next →</button>
      </div>
    </div>
    ${s.pending.length ? `<div class="panel"><b>Staged this session (${s.pending.length})</b> — <button onclick="submitReview()">Save all</button>
      <ul>${s.pending.map(p => `<li>${esc(p.task_id)} step ${p.step_id}: ${esc(p.action)}${p.reasoning_note ? ` — “${esc(p.reasoning_note)}”` : ""}</li>`).join("")}</ul></div>` : ""}`;
}
function reviewNav(delta) {
  reviewState.idx = Math.max(0, Math.min(reviewState.items.length, reviewState.idx + delta));
  renderReview({ reviewed: 0 });
}
function reviewSubmitOne() {
  const it = reviewState.items[reviewState.idx];
  const action = document.querySelector('input[name="act"]:checked').value;
  const note = document.getElementById("note").value.trim();
  reviewState.pending = reviewState.pending.filter(p => !(p.task_id === it.task_id && p.step_id === it.step_id));
  reviewState.pending.push({
    task_id: it.task_id, record_id: it.record_id, source: it.source, step_id: it.step_id,
    model_said: it.model_said, ground_truth: it.ground_truth, action, reasoning_note: note,
  });
  reviewNav(1);
}
async function submitReview() {
  if (!reviewState.pending.length) return;
  const r = await post("/review/submit", {
    reviewer: "dashboard", harness_version: reviewState.version, items: reviewState.pending,
  });
  app.innerHTML = `<div class="panel"><span class="pill ok">Saved</span> ${r.items_saved} annotation(s). Session <code>${esc(r.session_id)}</code>.<br><br><button onclick="go('#/')">Back to Overview</button></div>`;
}

// ── Runner (input-driven pipeline) ──
let sse = null;
const runner = { prompt: "", data: "", loaded: false, model: "" };
route(/^\/runner$/, async () => {
  if (!runner.loaded) {
    try { const d = await api("/bench/defaults"); runner.model = d.model || ""; } catch (e) {}
    runner.loaded = true;
  } else if (!runner.model) {
    // loaded via sendToRunner but model not fetched yet
    try { const d = await api("/bench/defaults"); runner.model = d.model || ""; } catch (e) {}
  }
  const st = await api("/run/status").catch(() => ({}));
  app.innerHTML = runnerShell(st);
  wireRunner();
  loadConn();
  refreshDataParse("r");
  if (st.running && st.run_id) { refreshRunButtons(true); attachSSE(st.run_id); }
});

// ── Data entry: JSONL paste OR manual input+label rows (shared by Runner & Bench) ──
const DSCOPE = {
  r: { store: () => runner, footId: "rDataFoot", panelId: "rDataPanel" },
  b: { store: () => bench, footId: "bDataFoot", panelId: "bDataPanel" },
};
const dataMode = { r: "jsonl", b: "jsonl" };
const manualRows = { r: [{ input: "", label: "" }], b: [{ input: "", label: "" }] };

function dataPanelInner(scope) {
  const c = DSCOPE[scope], jsonl = dataMode[scope] === "jsonl";
  const tools = jsonl
    ? `<button class="mini" onclick="dataLoadSample('${scope}')">load sample</button><button class="mini" onclick="dataClear('${scope}')">clear</button>`
    : `<button class="mini" onclick="addRow('${scope}')">+ record</button><button class="mini" onclick="dataClear('${scope}')">clear</button>`;
  const body = jsonl
    ? `<textarea class="code" id="${scope}Data" spellcheck="false" placeholder='One record per line: { "input": "…", "label": "…" }' oninput="onDataInput('${scope}', this.value)">${esc(c.store().data)}</textarea>`
    : manualEditor(scope);
  return `<div class="p-label"><span class="lbl">Data</span><span class="spacer"></span>
      <span class="seg"><button class="mini ${jsonl ? "active" : ""}" onclick="setDataMode('${scope}','jsonl')">JSONL</button><button class="mini ${jsonl ? "" : "active"}" onclick="setDataMode('${scope}','manual')">Manual</button></span>
      ${tools}</div>
    ${body}
    <div class="p-foot" id="${c.footId}"></div>`;
}

function manualEditor(scope) {
  const rows = manualRows[scope].map((r, i) => `
    <div class="mrow">
      <textarea class="code" placeholder="input — what the model reads" oninput="updateRow('${scope}',${i},'input',this.value)">${esc(r.input)}</textarea>
      <textarea class="code" placeholder="label — ground truth (string or JSON)" oninput="updateRow('${scope}',${i},'label',this.value)">${esc(r.label)}</textarea>
      <button class="mini mdel" title="remove record" onclick="removeRow('${scope}',${i})">×</button>
    </div>`).join("");
  return `<div class="manual-rows">${rows}</div>`;
}

function rerenderData(scope) {
  const p = document.getElementById(DSCOPE[scope].panelId);
  if (p) p.innerHTML = dataPanelInner(scope);
  refreshDataParse(scope);
}
window.setDataMode = (scope, mode) => { dataMode[scope] = mode; rerenderData(scope); };
window.addRow = (scope) => { manualRows[scope].push({ input: "", label: "" }); rerenderData(scope); };
window.removeRow = (scope, i) => { manualRows[scope].splice(i, 1); if (!manualRows[scope].length) manualRows[scope].push({ input: "", label: "" }); rerenderData(scope); };
window.updateRow = (scope, i, field, val) => { manualRows[scope][i][field] = val; clearTimeout(DSCOPE[scope]._pt); DSCOPE[scope]._pt = setTimeout(() => refreshDataParse(scope), 400); };
window.onDataInput = (scope, val) => { DSCOPE[scope].store().data = val; clearTimeout(DSCOPE[scope]._pt); DSCOPE[scope]._pt = setTimeout(() => refreshDataParse(scope), 400); };
window.dataClear = (scope) => {
  if (dataMode[scope] === "manual") manualRows[scope] = [{ input: "", label: "" }];
  else DSCOPE[scope].store().data = "";
  rerenderData(scope);
};
window.dataLoadSample = async (scope) => {
  try { const d = await api("/bench/defaults"); DSCOPE[scope].store().data = d.sample_data || ""; } catch (e) {}
  dataMode[scope] = "jsonl"; rerenderData(scope);
};
function collectData(scope) {
  if (dataMode[scope] === "manual") {
    const rows = manualRows[scope]
      .filter(r => (r.input && r.input.trim()) || (r.label && r.label.trim()))
      .map(r => ({ input: r.input, label: r.label }));
    return JSON.stringify(rows);
  }
  return DSCOPE[scope].store().data || "";
}
async function refreshDataParse(scope) {
  const foot = document.getElementById(DSCOPE[scope].footId); if (!foot) return;
  const text = collectData(scope).trim();
  if (!text || text === "[]") { foot.className = "p-foot"; foot.textContent = dataMode[scope] === "manual" ? "no records yet — add input + label" : "no data loaded"; return; }
  try {
    const r = await post("/bench/parse", { data: text });
    if (r.warnings && r.warnings.length) { foot.className = "p-foot warn"; foot.textContent = `${r.count} records · ${r.warnings.length} skipped`; }
    else { foot.className = "p-foot ok"; foot.textContent = `${r.count} records parsed`; }
  } catch (e) { foot.className = "p-foot warn"; foot.textContent = "could not parse"; }
}

function runnerShell(st) {
  return `<div class="bench">
    <div class="b-head">
      <span class="b-title">Runner</span>
      <span class="b-sub">paste a starting prompt · load calls · configure · run the full self-improvement loop</span>
      <span class="b-model"><span class="dot"></span> model <b id="rModel">${esc(runner.model || "…")}</b></span>
    </div>
    ${connSection()}
    <div class="b-grid">
      <div class="b-panel">
        <div class="p-label"><span class="lbl">Starting prompt</span><span class="spacer"></span>
          <button class="mini" onclick="runnerLoadPrompt()">load current harness</button></div>
        <textarea class="code" id="rPrompt" spellcheck="false">${esc(runner.prompt)}</textarea>
        <div class="p-foot" id="rPromptFoot"></div>
      </div>
      <div class="b-panel" id="rDataPanel">${dataPanelInner("r")}</div>
    </div>

    <details class="runner-help">
      <summary>What input does the pipeline need? ▾</summary>
      <div class="help-body">
        <b>Starting prompt</b> — the system prompt the QC model scores with. It becomes the base harness version (label below). The loop edits and promotes new versions from it.<br><br>
        <b>Data</b> — JSONL (one JSON object per line) or a JSON array. Each record needs only two fields:
        <pre class="diff">{
  "input": "…what the model reads (transcript, question, any text)…",
  "label": "…the ground truth — a plain string or a JSON object…"
}
// Optional: "id" and "source" (auto-filled if omitted).
// The label is handed to the scorer. Default scorer = 9-step S/U (XLTC);
// for that task the label is the round/step JSON, e.g.
//   "label": { "round1": { "step1": { "id": 1, "r": "U", "e": "…" } } }
// A different task keeps this same intake and swaps only the scorer.</pre>
        <b>held_in / held_out</b> — how to split the records: <i>held_in</i> is used for mining failures &amp; picking edits, <i>held_out</i> checks for regressions. Leave 0 to auto-split ~2/3 : 1/3.<br>
        <b>max_rounds</b> — how many optimize rounds to try. <b>min_improvement</b> — a round whose held_out gain is below this counts as "dry". <b>dry_streak</b> — stop after this many dry rounds.<br>
        <span style="color:var(--warning)"><b style="color:var(--warning)">Caution</b> — running promotes real harness versions (writes configs/harness/versions/ + current.yaml + data/). Results appear on Pipeline / Memory / Review.</span>
      </div>
    </details>

    <div class="run-bar" style="flex-wrap:wrap;gap:16px">
      <label>base version <input type="text" id="rBase" value="v0.1.0" style="width:80px"></label>
      <label>held_in <input type="number" id="rHeldIn" value="0" min="0" style="width:64px"></label>
      <label>held_out <input type="number" id="rHeldOut" value="0" min="0" style="width:64px"></label>
      <label>max rounds <input type="number" id="rMaxR" value="3" min="1" max="10" style="width:56px"></label>
      <label>min impr % <input type="number" id="rMinImp" value="0.5" min="0" step="0.1" style="width:64px"></label>
      <label>dry streak <input type="number" id="rDry" value="2" min="1" style="width:56px"></label>
      <div class="grow"></div>
      <span id="statusText" class="muted" style="font-family:var(--mono);font-size:12px">${st && st.running ? `round ${st.current_round}/${st.max_rounds}` : "idle"}</span>
      <button class="run-btn" id="startBtn" onclick="startRun()" ${st && st.running ? "disabled" : ""}>Run</button>
      <button class="run-btn danger-btn" id="stopBtn" onclick="stopRun()" ${st && st.running ? "" : "disabled"}>Stop</button>
    </div>

    <div id="rSummary"></div>
    <div class="section-label">Live log</div>
    <pre class="log" id="log"></pre>
  </div>`;
}

function wireRunner() {
  const pt = document.getElementById("rPrompt");
  pt.addEventListener("input", () => { runner.prompt = pt.value; document.getElementById("rPromptFoot").textContent = `${pt.value.length} chars`; });
  document.getElementById("rPromptFoot").textContent = `${runner.prompt.length} chars`;
}
async function runnerLoadPrompt() { const d = await api("/bench/defaults"); runner.prompt = d.prompt || ""; document.getElementById("rPrompt").value = runner.prompt; document.getElementById("rPromptFoot").textContent = `${runner.prompt.length} chars`; }

function renderRunnerSummary(s) {
  const el = document.getElementById("rSummary"); if (!el || !s) return;
  const promoted = s.promoted;
  el.innerHTML = `<div class="readout" style="margin-top:16px">
    <div><div class="big-pct">${promoted ? benchPct(s.final_out) : benchPct(s.baseline_out)}<span class="u">%</span></div></div>
    <div class="meta">${promoted ? `promoted <b>${esc(s.new_version)}</b>` : "no promotion"}<br>
      held_in <b>${benchPct(s.baseline_in)}%</b>${promoted ? ` → <b>${benchPct(s.final_in)}%</b>` : ""}<br>
      held_out <b>${benchPct(s.baseline_out)}%</b>${promoted ? ` → <b>${benchPct(s.final_out)}%</b>` : ""}</div>
    <div class="type-bars" style="justify-content:center">
      <a class="mini" href="#/pipeline">Pipeline</a>
      <a class="mini" href="#/memory">Memory</a>
      <a class="mini" href="#/review">Review</a>
    </div></div>`;
}
function logLine(e) {
  const el = document.getElementById("log"); if (!el) return;
  if (e.done) { el.innerHTML += `\n<span class="l-done">— stream closed —</span>`; return; }
  const cls = e.step === "error" ? "l-error" : e.step === "round" ? "l-round" : e.step === "done" ? "l-done" : "";
  el.innerHTML += `<span class="ts">[${esc(e.timestamp || "")}]</span> <span class="${cls}">${esc(e.message)}</span>\n`;
  el.scrollTop = el.scrollHeight;
}
function attachSSE(runId) {
  if (sse) sse.close();
  sse = new EventSource(`/api/run/logs/${runId}`);
  sse.onmessage = async (ev) => {
    const e = JSON.parse(ev.data); logLine(e);
    if (e.done) {
      sse.close(); sse = null; refreshRunButtons(false);
      const st = await api("/run/status").catch(() => null);
      if (st && st.summary) renderRunnerSummary(st.summary);
    }
  };
  sse.onerror = () => { if (sse) { sse.close(); sse = null; } };
}
function refreshRunButtons(running) {
  const sb = document.getElementById("startBtn"), stb = document.getElementById("stopBtn"), stt = document.getElementById("statusText");
  if (sb) sb.disabled = running; if (stb) stb.disabled = !running;
  if (stt) stt.textContent = running ? "running…" : "idle";
}
async function startRun() {
  const body = {
    prompt: document.getElementById("rPrompt").value,
    data: collectData("r"),
    base_version: document.getElementById("rBase").value.trim() || "v0.1.0",
    held_in: parseInt(document.getElementById("rHeldIn").value) || 0,
    held_out: parseInt(document.getElementById("rHeldOut").value) || 0,
    max_rounds: parseInt(document.getElementById("rMaxR").value) || 3,
    min_improvement: (parseFloat(document.getElementById("rMinImp").value) || 0) / 100,
    dry_streak: parseInt(document.getElementById("rDry").value) || 2,
  };
  document.getElementById("log").innerHTML = "";
  document.getElementById("rSummary").innerHTML = "";
  let r;
  try { r = await post("/run/start", body); }
  catch (e) { return alert("Start failed: " + e.message); }
  refreshRunButtons(true); attachSSE(r.run_id);
}
async function stopRun() { await post("/run/stop", {}); document.getElementById("statusText").textContent = "stopping after round…"; }

// ════════════════════ BENCH — prompt eval workbench ════════════════════
const bench = { prompt: "", data: "", loaded: false, result: null, sse: null, running: false, activeRunId: null };
const TYPE_CLASS = { "làm_dịu": "type-d", "làm_rõ": "type-r", "làm_hài_lòng": "type-h" };
const TCOLOR = { "làm_dịu": "#d95926", "làm_rõ": "#9085e9", "làm_hài_lòng": "#199e70" };
const benchPct = (x) => (x == null ? "––" : Math.round(x * 100));

// ── Model connection (base_url / model_name / api_key) ──
function connSection() {
  const col = "display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase";
  return `<details class="runner-help" id="connBox">
    <summary>Model connection ▾</summary>
    <div class="help-body">
      <div class="row" style="align-items:flex-end;gap:14px">
        <label style="${col}">model name<input id="cfgModel" type="text" placeholder="gpt-4.1-mini" style="min-width:180px"></label>
        <label style="${col}">base_url<input id="cfgBase" type="text" placeholder="https://…/openai" style="min-width:300px"></label>
        <label style="${col}">api_key<input id="cfgKey" type="password" placeholder="leave blank to keep" style="min-width:200px"></label>
        <button onclick="saveConn()">Save</button>
        <span id="cfgFoot" style="font-family:var(--mono);font-size:12px"></span>
      </div>
      <div style="margin-top:8px">Applies immediately and persists to <b>.env</b>. An empty api_key keeps the current key.</div>
    </div>
  </details>`;
}
async function loadConn() {
  try {
    const c = await api("/config");
    const m = document.getElementById("cfgModel"), b = document.getElementById("cfgBase"), f = document.getElementById("cfgFoot");
    if (m) m.value = c.model_name || "";
    if (b) b.value = c.base_url || "";
    if (f) { f.textContent = c.has_key ? "key: set" : "key: not set"; f.className = c.has_key ? "" : "muted"; }
  } catch (e) {
    const f = document.getElementById("cfgFoot");
    if (f) { f.textContent = "config unavailable — restart the server?"; f.className = "muted"; }
  }
}
async function saveConn() {
  const foot = document.getElementById("cfgFoot");
  const body = {
    model_name: document.getElementById("cfgModel").value.trim(),
    base_url: document.getElementById("cfgBase").value.trim(),
  };
  const key = document.getElementById("cfgKey").value.trim();
  if (key) body.api_key = key;
  try {
    const r = await post("/config", body);
    foot.textContent = (r.persisted ? "saved · " : "applied · ") + (r.has_key ? "key set" : "no key");
    document.getElementById("cfgKey").value = "";
    ["bModel", "rModel"].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = r.model_name || "…"; });
    bench.model = r.model_name; runner.model = r.model_name;
  } catch (e) { foot.textContent = "error: " + e.message; }
}

route(/^\/$/, async () => {
  if (!bench.loaded) {
    try {
      const d = await api("/bench/defaults");
      bench.model = d.model || "";
    } catch (e) {}
    bench.loaded = true;
  }
  app.innerHTML = benchShell();
  wireBench();
  loadConn();
  refreshDataParse("b");
  await loadBenchHistory();
  const st = await api("/bench/status").catch(() => null);
  if (st && st.running) { bench.running = true; setRunning(true); document.getElementById("bResult").innerHTML = liveScaffold(st.total, st.steps); attachBenchSSE(st.run_id); }
  else if (bench.result) renderBenchResult(bench.result);
  else document.getElementById("bResult").innerHTML = benchEmptyState();
});

function benchEmptyState() {
  return `<div class="empty">No run in this session yet — press <b>Run</b>, or open a past run from <b>Run history</b> below.</div>`;
}
function clearBenchResult() {
  bench.result = null; bench.activeRunId = null; bench.liveSteps = [];
  const el = document.getElementById("bResult"); if (el) el.innerHTML = benchEmptyState();
  loadBenchHistory();
}

function benchShell() {
  return `<div class="bench">
    <div class="b-head">
      <span class="b-title">Bench</span>
      <span class="b-sub">paste a scoring prompt · load calls · run · read the verdicts</span>
      <span class="b-model"><span class="dot"></span> model <b id="bModel">${esc(bench.model || "…")}</b></span>
    </div>
    ${connSection()}
    <div class="b-grid">
      <div class="b-panel">
        <div class="p-label"><span class="lbl">Prompt</span><span class="spacer"></span>
          <button class="mini" onclick="benchLoadPrompt()">load current harness</button></div>
        <textarea class="code" id="bPrompt" spellcheck="false" placeholder="Paste the system prompt the QC model should score with…">${esc(bench.prompt)}</textarea>
        <div class="p-foot" id="bPromptFoot"></div>
      </div>
      <div class="b-panel" id="bDataPanel">${dataPanelInner("b")}</div>
    </div>
    <div class="run-bar">
      <label>label <input type="text" id="bLabel" placeholder="e.g. stricter empathy rule"></label>
      <label>records <select id="bSample"><option value="0">all</option><option value="3">first 3</option><option value="5">first 5</option><option value="10">first 10</option></select></label>
      <button class="run-btn" id="bRun" onclick="runBench()">Run</button>
    </div>
    <div class="result" id="bResult"></div>
    <div class="history" id="bHistory"></div>
  </div>`;
}

function wireBench() {
  const pt = document.getElementById("bPrompt");
  pt.addEventListener("input", () => { bench.prompt = pt.value; document.getElementById("bPromptFoot").textContent = `${pt.value.length} chars`; });
  document.getElementById("bPromptFoot").textContent = `${bench.prompt.length} chars`;
}

async function benchLoadPrompt() { const d = await api("/bench/defaults"); bench.prompt = d.prompt || ""; document.getElementById("bPrompt").value = bench.prompt; document.getElementById("bPromptFoot").textContent = `${bench.prompt.length} chars`; }

function setRunning(on) {
  const btn = document.getElementById("bRun"); if (!btn) return;
  btn.disabled = on; btn.textContent = on ? "running…" : "Run";
}

async function runBench() {
  const prompt = document.getElementById("bPrompt").value.trim();
  const data = collectData("b").trim();
  const label = document.getElementById("bLabel").value.trim();
  const sample = parseInt(document.getElementById("bSample").value) || 0;
  if (!prompt) return alert("Paste a prompt first.");
  if (!data) return alert("Load some data first.");
  let r;
  try { r = await post("/bench/run", { prompt, data, label, sample }); }
  catch (e) { return alert("Run failed: " + e.message); }
  bench.running = true; setRunning(true); bench.activeRunId = r.run_id;
  document.getElementById("bResult").innerHTML = liveScaffold(r.n_records, r.steps);
  attachBenchSSE(r.run_id);
}

function liveScaffold(total, steps) {
  bench.liveSteps = (steps && steps.length) ? steps : [];
  return `<div class="readout">
      <div><div class="big-pct" id="bBigPct">––<span class="u">%</span></div></div>
      <div class="meta">running…<br><b id="bDone">0</b> / ${total} records<br>live agreement</div>
      <div class="type-bars" id="bTypeBars"></div>
    </div>
    <div class="progress-track"><div id="bProg" style="width:0%"></div></div>
    <div class="section-label">Verdict matrix — filling as calls complete</div>
    <div class="matrix-wrap"><table class="matrix"><thead>${matrixHead(bench.liveSteps)}</thead><tbody id="bMatrixBody"></tbody></table>
      ${matrixLegend()}</div>`;
}
function matrixHead(steps) {
  steps = (steps && steps.length) ? steps : Array.from({ length: 9 }, (_, i) => ({ id: i + 1, type: "" }));
  let h = `<tr><th></th>`;
  for (const s of steps) h += `<th class="${TYPE_CLASS[s.type] || ""}">${s.id}</th>`;
  return h + `</tr>`;
}
function stepCount() { return (bench.liveSteps && bench.liveSteps.length) || 9; }
function matrixLegend() {
  return `<div class="matrix-legend">
    <span><span class="m-cell agree" style="width:16px;height:16px">S</span> model agrees with GT</span>
    <span><span class="m-cell diverge" style="width:16px;height:16px">U</span> model diverges (shows model's call)</span>
    <span><span class="m-cell missing" style="width:16px;height:16px">·</span> no model output</span>
    <span><span class="m-cell na" style="width:16px;height:16px"></span> no GT</span></div>`;
}

function attachBenchSSE(runId) {
  if (bench.sse) bench.sse.close();
  bench.sse = new EventSource(`/api/bench/stream/${runId}`);
  bench.sse.onmessage = async (ev) => {
    const e = JSON.parse(ev.data);
    if (e.kind === "record") onBenchRecord(e);
    else if (e.kind === "record_error") { const b = document.getElementById("bMatrixBody"); if (b) b.insertAdjacentHTML("beforeend", `<tr><td class="gutter" title="${esc(e.error)}"><b>${esc(e.task_id)}</b> err</td><td colspan="9"></td></tr>`); }
    else if (e.kind === "done" || e.kind === "closed" || e.kind === "error") {
      bench.sse.close(); bench.sse = null; bench.running = false; setRunning(false);
      if (e.kind === "error") { document.getElementById("bResult").innerHTML = `<div class="empty">Run failed: ${esc(e.error)}</div>`; return; }
      try { const full = await api("/bench/result"); bench.result = full; renderBenchResult(full); } catch (err) {}
      loadBenchHistory();
    }
  };
  bench.sse.onerror = () => { if (bench.sse) { bench.sse.close(); bench.sse = null; } setRunning(false); };
}

function onBenchRecord(e) {
  const bp = document.getElementById("bBigPct");
  if (bp) bp.innerHTML = `${benchPct(e.running_overall)}<span class="u">%</span>`;
  const done = document.getElementById("bDone"); if (done) done.textContent = e.index;
  const prog = document.getElementById("bProg"); if (prog) prog.style.width = `${(e.index / e.total) * 100}%`;
  const body = document.getElementById("bMatrixBody");
  if (body) body.insertAdjacentHTML("beforeend", `<tr><td class="gutter"><b>${esc(e.task_id)}</b> ${benchPct(e.agreement)}%</td>${Array(stepCount()).fill(`<td><div class="m-cell pending"></div></td>`).join("")}</tr>`);
}

function renderBenchResult(res) {
  const el = document.getElementById("bResult"); if (!el) return;
  bench.activeRunId = res.run_id;
  const steps = (res.steps && res.steps.length) ? res.steps
    : (res.matrix[0] ? res.matrix[0].cells.map(c => ({ id: c.id, type: "" })) : []);
  bench.liveSteps = steps;
  const bars = Object.keys(res.by_type || {}).map(t => {
    const v = res.by_type[t] || 0;
    return `<div class="tbar"><span class="name">${esc(t)}</span><span class="track"><span class="fill" style="width:${v * 100}%;background:${TCOLOR[t] || "var(--accent)"}"></span></span><span class="val">${benchPct(v)}%</span></div>`;
  }).join("");
  let rows = "";
  for (const r of res.matrix) {
    let cells = "";
    for (const c of r.cells) {
      if (c.state === "agree") cells += `<td><div class="m-cell agree" title="step ${c.id}: model ${c.model} = GT ${c.gt}">${esc(c.model)}</div></td>`;
      else if (c.state === "diverge") cells += `<td><div class="m-cell diverge" title="step ${c.id}: model ${c.model} ≠ GT ${c.gt}">${esc(c.model)}</div></td>`;
      else if (c.state === "missing") cells += `<td><div class="m-cell missing" title="no model output">·</div></td>`;
      else cells += `<td><div class="m-cell na" title="no ground truth"></div></td>`;
    }
    rows += `<tr><td class="gutter"><b>${esc(r.task_id)}</b> ${benchPct(r.agreement)}%</td>${cells}</tr>`;
  }
  const dis = res.disagreements.map(d => `<div class="dcard">
      <div class="dhead"><b>${esc(d.task_id)}</b> · step ${d.step_id} <span style="color:${TCOLOR[d.step_type] || "var(--muted)"}">${esc(d.step_type)}</span> · <span class="verdict">model <span class="m">${esc(d.model)}</span> → GT <span class="g">${esc(d.gt)}</span></span></div>
      <div class="evi"><span class="who">model:</span> ${esc(d.model_evidence)}<br><span class="who">GT:</span> ${esc(d.gt_evidence)}</div>
      ${d.model_thinking ? `<details style="margin-top:7px"><summary style="cursor:pointer;color:var(--muted);font-family:var(--mono);font-size:11px;letter-spacing:.04em">model thinking ▾</summary><div class="evi" style="margin-top:6px;white-space:pre-wrap;line-height:1.5">${esc(d.model_thinking)}</div></details>` : ""}</div>`).join("");
  el.innerHTML = `
    <div class="readout">
      <div><div class="big-pct">${benchPct(res.overall)}<span class="u">%</span></div></div>
      <div class="meta"><b>${res.n_records}</b> records${res.n_failed ? ` · <b>${res.n_failed}</b> failed` : ""}<br>model <b>${esc(res.model)}</b><br>${esc(res.created_at.replace("T", " "))}</div>
      <div class="type-bars">${bars}</div>
    </div>
    <div class="section-label">Verdict matrix · ${res.matrix.length} calls × ${steps.length} steps</div>
    <div class="matrix-wrap"><table class="matrix"><thead>${matrixHead(steps)}</thead><tbody>${rows}</tbody></table>${matrixLegend()}</div>
    <div class="section-label">Disagreements · ${res.disagreements.length}</div>
    ${dis ? `<div class="disagreements">${dis}</div>` : '<div class="empty">Perfect agreement — no disagreements.</div>'}
    <div style="margin-top:14px"><button class="mini" onclick="benchUsePrompt('${res.run_id}')">Load this prompt into editor</button> <button class="mini" onclick="sendToRunner('${res.run_id}')" title="Copy prompt + data sang Runner để chạy pipeline">→ Send to Runner</button> <button class="mini" onclick="clearBenchResult()">Clear</button></div>`;
}

async function benchUsePrompt(runId) {
  const r = await api("/bench/runs/" + runId);
  bench.prompt = r.prompt || ""; const pt = document.getElementById("bPrompt");
  if (pt) { pt.value = bench.prompt; document.getElementById("bPromptFoot").textContent = `${bench.prompt.length} chars`; }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function benchUseData(runId) {
  const r = await api("/bench/runs/" + runId);
  if (!r.matrix || !r.matrix.length) return;
  // Reconstruct JSONL from the run's matrix (task_id + cells carry no raw input,
  // so we fetch the actual records from bench/defaults as fallback, or just
  // surface a clear message that the raw data must come from the original source).
  // Best effort: load the current sample data as a starting point.
  const d = await api("/bench/defaults").catch(() => null);
  if (d && d.sample_data) {
    bench.data = d.sample_data;
    dataMode["b"] = "jsonl";
    rerenderData("b");
  }
}

// Send a Bench run's prompt + data straight to the Runner tab
async function sendToRunner(runId) {
  const r = await api("/bench/runs/" + runId);
  // Copy prompt
  runner.prompt = r.prompt || "";
  // Copy data — dùng records đã lưu trong run (đúng số records bench đã chạy)
  if (r.records && r.records.length) {
    runner.data = r.records.map(rec => JSON.stringify(rec)).join("\n");
  } else {
    // fallback cho các run cũ chưa có field records
    const d = await api("/bench/defaults").catch(() => null);
    if (d && d.sample_data) runner.data = d.sample_data;
  }
  dataMode["r"] = "jsonl";
  runner.loaded = true;
  // Navigate to Runner
  go("#/runner");
}

async function loadBenchHistory() {
  const el = document.getElementById("bHistory"); if (!el) return;
  const runs = await api("/bench/runs").catch(() => []);
  if (!runs.length) { el.innerHTML = ""; return; }
  const chips = runs.map(r => {
    const tk = Object.keys(r.by_type || {});
    const spark = tk.length
      ? tk.map(t => `<i style="height:${Math.max(8, (r.by_type[t] || 0) * 20)}px;background:${TCOLOR[t] || "var(--accent)"}"></i>`).join("")
      : `<i style="height:${Math.max(8, (r.overall || 0) * 20)}px;background:var(--accent)"></i>`;
    const active = r.run_id === bench.activeRunId ? " active" : "";
    return `<div class="rchip${active}" onclick="openBenchRun('${r.run_id}')">
      <div class="rp">${benchPct(r.overall)}%</div>
      <div class="rl">${esc(r.label || r.run_id)}</div>
      <div class="rl">${r.n_records} rec · ${esc((r.created_at || "").slice(11, 16))}</div>
      <div class="spark">${spark}</div>
      <div class="rchip-actions"><button class="mini" onclick="event.stopPropagation();sendToRunner('${r.run_id}')" title="Gửi sang Runner">→ Runner</button></div>
    </div>`;
  }).join("");
  el.innerHTML = `<div class="section-label">Run history · ${runs.length}</div><div class="rail">${chips}</div>`;
}

async function openBenchRun(runId, scroll = true) {
  const r = await api("/bench/runs/" + runId);
  bench.result = r; renderBenchResult(r); loadBenchHistory();
  if (scroll) document.getElementById("bResult").scrollIntoView({ behavior: "smooth", block: "start" });
}

render();
