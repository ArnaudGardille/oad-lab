/* Atelier oad-lab — page unique, sans dépendance (SPEC.md §3).
   Lit l'API JSON en polling ; seule écriture : POST /api/intents. */

"use strict";

const GRID = 8;               // grille comportementale (= feature_bins)
const POLL_MS = 3000;
const LOWCONF_GAMES = 24;     // sous une éval complète (stage 1 seul,
                              // 6-12 parties), le score est peu fiable (P3)

const state = {
  runs: [], run: null, programs: [], selected: null, cell: null,
  maxIter: Infinity, sliderTouched: false, lastEventTs: 0,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

/* score ∈ [0,1] → couleur (froid → chaud) */
function scoreColor(s) {
  if (s == null) return "#3a4152";
  return `hsl(${250 - 190 * Math.max(0, Math.min(1, s))} 65% 55%)`;
}

/* ---------- runs ---------- */

async function loadRuns() {
  state.runs = await jget("/api/runs");
  const sel = $("run-select");
  const prev = state.run;
  sel.innerHTML = state.runs.map((r) =>
    `<option value="${esc(r.id)}">${esc(r.id)} (${esc(r.status)})</option>`
  ).join("");
  if (!prev && state.runs.length) state.run = state.runs[0].id;
  if (state.run) sel.value = state.run;
}

function renderRunBadges() {
  const r = state.runs.find((x) => x.id === state.run);
  if (!r) { $("run-badges").innerHTML = ""; return; }
  const b = [];
  b.push(`<span class="badge ${esc(r.status)}">${esc(r.status)}</span>`);
  if (r.iterations_done != null)
    b.push(`<span class="badge">${r.iterations_done} itérations</span>`);
  if (r.games) b.push(`<span class="badge">${r.games} parties</span>`);
  if (r.errors) b.push(`<span class="badge">${r.errors} erreurs</span>`);
  if (r.hof) b.push(`<span class="badge">pool HOF</span>`);
  if (r.git_commit)
    b.push(`<span class="badge">${esc(r.git_commit)}${r.dirty ? " (sale)" : ""}</span>`);
  $("run-badges").innerHTML = b.join("");
}

/* ---------- carte comportementale ---------- */

function cellOf(p) {
  if (p.aggression == null || p.boom == null) return null;
  const clamp = (v) => Math.max(0, Math.min(GRID - 1, Math.floor(v * GRID)));
  return `${clamp(p.aggression)},${clamp(p.boom)}`;
}

function renderMap(progs) {
  const svg = $("map");
  const withDesc = progs.filter((p) => cellOf(p));
  $("map-empty").hidden = withDesc.length > 0;
  const cells = new Map();
  for (const p of withDesc) {
    const key = cellOf(p);
    const c = cells.get(key) || {n: 0, best: null};
    c.n++;
    if (c.best == null || (p.combined_score ?? -1) > c.best)
      c.best = p.combined_score;
    cells.set(key, c);
  }
  const S = 38, M = 30;   // taille cellule, marge axes
  let out = "";
  for (let x = 0; x < GRID; x++) {
    for (let y = 0; y < GRID; y++) {
      const key = `${x},${y}`, c = cells.get(key);
      const fill = c ? scoreColor(c.best) : "#1e2330";
      const sel = state.cell === key ? " selected" : "";
      const py = M + (GRID - 1 - y) * S;   // boom vers le haut
      out += `<rect class="cell${sel}" data-cell="${key}" x="${M + x * S}"
        y="${py}" width="${S}" height="${S}" fill="${fill}"></rect>`;
      if (c) out += `<text x="${M + x * S + S / 2}" y="${py + S / 2 + 3}"
        text-anchor="middle">${c.n}</text>`;
    }
  }
  out += `<text class="axis" x="${M + GRID * S / 2}" y="${M + GRID * S + 16}"
    text-anchor="middle">agression →</text>`;
  out += `<text class="axis" x="12" y="${M + GRID * S / 2}"
    text-anchor="middle" transform="rotate(-90 12 ${M + GRID * S / 2})">boom →</text>`;
  svg.innerHTML = out;
  svg.querySelectorAll("rect.cell").forEach((r) =>
    r.addEventListener("click", () => {
      state.cell = state.cell === r.dataset.cell ? null : r.dataset.cell;
      render();
    }));
}

/* ---------- DAG des lignées ---------- */

function layoutDag(progs) {
  // x = itération ; y = couloir : un enfant hérite du couloir de son
  // parent s'il est le premier, sinon nouveau couloir.
  const byId = new Map(progs.map((p) => [p.id, p]));
  const lanes = new Map();  // id -> lane
  let maxLane = 0, rootSeen = false;
  const childSeen = new Set();
  for (const p of progs) {   // triés par itération (ORDER BY du serveur)
    const parent = p.parent_id && byId.get(p.parent_id);
    if (parent && !childSeen.has(p.parent_id)) {
      lanes.set(p.id, lanes.get(p.parent_id) ?? ++maxLane);
      childSeen.add(p.parent_id);
    } else if (!parent && !rootSeen) {
      // première vraie racine (ou orphelin élagué) : couloir 0
      lanes.set(p.id, 0);
      rootSeen = true;
    } else {
      lanes.set(p.id, ++maxLane);
    }
  }
  return {lanes, maxLane, byId};
}

function renderDag(progs) {
  const svg = $("dag");
  const {lanes, maxLane, byId} = layoutDag(progs);
  const DX = 26, DY = 22, MX = 40, MY = 24;
  const maxIter = Math.max(0, ...progs.map((p) => p.iteration || 0));
  const W = MX * 2 + (maxIter + 1) * DX, H = MY * 2 + (maxLane + 1) * DY;
  svg.setAttribute("width", W);
  svg.setAttribute("height", Math.max(H, 200));
  const pos = (p) => [MX + (p.iteration || 0) * DX,
                      MY + (lanes.get(p.id) || 0) * DY];
  let out = "";
  for (const p of progs) {
    const parent = p.parent_id && byId.get(p.parent_id);
    if (!parent) continue;
    const [x1, y1] = pos(parent), [x2, y2] = pos(p);
    const faded = isFaded(p) || isFaded(parent) ? " faded" : "";
    out += `<path class="edge${faded}" d="M${x1},${y1} C${(x1 + x2) / 2},${y1}
      ${(x1 + x2) / 2},${y2} ${x2},${y2}"></path>`;
  }
  for (const p of progs) {
    const [x, y] = pos(p);
    const cls = ["node",
      isFaded(p) ? "faded" : "",
      (p.games ?? 0) < LOWCONF_GAMES ? "lowconf" : "",
      state.selected === p.id ? "selected" : ""].join(" ").trim();
    const r = 4 + 4 * (p.combined_score ?? 0);
    out += `<circle class="${cls}" data-id="${esc(p.id)}" cx="${x}" cy="${y}"
      r="${r}" fill="${scoreColor(p.combined_score)}">
      <title>it ${p.iteration} — ${(p.combined_score ?? 0).toFixed(3)}</title>
      </circle>`;
  }
  for (let i = 0; i <= maxIter; i += 10)
    out += `<text class="iter-axis" x="${MX + i * DX}" y="12"
      text-anchor="middle">${i}</text>`;
  svg.innerHTML = out;
  svg.querySelectorAll("circle.node").forEach((c) =>
    c.addEventListener("click", () => selectProgram(c.dataset.id)));
  $("dag-info").textContent =
    `${progs.length} programmes` + (state.cell ? " — cellule " + state.cell : "");
}

function isFaded(p) {
  if ((p.iteration || 0) > state.maxIter) return true;
  if (state.cell && cellOf(p) !== state.cell) return true;
  return false;
}

/* ---------- fiche programme ---------- */

async function selectProgram(id) {
  state.selected = id;
  render();
  const d = $("detail");
  d.innerHTML = `<div class="empty">chargement…</div>`;
  let p;
  try { p = await jget("/api/program?id=" + encodeURIComponent(id)); }
  catch { d.innerHTML = `<div class="empty">introuvable</div>`; return; }
  const wr = (v) => v == null ? "—" : Math.round(v * 100) + " %";
  const lowconf = (p.games ?? 0) < LOWCONF_GAMES;
  let html = `
    <div class="score" style="color:${scoreColor(p.combined_score)}">
      ${(p.combined_score ?? 0).toFixed(3)}</div>
    ${lowconf ? `<div class="lowconf-warn">⚠ estimé sur ${p.games ?? "?"}
      parties — faible confiance</div>` : ""}
    <table>
      <tr><td>itération</td><td>${p.iteration ?? "—"}</td></tr>
      <tr><td>vs Petra facile</td><td>${wr(p.wr_easy)}</td></tr>
      <tr><td>vs Petra moyen</td><td>${wr(p.wr_medium)}</td></tr>
      <tr><td>vs Petra dur</td><td>${wr(p.wr_hard)}</td></tr>
      <tr><td>agression / boom</td>
        <td>${p.aggression?.toFixed(2) ?? "—"} / ${p.boom?.toFixed(2) ?? "—"}</td></tr>
      <tr><td>parties</td><td>${p.games ?? "—"}</td></tr>
      <tr><td>id</td><td>${esc(p.id.slice(0, 8))}</td></tr>
    </table>
    <div class="verbs">
      <button id="verb-pin">📌 épingler</button>
      <button id="verb-explore" ${cellOf(p) ? "" : "disabled"}>🧭 explore cette zone</button>
    </div>`;
  if (p.changes)
    html += `<h2>Changement (résumé LLM)</h2>
      <div class="changes">${esc(p.changes)}</div>`;
  if (p.parent_code && p.code)
    html += `<h2>Diff vs parent</h2><pre class="diff">${diffHtml(
      p.parent_code, p.code)}</pre>`;
  d.innerHTML = html;
  $("verb-pin").addEventListener("click", () => sendIntent("pin", p.id));
  const ex = $("verb-explore");
  if (ex && !ex.disabled)
    ex.addEventListener("click", () => sendIntent("explore", cellOf(p)));
}

async function sendIntent(verb, target) {
  const note = verb === "pin" ? null :
    prompt("Note d'intention (optionnelle) :") || null;
  await fetch("/api/intents", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({verb, target, note}),
  });
  alert(`Intention « ${verb} » enregistrée (consommée au prochain run).`);
}

/* diff ligne à ligne (LCS) — suffisant pour un config de ~300 lignes */
function diffHtml(a, b) {
  const A = a.split("\n"), B = b.split("\n");
  const n = A.length, m = B.length;
  const L = Array.from({length: n + 1}, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1
                              : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { out.push(["ctx", A[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(["del", A[i++]]);
    else out.push(["add", B[j++]]);
  }
  while (i < n) out.push(["del", A[i++]]);
  while (j < m) out.push(["add", B[j++]]);
  // ne garder que les hunks (± 2 lignes de contexte)
  const keep = new Set();
  out.forEach(([k], idx) => {
    if (k === "ctx") return;
    for (let d = -2; d <= 2; d++) keep.add(idx + d);
  });
  let html = "", skipping = false;
  out.forEach(([k, line], idx) => {
    if (!keep.has(idx)) {
      if (!skipping) { html += `<span class="ctx">⋯</span>\n`; skipping = true; }
      return;
    }
    skipping = false;
    const mark = k === "add" ? "+" : k === "del" ? "−" : " ";
    html += `<span class="${k}">${mark} ${esc(line)}</span>\n`;
  });
  return html || "(identique)";
}

/* ---------- événements ---------- */

const EVENT_LABEL = {
  new_best: "★ nouveau meilleur", error: "✗ erreur",
  cell_occupied: "▦ cellule occupée", checkpoint: "⛁ checkpoint",
  completed: "✓ terminé", iteration: "· itération",
  evaluated: "· évalué", final_metrics: "✓ métriques finales",
};

function renderEvents(events) {
  const ul = $("events");
  for (const e of events) {
    if (!["new_best", "error", "cell_occupied", "completed",
          "final_metrics"].includes(e.kind)) continue;
    const li = document.createElement("li");
    li.className = e.kind;
    const t = new Date(e.ts * 1000).toLocaleTimeString("fr-FR");
    li.innerHTML = `<b>${EVENT_LABEL[e.kind] || esc(e.kind)}</b>
      ${e.iteration != null ? "it " + e.iteration : ""} <span>${t}</span>`;
    ul.prepend(li);
  }
  while (ul.children.length > 40) ul.lastChild.remove();
}

/* ---------- boucle ---------- */

function render() {
  renderRunBadges();
  const progs = state.programs;
  renderMap(progs.filter((p) => (p.iteration || 0) <= state.maxIter));
  renderDag(progs);
  const maxIter = Math.max(0, ...progs.map((p) => p.iteration || 0));
  const slider = $("iter-slider");
  slider.max = maxIter;
  if (!state.sliderTouched) { slider.value = maxIter; state.maxIter = Infinity; }
  $("iter-label").textContent = state.sliderTouched
    ? `itération ≤ ${slider.value}` : `${maxIter} itérations`;
}

async function refresh() {
  try {
    await loadRuns();
    if (!state.run) return;
    state.programs = await jget(
      "/api/programs?run=" + encodeURIComponent(state.run));
    const events = await jget(`/api/events?run=${encodeURIComponent(
      state.run)}&since=${state.lastEventTs}`);
    if (events.length) state.lastEventTs = events[events.length - 1].ts;
    renderEvents(events);
    render();
  } catch (e) {
    console.error(e);
  }
}

$("run-select").addEventListener("change", (e) => {
  state.run = e.target.value;
  state.programs = [];
  state.selected = state.cell = null;
  state.lastEventTs = 0;
  state.sliderTouched = false;
  $("events").innerHTML = "";
  $("detail").innerHTML = `<div class="empty">Cliquer un nœud ou une cellule.</div>`;
  refresh();
});
$("iter-slider").addEventListener("input", (e) => {
  state.sliderTouched = true;
  state.maxIter = +e.target.value;
  render();
});
setInterval(() => { if ($("live").checked) refresh(); }, POLL_MS);
refresh();
