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

function renderDag(all) {
  const svg = $("dag");
  let progs = all;
  if ($("essential").checked) {
    const keep = essentialIds(all);
    progs = all.filter((p) => keep.has(p.id));
  }
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

/* vrai si au moins la moitié des prédictions annoncées se sont
   confirmées (verdict au format "n/m confirmées — ..."). */
function verdictConfirmed(verdict) {
  if (!verdict) return false;
  const m = verdict.match(/^(\d+)\/(\d+)/);
  if (!m) return false;
  return Number(m[1]) >= Number(m[2]) / 2;
}

/* « l'essentiel » : les itérations qui ont porté fruit — meilleurs
   scores successifs, prédictions confirmées, premiers occupants de
   cellule — plus leurs ancêtres (le chemin qui y mène). */
function essentialIds(progs) {
  const keep = new Set();
  let best = -1;
  const cellSeen = new Set();
  for (const p of progs) {
    if ((p.combined_score ?? -1) > best) {
      best = p.combined_score;
      keep.add(p.id);
    }
    const cell = cellOf(p);
    if (cell && !cellSeen.has(cell)) { cellSeen.add(cell); keep.add(p.id); }
    if (verdictConfirmed(p.verdict)) keep.add(p.id);
  }
  const byId = new Map(progs.map((p) => [p.id, p]));
  for (const id of [...keep]) {
    let cur = byId.get(id);
    while (cur && cur.parent_id && !keep.has(cur.parent_id)) {
      keep.add(cur.parent_id);
      cur = byId.get(cur.parent_id);
    }
  }
  return keep;
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
  const verdictCls = !p.verdict ? "" : verdictConfirmed(p.verdict) ? "ok" : "ko";
  let html = `
    <div class="score" style="color:${scoreColor(p.combined_score)}">
      ${(p.combined_score ?? 0).toFixed(3)}</div>
    ${lowconf ? `<div class="lowconf-warn">⚠ estimé sur ${p.games ?? "?"}
      parties — faible confiance</div>` : ""}
    ${p.hypothesis ? `<div class="hyp">💡 ${esc(p.hypothesis)}</div>` : ""}
    ${p.prediction ? `<div class="dim">prédit : ${esc(p.prediction)}</div>` : ""}
    ${p.verdict ? `<div class="verdict ${verdictCls}">verdict :
      ${esc(p.verdict)}</div>` : ""}
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
      <button id="verb-cut">✂ couper</button>
      <button id="verb-branch">🌱 brancher</button>
      <button id="verb-explore" ${cellOf(p) ? "" : "disabled"}>🧭 explorer ici</button>
    </div>`;
  html += `<h2>Matchs</h2><ul class="matches" id="match-list">
    <li class="dim">chargement…</li></ul>
    <div class="charts" id="match-charts"></div>`;
  if (p.changes)
    html += `<h2>Changement (résumé LLM)</h2>
      <div class="changes">${esc(p.changes)}</div>`;
  if (p.parent_code && p.code)
    html += `<h2>Diff vs parent</h2><pre class="diff">${diffHtml(
      p.parent_code, p.code)}</pre>`;
  d.innerHTML = html;
  $("verb-pin").addEventListener("click", () => sendIntent("pin", p.id));
  $("verb-cut").addEventListener("click", () => sendIntent("cut", p.id));
  $("verb-branch").addEventListener("click", () =>
    sendIntent("branch", p.id, true));
  const ex = $("verb-explore");
  if (ex && !ex.disabled)
    ex.addEventListener("click", () => sendIntent("explore", cellOf(p)));
  loadMatches(p.id);
}

async function loadMatches(programId) {
  const ul = $("match-list");
  let matches;
  try { matches = await jget("/api/matches?id=" + encodeURIComponent(programId)); }
  catch { ul.innerHTML = `<li class="dim">indisponibles</li>`; return; }
  if (!matches.length) {
    ul.innerHTML = `<li class="dim">aucun match relié</li>`;
    return;
  }
  ul.innerHTML = matches.map((m, i) => {
    const res = m.cand_won == null ? "∅" : m.cand_won ? "V" : "D";
    const cls = m.cand_won == null ? "dim" : m.cand_won ? "w" : "l";
    const min = m.game_s ? Math.round(m.game_s / 60) + " min" : "—";
    return `<li data-i="${i}"><span class="${cls}">${res}</span>
      vs ${esc(m.opponent)} d${m.opp_diff}
      <span class="dim">pos ${m.cand_pos} · seed ${m.seed} · ${min}</span></li>`;
  }).join("");
  ul.querySelectorAll("li").forEach((li) =>
    li.addEventListener("click", () => {
      ul.querySelectorAll("li").forEach((x) => x.classList.remove("selected"));
      li.classList.add("selected");
      const m = matches[+li.dataset.i];
      if (m.replay) loadCharts(m.replay, m.cand_pos);
      else $("match-charts").innerHTML =
        `<div class="dim">pas de replay pour ce match</div>`;
    }));
}

async function loadCharts(replay, player) {
  const box = $("match-charts");
  box.innerHTML = `<div class="dim">chargement…</div>`;
  let s;
  try {
    s = await jget(`/api/series?replay=${encodeURIComponent(replay)}` +
                   `&player=${player}`);
  } catch { box.innerHTML = `<div class="dim">séries indisponibles</div>`; return; }
  if (s.error || !s.time?.length) {
    box.innerHTML = `<div class="dim">${esc(s.error || "vide")}</div>`;
    return;
  }
  box.innerHTML = Object.entries(s.series).map(([label, vals]) =>
    `<div class="lbl">${esc(label)}</div>${sparkline(s.time, vals)}`
  ).join("");
}

function sparkline(time, vals) {
  const W = 340, H = 90, P = 6;
  const n = Math.min(time.length, vals.length);
  if (!n) return "";
  const tMax = time[n - 1] || 1;
  const realMax = Math.max(...vals.slice(0, n));
  const vMax = Math.max(1, realMax);
  const pts = [];
  for (let i = 0; i < n; i++)
    pts.push(`${(P + (W - 2 * P) * time[i] / tMax).toFixed(1)},` +
             `${(H - P - (H - 2 * P) * vals[i] / vMax).toFixed(1)}`);
  const lastMin = Math.round(tMax / 60);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <polyline points="${pts.join(" ")}" fill="none"
      stroke="var(--accent)" stroke-width="1.5"/>
    <text x="${W - P}" y="12" text-anchor="end" fill="var(--dim)"
      font-size="10">max ${Math.round(realMax)} · ${lastMin} min</text>
  </svg>`;
}

async function sendIntent(verb, target, askNote = false) {
  const note = verb === "pin" || verb === "cut" ? null :
    prompt(askNote ? "Intention de la branche (devient une directive) :"
                   : "Note d'intention (optionnelle) :") || null;
  if (askNote && !note) {
    alert("Branche annulée : une intention est requise.");
    return;
  }
  await fetch("/api/intents", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({verb, target, note}),
  });
  renderIntents();
  alert(`Intention « ${verb} » enregistrée (consommée au prochain run).`);
}

/* ---------- carnet de laboratoire ---------- */

const KIND_BADGE = {law: ["⚖", "loi"], impasse: ["⛔", "impasse"],
                    question: ["❓", "question"]};

async function renderLessons() {
  let lessons;
  try { lessons = await jget("/api/lessons"); } catch { return; }
  const ul = $("lessons");
  ul.innerHTML = lessons.length ? lessons.map((l) => {
    const [icon, label] = KIND_BADGE[l.kind] || ["·", l.kind];
    return `<li><b title="${esc(label)}">${icon}</b>
      ${esc(l.statement)}
      ${l.confidence ? `<span class="dim">(${esc(l.confidence)})</span>` : ""}</li>`;
  }).join("") : `<li class="dim">vide — le distillateur le remplit
    après chaque run</li>`;
}

/* ---------- intentions en attente ---------- */

const VERB_ICON = {pin: "📌", cut: "✂", branch: "🌱", explore: "🧭"};

async function renderIntents() {
  let intents;
  try { intents = await jget("/api/intents"); } catch { return; }
  const ul = $("intents");
  const pending = intents.filter((i) => i.status === "pending");
  ul.innerHTML = pending.length ? pending.map((i) =>
    `<li><b>${VERB_ICON[i.verb] || ""} ${esc(i.verb)}</b>
     ${esc((i.target || "").slice(0, 12))}
     ${i.note ? `<span class="dim">« ${esc(i.note)} »</span>` : ""}</li>`
  ).join("") : `<li class="dim">aucune — les verbes de la fiche en
    créent ; consommées au prochain run</li>`;
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
    renderIntents();
    renderLessons();
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
$("essential").addEventListener("change", render);

/* ---------- chat analyste ---------- */

$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  const log = $("chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="q">${esc(q)}</div>`);
  const wait = document.createElement("div");
  wait.className = "a dim";
  wait.textContent = "analyse en cours…";
  log.appendChild(wait);
  wait.scrollIntoView();
  try {
    const r = await fetch("/api/chat", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: q, program_id: state.selected}),
    });
    const data = await r.json();
    wait.className = data.answer ? "a" : "a err";
    wait.textContent = data.answer || data.error || "erreur";
  } catch (err) {
    wait.className = "a err";
    wait.textContent = String(err);
  }
  wait.scrollIntoView();
});
setInterval(() => { if ($("live").checked) refresh(); }, POLL_MS);
refresh();
