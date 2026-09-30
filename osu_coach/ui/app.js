/* osu!coach window: profile, replay analysis, beatmap search, settings. Talks to gui.py's JSON API. */

// --- helpers -----------------------------------------------------------------------------------------------

function h(sel, props, ...kids) {
  const [tag, ...cls] = sel.split(".");
  const e = document.createElement(tag || "div");
  if (cls.length) e.className = cls.join(" ");
  if (props != null && (typeof props !== "object" || props instanceof Node || Array.isArray(props))) {
    kids.unshift(props);
    props = null;
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") e.innerHTML = v;
    else if (k === "class") e.className += (e.className ? " " : "") + v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k === "value" || k === "checked" || k === "disabled") e[k] = v;
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
}
const $ = (sel, root = document) => root.querySelector(sel);
/** replaceChildren without the nulls and falses of conditional children (it would print them as text). */
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const pct = (x, d = 1) => x == null ? "—" : `${(x * 100).toFixed(d)}%`;
const num = (x, d = 0) => x == null ? "—" : Number(x).toFixed(d);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M4 20h16"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v12M7 11l5 5 5-5M4 20h16"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/></svg>',
  link: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  bulb: '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z"/></svg>',
  tablet: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><rect x="7" y="8" width="10" height="7" rx="1"/></svg>',
  keyboard: '<svg viewBox="0 0 24 24"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 19h16M6 15l4-4 3 3 5-6"/></svg>',
  target: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".5"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  replay: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
};
const icon = name => h("span", { html: ICON[name], style: { display: "contents" } });

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch("/api/" + path, opts);
  let data;
  try { data = await r.json(); } catch { throw new Error(`server error (${r.status})`); }
  if (!r.ok || data.error) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, err = false) {
  const t = h("div.toast" + (err ? ".err" : ""), msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), err ? 8000 : 4500);
}

/** A progress box for a background job, with a cancel button. */
function progressBox(title) {
  const lbl = h("span", title), cnt = h("span.muted.mono");
  const fill = h("i", { style: { width: "0%" } });
  const bar = h("div.bar.indet", fill);
  const cancel = h("button.btn.sm.ghost", "Stop");
  const el = h("div.progress", h("div.lbl", h("div.row", h("div.spinner"), lbl), h("div.row", cnt, cancel)), bar);
  let jobId = null;
  cancel.addEventListener("click", () => { if (jobId) api(`job/${jobId}/cancel`, {}); cancel.disabled = true; });
  return {
    el,
    attach(id) { jobId = id; },
    update(j) {
      if (j.label) lbl.textContent = j.label;
      if (j.total) {
        bar.classList.remove("indet");
        fill.style.width = `${Math.round(j.done / j.total * 100)}%`;
        cnt.textContent = `${j.done}/${j.total}`;
      } else {
        bar.classList.add("indet");
        cnt.textContent = `${Math.round(j.elapsed || 0)} s`;
      }
    },
  };
}

async function runJob(route, body, box, onPartial) {
  const job = await api(route, body);
  box?.attach(job.id);
  for (;;) {
    await sleep(350);
    const j = await api("job/" + job.id);
    box?.update(j);
    if (onPartial && j.partial) onPartial(j.partial);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
    if (j.status === "cancelled") throw new Error("Stopped.");
  }
}

// osu!'s star rating colours
const STAR_STOPS = [[0.1, "#4290fb"], [1.25, "#4fc0ff"], [2, "#4fffd5"], [2.5, "#7cff4f"], [3.3, "#f6f05c"], [4.2, "#ff8068"],
  [4.9, "#ff4e6f"], [5.8, "#c645b8"], [6.7, "#6563de"], [7.7, "#18158e"], [9, "#000000"]];
function starColor(s) {
  if (s <= STAR_STOPS[0][0]) return STAR_STOPS[0][1];
  for (let i = 1; i < STAR_STOPS.length; i++) {
    const [b, cb] = STAR_STOPS[i], [a, ca] = STAR_STOPS[i - 1];
    if (s <= b) {
      const k = (s - a) / (b - a), p = x => parseInt(x, 16);
      const mix = j => Math.round(p(ca.slice(j, j + 2)) + (p(cb.slice(j, j + 2)) - p(ca.slice(j, j + 2))) * k);
      return `rgb(${mix(1)},${mix(3)},${mix(5)})`;
    }
  }
  return "#000";
}
function starChip(s) {
  if (s == null) return null;
  const bg = starColor(s);
  return h("span.chip.stars", { style: { background: bg, color: s >= 6.5 ? "#ffd966" : "#15101a" } }, `★ ${s.toFixed(2)}`);
}
const modsChip = m => h("span.chip.mods", m || "NM");
const coverUrl = setId => setId ? `https://assets.ppy.sh/beatmaps/${setId}/covers/cover.jpg` : null;
const openOsu = url => api("open", { url }).catch(e => toast(e.message, true));

function fmtDate(unix) {
  const d = new Date(unix * 1000), now = Date.now(), diff = (now - d) / 1000;
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} d ago`;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
const fmtLen = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

function emptyState(ico, title, text, ...actions) {
  return h("div.empty", h("div.big-ico", { html: ICON[ico] }), h("h3", title), h("p", text), h("div.row", { style: { justifyContent: "center" } }, actions));
}

window.addEventListener("error", e => api("log", { message: `${e.message} at ${e.filename}:${e.lineno}:${e.colno}` }).catch(() => {}));
window.addEventListener("unhandledrejection", e => api("log", { message: `unhandled: ${e.reason?.stack || e.reason}` }).catch(() => {}));

// --- app shell ---------------------------------------------------------------------------------------------

const S = { state: null, pages: {}, current: null, profileData: null, profileListeners: [], profileLoading: null };

async function boot() {
  try {
    S.state = await api("state");
  } catch (e) {
    $("#page").append(h("div.page", emptyState("x", "The server is not responding", e.message)));
    return;
  }
  document.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => show(b.dataset.page)));
  setInterval(() => api("ping").catch(() => {}), 20000);
  show(S.state.first_run ? "setup" : "replays");
}

const BUILDERS = { profile: buildProfile, skills: buildSkills, improvement: buildImprovement, replays: buildReplays,
  search: buildSearch, settings: buildSettings, setup: buildSetup };

function show(page) {
  S.current = page;
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  for (const [name, el] of Object.entries(S.pages)) el.classList.toggle("hidden", name !== page);
  if (!S.pages[page]) {
    const el = h("div.page-root", { style: { height: "100vh", overflow: page === "replays" ? "hidden" : "auto" } });
    S.pages[page] = el;
    $("#page").append(el);
    BUILDERS[page](el);
  }
}

// --- profile -----------------------------------------------------------------------------------------------

const SKILL_NAMES = {
  streams: "Streams", "tap timing": "Tapping speed", alt: "Alt", jumps: "Jumps", irregular: "Irregular rhythms",
  fingercontrol: "Finger control", sliders: "Sliders (tech)", stamina: "Stamina", high_ar: "High AR (reaction)",
  reading: "Reading", accuracy: "Accuracy",
};
const SKILL_SCALE = {
  streams: [120, 300], "tap timing": [120, 300], alt: [100, 220], jumps: [120, 300], irregular: [2, 16],
  fingercontrol: [5, 25], sliders: [10, 60], stamina: [0, 3000], high_ar: [9.5, 11], reading: [0, 12], accuracy: [6, 11],
};
const CAT_NAMES = {
  streams: "Streams", alt: "Alt", jumps: "Jumps", irregular: "Irregular rhythms", sliders: "Sliders", reading: "Reading",
  accuracy: "Accuracy", stamina: "Stamina", dt: "DT / high AR", aim: "Other aim", setup: "Setup", info: "Info",
};

function valueText(v, cens, unit) {
  if (v == null) return "—";
  const d = unit === "AR" || unit === "OD" || unit === "notes/s" || unit === "taps/s" ? 1 : 0;
  return `${cens === "above" ? ">" : cens === "below" ? "<" : ""}${Number(v).toFixed(d)}`;
}

const NO_NOTES = new Set(["high_ar", "reading", "accuracy"]);   // levels shown without the analysis' comments

// one short line under the levels of the cards that need it
const EXPLAIN = {
  stamina: "Notes into a map before your hit error gets 10% above your usual (→ 25% above it).",
  high_ar: "The highest AR before your misses double compared with AR 9-10.",
};

function skillCard(sk) {
  if (sk.key === "high_ar") {   // only the maximum AR: the limit, or beyond what was played
    const l = sk.levels[0];
    const top = !l ? null : l.limit != null ? { v: l.limit, c: l.limit_censored } : sk.played ? { v: sk.played[1], c: "above" } : null;
    sk = { ...sk, levels: top ? [{ label: "max AR", comfort: top.v, comfort_censored: top.c, limit: null }] : [] };
  }
  const unit = sk.unit;
  let [lo, hi] = SKILL_SCALE[sk.key] || [0, Math.max(1, ...sk.levels.map(l => Math.max(l.comfort || 0, l.limit || 0))) * 1.2];
  const pos = v => v == null ? 0 : Math.max(2, Math.min(100, (v - lo) / (hi - lo) * 100));
  const first = sk.levels[0];
  const headline = first ? h("div", { style: { textAlign: "right" } },
    h("div.big-num", valueText(first.comfort, first.comfort_censored, sk.unit)), h("div.small.muted", unit)) : null;
  const levels = sk.levels.map(l => h("div.level",
    h("div.lab", h("span", l.label || "level"),
      h("b", valueText(l.comfort, l.comfort_censored, sk.unit), l.limit != null ? h("span.muted", ` → ${valueText(l.limit, l.limit_censored, sk.unit)}`) : "")),
    h("div.meter", l.limit != null ? h("div.lim", { style: { width: pos(l.limit) + "%" } }) : null, h("div.com", { style: { width: pos(l.comfort) + "%" } }))));
  return h("div.card.skill-card",
    h("div.head", h("div", h("h3", SKILL_NAMES[sk.key] || sk.name), h("div.sum", sk.summary)), headline),
    sk.missing ? h("div.note", sk.missing) : null,
    levels,
    NO_NOTES.has(sk.key) ? null : (sk.notes || []).map(n => h("div.note", n)),
    EXPLAIN[sk.key] ? h("div.note", EXPLAIN[sk.key]) : null);
}

/** Bad habits of one category as a single focused piece of advice: the costliest one leads, the others support it. */
function habitGroup(group, n, maxImpact, plays) {
  const [top, ...rest] = group.items;
  const isAcc = group.items.every(x => x.kind === "accuracy");
  const impact = isAcc ? "affects accuracy" : `~${Math.round(group.impact)} misses in ${plays} plays`;
  return h("div.habit",
    h("div.rank" + (isAcc ? ".acc" : ""), n),
    h("div",
      h("div.top", h("span.chip" + (isAcc ? ".warn" : ".pink"), CAT_NAMES[group.category] || group.category), h("h3", top.title)),
      h("div.impact", h("div.meter", h("div.com", { style: { width: `${Math.max(3, group.impact / maxImpact * 100)}%` } })), h("span", impact)),
      h("p.detail", top.detail),
      rest.length ? h("ul.also", rest.map(x => h("li", h("b", x.title + ". "), x.detail))) : null,
      top.solution ? h("div.solution", h("span", { html: ICON.bulb }), h("div", h("b", "Fix: "), top.solution)) : null));
}

function groupHabits(habits) {
  const groups = new Map();
  for (const x of habits) {
    if (!groups.has(x.category)) groups.set(x.category, { category: x.category, items: [], impact: 0 });
    const g = groups.get(x.category);
    g.items.push(x);
    g.impact = Math.max(g.impact, x.impact);   // habits of a category overlap: their misses aren't added up
  }
  return [...groups.values()].sort((a, b) => b.impact - a.impact);
}

function adviceCard(title, ico, items, emptyText) {
  return h("div.card",
    h("div.card-head", h("div.ico", { html: ICON[ico] }), h("h3", title)),
    items.length ? items.map(i => h("div.advice-item",
      h("div.row", h("b", i.title), i.kind === "info" ? h("span.chip.green", "ok") : h("span.chip.pink", "change")),
      h("p", i.detail))) : h("p.muted", { style: { margin: "8px 0 0" } }, emptyText));
}

function trendTable(trends) {
  if (!trends?.length) return h("p.muted", "At least 10 recent plays are needed for a trend.");
  return h("table.trend-table",
    h("thead", h("tr", h("th", "Category"), h("th", "Older plays"), h("th", "Newer plays"), h("th", ""))),
    h("tbody", trends.map(t => h("tr",
      h("td", CAT_NAMES[t.category] || t.name),
      h("td.mono", pct(t.old) + " misses"),
      h("td.mono", pct(t.new) + " misses"),
      h("td", t.trend === "worse" ? h("span.chip.red", "▲ worse") : t.trend === "better" ? h("span.chip.green", "▼ better") : h("span.chip", "steady"))))));
}

/** Recomputing the profile feeds both the Profile and the Skills pages. */
async function recomputeProfile(btn, prog) {
  btn.disabled = true;
  const box = progressBox("Getting ready…");
  put(prog, box.el);
  try {
    const p = await runJob("profile", {}, box);
    S.profileData = p;
    S.profileListeners.forEach(f => f(p));
    toast("Profile updated.");
  } catch (e) {
    toast(e.message, true);
  } finally {
    put(prog);
    btn.disabled = false;
  }
}

/** Read the profile again (its setup advice follows the current settings) and redraw the pages showing it. */
function reloadProfile() {
  if (!S.profileData) return;
  api("profile").then(p => { S.profileData = p; S.profileListeners.forEach(f => f(p)); }).catch(() => {});
}

function onProfile(render) {
  S.profileListeners.push(render);
  if (S.profileData) render(S.profileData);
  else if (!S.profileLoading) {
    S.profileLoading = api("profile").then(p => { S.profileData = p; S.profileListeners.forEach(f => f(p)); })
      .catch(e => toast(e.message, true));
  }
}

function buildProfile(root) {
  const page = h("div.page");
  root.append(page);
  const sub = h("p");
  const btn = h("button.btn.primary", { onclick: () => recomputeProfile(btn, prog) }, icon("refresh"), "Recompute profile");
  const prog = h("div");
  const body = h("div");
  page.append(h("div.page-head", h("div", h("h1", "Profile"), sub), btn), prog, body);
  S.startProfile = () => recomputeProfile(btn, prog);   // the setup wizard's last step

  onProfile(p => {
    body.innerHTML = "";
    const hb = p.habits;
    sub.textContent = hb ? `Bad habits from your last ${hb.plays} plays (${hb.objects.toLocaleString("en")} notes) · updated ${fmtDate(hb.updated)}`
      : "Bad habits, setup advice and trend from your recent plays.";
    if (!hb) {
      body.append(h("div.card", emptyState("user", "No profile yet", "It analyses your recent plays: it takes a minute or so.",
        h("button.btn.primary.big", { onclick: e => recomputeProfile(e.currentTarget, prog) }, "Compute the profile"))));
      return;
    }
    const groups = groupHabits(hb.habits || []);
    const maxImpact = Math.max(1, ...groups.map(g => g.impact));
    body.append(h("div.section",
      h("div.section-title", h("h2", "Bad habits"), h("span.count", `${groups.length} areas · ordered by how much they cost you`)),
      groups.length ? h("div.card.flush", groups.map((g, i) => habitGroup(g, i + 1, maxImpact, hb.plays)))
        : h("div.card", h("p.muted", "No clear bad habits in your recent plays."))));
    body.append(h("div.section",
      h("div.section-title", h("h2", "Area and rapid trigger"), h("span.count", hb.setup_desc)),
      h("div.advice-grid",
        adviceCard("Area / sensitivity", "tablet", hb.setup?.area || [], "No advice with the current cutoffs."),
        adviceCard("Keyboard / rapid trigger", "keyboard", hb.setup?.keys || [], "No key problems with the current cutoffs."))));
    body.append(h("div.section",
      h("div.section-title", h("h2", "Trend"), h("span.count", "miss rate: older half against newer half of your recent plays")),
      h("div.card", trendTable(hb.trends))));
    const notes = (hb.notes || []).filter(n => !/area|sensitivity|speed, not/i.test(n.title));
    if (notes.length) {
      body.append(h("div.section", h("div.section-title", h("h2", "Notes")),
        h("div.card", notes.map(n => h("div.advice-item", h("b", n.title), h("p", n.detail))))));
    }
  });
}

// --- skills ------------------------------------------------------------------------------------------------

const rateColor = (x, ok, bad) => x == null ? "" : x <= ok ? "var(--ok)" : x >= bad ? "var(--miss)" : "var(--warn)";
const cens = v => v == null ? "—" : `${v.censored === "above" ? ">" : v.censored === "below" ? "<" : ""}${Math.round(v.bpm)}`;

/** A cell with a value and a small bar (share of `max`). */
function barCell(value, max, text, color) {
  if (value == null) return h("td.muted", "—");
  return h("td", h("div.barcell", h("div.meter", h("div.com", { style: { width: `${Math.max(2, Math.min(100, value / max * 100))}%`, ...(color ? { background: color, boxShadow: "none" } : {}) } })),
    h("span.mono", text)));
}

function statTable(head, rows) {
  return h("table.trend-table.stat-table", h("thead", h("tr", head.map(x => h("th", x)))), h("tbody", rows));
}

function streamsCard(st) {
  const groups = st.groups.map(g => {
    const detail = h("tr.detail.hidden", h("td", { colspan: 5 },
      g.bins.length ? statTable(["BPM", "Streams", "UR", "Finished"], g.bins.map(b => h("tr",
        h("td.mono", `${b.lo}-${b.hi}`), h("td.mono", b.runs),
        barCell(b.ur, 350, b.ur == null ? "—" : `${Math.round(b.ur)}`, b.ur == null ? null : Math.abs(b.fitted - g.comfort_ur) <= st.ur_tolerance * g.comfort_ur ? "var(--ok)" : "var(--warn)"),
        barCell(b.cleared, 1, pct(b.cleared, 0), rateColor(1 - b.cleared, 0.15, 0.5))))) : h("p.muted", "No streams in this range.")));
    const row = h("tr.clickable", { onclick: () => detail.classList.toggle("hidden"), title: "Show the BPM table" },
      h("td", h("b", g.label), h("div.small.muted", `${g.runs} streams`), g.note ? h("div.small", { style: { color: "var(--warn)" } }, g.note) : null),
      h("td.big-cell", cens(g.min)), h("td.big-cell.pinkv", cens(g.comfort)), h("td.big-cell", cens(g.max)),
      h("td.muted", { html: ICON.chev, class: "chev" }));
    return [row, detail];
  });
  return h("div.card.skill-card.wide",
    h("div.head", h("div", h("h3", "Streams"),
      h("div.sum", "From 160 BPM, per 10 BPM. Comfort: the best BPM^0.9 / UR among the speeds you finish; "
        + `minimum: the slowest speed whose UR is within ${Math.round(st.ur_tolerance * 100)}% of the UR at the comfort speed (green in the table); `
        + "maximum: the fastest you still finish half of your streams, whatever the UR. Click a row for the BPM table."))),
    statTable(["Length", "Minimum BPM", "Comfort BPM", "Maximum BPM", ""], groups));
}

function altCard(alt) {
  const maxUr = Math.max(250, ...alt.rows.map(r => r.ur || 0));
  return h("div.card.skill-card",
    h("div.head", h("div", h("h3", "Alt"), h("div.sum", "Average UR on truly alternated runs, per 10 BPM (1/4 notes)."))),
    statTable(["BPM", "Notes", "UR", "Missed"], alt.rows.map(r => h("tr",
      h("td.mono", `${r.lo}-${r.hi}`), h("td.mono", r.notes),
      barCell(r.ur, maxUr, r.ur == null ? "—" : `${Math.round(r.ur)}`),
      h("td.mono", { style: { color: rateColor(r.miss, 0.03, 0.08) } }, r.miss == null ? "—" : pct(r.miss))))));
}

function fingerCard(fc) {
  const maxUr = Math.max(250, ...fc.rows.map(r => r.ur || 0));
  return h("div.card.skill-card",
    h("div.head", h("div", h("h3", "Finger control"), h("div.sum", "Miss rate and average UR by how many notes the burst has."))),
    statTable(["Pattern", "Notes", "Missed", "UR"], fc.rows.map(r => h("tr",
      h("td", r.label), h("td.mono", r.notes),
      h("td.mono", { style: { color: rateColor(r.miss, 0.03, 0.08) } }, r.miss == null ? "—" : pct(r.miss)),
      barCell(r.ur, maxUr, r.ur == null ? "—" : `${Math.round(r.ur)}`)))));
}

function jumpsCard(j) {
  return h("div.card.skill-card",
    h("div.head", h("div", h("h3", "Jumps"), h("div.sum", "Average click distance from the circle centre (in radii: 1 = the edge) and the fastest 1/2 BPM with at most 5% misses."))),
    statTable(["Spacing", "Jumps", "From centre", "Missed", "Max BPM"], j.rows.map(r => h("tr",
      h("td", h("b", r.label), h("div.small.muted", `${r.lo}${r.hi ? `-${r.hi}` : "+"} radii`)), h("td.mono", r.jumps),
      barCell(r.distance, 1, r.distance == null ? "—" : r.distance.toFixed(2), rateColor(r.distance, 0.45, 0.7)),
      h("td.mono", { style: { color: rateColor(r.miss, 0.03, 0.08) } }, r.miss == null ? "—" : pct(r.miss)),
      h("td.big-cell.pinkv", r.max_bpm == null ? "—" : Math.round(r.max_bpm), r.played_bpm ? h("div.small.muted", `played up to ${Math.round(r.played_bpm)}`) : null)))));
}

function flowCard(f) {
  return h("div.card.skill-card",
    h("div.head", h("div", h("h3", "Flow aim"), h("div.sum", "Misses due to aim (clicked in time, off the circle) on fast notes, by spacing."))),
    statTable(["Spacing", "Notes", "Aim misses", "All misses"], f.rows.map(r => h("tr",
      h("td", h("b", r.label), h("div.small.muted", `${r.lo}-${r.hi} radii`)), h("td.mono", r.notes),
      barCell(r.aim_miss, 0.1, r.aim_miss == null ? "—" : `${pct(r.aim_miss)} (${r.aim_misses})`, rateColor(r.aim_miss, 0.01, 0.04)),
      h("td.mono.muted", r.miss == null ? "—" : pct(r.miss))))));
}

function slidersCard(sl) {
  const cell = x => h("td.mono", { style: { color: rateColor(x, 0.02, 0.08) } }, x == null ? "—" : pct(x));
  return h("div.card.skill-card",
    h("div.head", h("div", h("h3", "Sliders"), h("div.sum", "By slider speed (circle radii the ball covers per second): heads missed, and ticks or ends dropped on the sliders you hit."))),
    statTable(["Speed", "Sliders", "Head missed", "Tick dropped", "End dropped"], sl.rows.map(r => h("tr",
      h("td.mono", `${r.lo}${r.hi ? `-${r.hi}` : "+"}`), h("td.mono", r.sliders), cell(r.head_miss), cell(r.tick_miss), cell(r.end_miss)))));
}

function buildSkills(root) {
  const page = h("div.page");
  root.append(page);
  const sub = h("p");
  const btn = h("button.btn.primary", { onclick: () => recomputeProfile(btn, prog) }, icon("refresh"), "Recompute");
  const prog = h("div");
  const body = h("div");
  page.append(h("div.page-head", h("div", h("h1", "Skills"), sub), btn), prog, body);

  onProfile(p => {
    body.innerHTML = "";
    const ss = p.skillsets, levels = p.skills?.skills || [];
    sub.textContent = ss ? `From your last ${ss.plays} plays (${ss.objects.toLocaleString("en")} notes) · updated ${fmtDate(ss.updated)}`
      : "Your numbers per skillset, from your recent plays.";
    if (!ss) {
      body.append(h("div.card", emptyState("chart", "No skills yet", "Compute them from your recent plays: it takes a minute or so.",
        h("button.btn.primary.big", { onclick: e => recomputeProfile(e.currentTarget, prog) }, "Compute"))));
      return;
    }
    const level = key => levels.find(s => s.key === key);
    body.append(
      h("div.section", streamsCard(ss.streams)),
      h("div.section.skills-grid", altCard(ss.alt), fingerCard(ss.finger), jumpsCard(ss.jumps), flowCard(ss.flow), slidersCard(ss.sliders),
        ...["stamina", "high_ar", "reading", "accuracy"].map(level).filter(Boolean).map(skillCard)));
  });
}

// --- improvement ---------------------------------------------------------------------------------------------

const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
const shortDate = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });

/** One skillset's rating over the days: a line on the shared scale, the 1200 reference, a hover crosshair. */
function eloChart(dates, values, plays, lo, hi) {
  const W = 420, H = 150, L = 40, R = 10, T = 10, B = 24;
  const x = i => L + i / Math.max(1, dates.length - 1) * (W - L - R);
  const y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "elo-chart", role: "img" });
  // recessive grid: three values on the shared scale, and the reference
  for (const v of [lo, 1200, hi]) {
    root.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }));
    const t = svg("text", { x: L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }); t.textContent = v; root.append(t);
  }
  root.append(svg("line", { x1: L, x2: W - R, y1: y(1200), y2: y(1200), class: "ref" }));
  for (const i of [0, Math.floor((dates.length - 1) / 2), dates.length - 1]) {
    const t = svg("text", { x: x(i), y: H - 6, class: "axis", "text-anchor": i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle" });
    t.textContent = shortDate(dates[i]); root.append(t);
  }
  // days with plays: small ticks on the baseline
  plays.forEach((n, i) => { if (n) root.append(svg("line", { x1: x(i), x2: x(i), y1: H - B, y2: H - B - 4, class: "played" })); });
  // the line, broken where there is no rating yet
  let d = "", open = false;
  values.forEach((v, i) => {
    if (v == null) { open = false; return; }
    d += `${open ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    open = true;
  });
  root.append(svg("path", { d, class: "line" }));
  const last = values.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
  if (last) root.append(svg("circle", { cx: x(last[1]), cy: y(last[0]), r: 4, class: "end" }));
  // hover: crosshair, marker and tooltip on the nearest day
  const cross = svg("line", { y1: T, y2: H - B, class: "cross", visibility: "hidden" });
  const dot = svg("circle", { r: 4.5, class: "hover", visibility: "hidden" });
  const hit = svg("rect", { x: L, y: 0, width: W - L - R, height: H, fill: "transparent" });
  root.append(cross, dot, hit);
  const tip = h("div.elo-tip.hidden");
  hit.addEventListener("mousemove", e => {
    const box = root.getBoundingClientRect(), px = (e.clientX - box.left) / box.width * W;
    const i = Math.max(0, Math.min(dates.length - 1, Math.round((px - L) / (W - L - R) * (dates.length - 1))));
    const v = values[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    if (v != null) { dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(v)); dot.setAttribute("visibility", "visible"); }
    else dot.setAttribute("visibility", "hidden");
    put(tip, h("b", shortDate(dates[i])), h("span", v == null ? "no rating yet" : `${v}`),
      h("span.muted", plays[i] ? `${plays[i]} play${plays[i] > 1 ? "s" : ""}` : "no plays"));
    tip.classList.remove("hidden");
    tip.style.left = `${Math.min(92, Math.max(8, x(i) / W * 100))}%`;
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.classList.add("hidden"); });
  return h("div.elo-plot", root, tip);
}

function buildImprovement(root) {
  const page = h("div.page");
  root.append(page);
  const sub = h("p", "Your rating in each skillset, day by day over the last 90 days.");
  const btn = h("button.btn.primary", { onclick: () => recompute() }, icon("refresh"), "Update");
  const prog = h("div");
  const body = h("div");
  page.append(h("div.page-head", h("div", h("h1", "Improvement"), sub), btn), prog, body);

  function render(e) {
    if (!e) {
      put(body, h("div.card", emptyState("chart", "No ratings yet", "They are computed from your plays of the last 90 days (and the month before): a minute or two the first time.",
        h("button.btn.primary.big", { onclick: () => recompute() }, "Compute my ratings"))));
      return;
    }
    sub.textContent = `Your rating in each skillset at the end of every day, from ${e.plays} plays · updated ${fmtDate(e.updated)}. `
      + "The dashed line is 1200, the reference player; +400 means ten times the odds of passing the same challenge.";
    const all = Object.values(e.skills).flatMap(s => s.values).filter(v => v != null);
    const lo = Math.floor((Math.min(1150, ...all) - 20) / 50) * 50, hi = Math.ceil((Math.max(1250, ...all) + 20) / 50) * 50;
    put(body, h("div.elo-grid", Object.entries(e.skills).map(([key, s]) => {
      const change = s.change == null ? null : Math.round(s.change);
      const trend = change == null ? h("span.chip", "no data")
        : change > 0 ? h("span.chip.green", `▲ +${change} in 90 days`)
        : change < 0 ? h("span.chip.red", `▼ ${change} in 90 days`) : h("span.chip", "unchanged");
      return h("div.card.elo-card",
        h("div.head", h("div", h("h3", s.label), trend), h("div.big-num", s.current ?? "—")),
        eloChart(e.dates, s.values, e.plays_per_day, lo, hi));
    })),
      h("p.small.muted", { style: { marginTop: "14px" } },
        "Each play moves a rating by how many of its challenges you passed beyond what your rating expected (streams finished, jumps hit, "
        + "300s...). Days without plays keep the last rating; the ticks on the baseline are the days you played."));
  }

  async function recompute() {
    btn.disabled = true;
    const box = progressBox("Getting ready…");
    put(prog, box.el);
    try {
      render(await runJob("elo", {}, box));
      toast("Ratings updated.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      put(prog);
      btn.disabled = false;
    }
  }

  api("elo").then(r => render(r.elo)).catch(err => toast(err.message, true));
}

// --- replays -----------------------------------------------------------------------------------------------

function buildReplays(root) {
  const R = { rows: [], filtered: [], shown: 0, selected: null, owner: null, viewer: null };
  const layout = h("div.replay-layout");
  root.append(layout);
  const search = h("input.input", { placeholder: "Search map…", type: "search" });
  const player = h("select.input", h("option", { value: "owner" }, "My plays"), h("option", { value: "all" }, "All players"));
  const file = h("input", { type: "file", accept: ".osr", multiple: true, class: "hidden" });
  const count = h("span.small.muted", "loading…");
  const list = h("div.replay-list");
  const side = h("div.replay-side",
    h("div.tools",
      h("div.row", h("h1.grow", "Replay"),
        h("button.btn.sm", { title: "Import .osr files", onclick: () => file.click() }, icon("upload"), "Import"),
        h("button.btn.sm.ghost.icon-btn", { title: "Reload", onclick: () => load(true) }, icon("refresh"))),
      search, h("div.row", player, h("div.grow"), count), file),
    list);
  const main = h("div.replay-main");
  layout.append(side, main);

  const content = h("div.page");
  main.append(content);
  content.append(h("div.card", emptyState("replay", "Pick a replay", "Select a replay from the list (or import one) and press Analyze.")));

  search.addEventListener("input", () => filter());
  player.addEventListener("change", () => filter());
  list.addEventListener("scroll", () => { if (list.scrollTop + list.clientHeight > list.scrollHeight - 400) more(); });
  file.addEventListener("change", async () => {
    let last = null;
    for (const f of file.files) {
      try {
        const r = await fetch(`/api/import?name=${encodeURIComponent(f.name)}`, { method: "POST", body: await f.arrayBuffer() });
        const d = await r.json();
        if (d.error) throw new Error(d.error);
        last = d.id;
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    file.value = "";
    if (last) {
      await load(false);
      const row = R.rows.find(r => r.id === last);
      if (row) { select(row); analyze(row); }
    }
  });

  async function load(reload) {
    count.textContent = "loading…";
    put(list, h("div.empty", h("div.spinner", { style: { margin: "0 auto 10px" } }), "Reading your replays (the first time takes a while)…"));
    try {
      const d = await api("replays" + (reload ? "?reload=1" : ""));
      R.rows = d.replays;
      R.owner = d.owner;
      player.options[0].textContent = d.owner ? `${d.owner}'s plays` : "My plays";
      filter();
    } catch (e) {
      put(list, h("div.empty", e.message));
      count.textContent = "";
    }
  }

  function filter() {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    const own = player.value === "owner" && R.owner;
    R.filtered = R.rows.filter(r => (!own || r.player.toLowerCase() === R.owner.toLowerCase() || r.source === "i")
      && words.every(w => r.map.toLowerCase().includes(w)));
    count.textContent = `${R.filtered.length.toLocaleString("en")} replays`;
    put(list);
    R.shown = 0;
    more();
  }

  function more() {
    const next = R.filtered.slice(R.shown, R.shown + 120);
    R.shown += next.length;
    list.append(...next.map(rowEl));
  }

  function rowEl(r) {
    const ok = r.found && r.supported;
    const el = h("div.rp" + (ok ? "" : ".missing") + (R.selected?.id === r.id ? ".sel" : ""),
      { onclick: () => select(r), ondblclick: () => { select(r); if (ok) analyze(r); },
        title: !r.supported ? "Relax/Autopilot: can't be analysed" : r.found ? "" : "map not found in Songs" },
      h("div.name", r.map),
      h("div.meta",
        h("span", r.source === "i" ? "imported" : fmtDate(r.time)), modsChip(r.mods), r.stars ? starChip(r.stars) : null,
        h("span.acc", pct(r.acc, 2)), h("span", `${r.combo}x`),
        r.miss ? h("span.x", `${r.miss}✕`) : h("span.fc", "FC")));
    el.dataset.id = r.id;
    return el;
  }

  function select(r) {
    R.selected = r;
    list.querySelectorAll(".rp").forEach(e => e.classList.toggle("sel", e.dataset.id === r.id));
    if (R.viewer) { R.viewer.destroy(); R.viewer = null; }
    put(content, 
      h("div.card.play-head",
        h("div",
          h("h2", r.map),
          h("div.row", modsChip(r.mods), starChip(r.stars), h("span.muted", `${r.player} · ${new Date(r.time * 1000).toLocaleString("en-GB")}`)),
          h("div.stat-row", { style: { marginTop: "14px" } },
            h("div.stat", h("b", pct(r.acc, 2)), h("span", "accuracy")),
            h("div.stat", h("b", `${r.combo}x`), h("span", "combo")),
            h("div.stat", h("b", { style: { color: r.miss ? "var(--miss)" : "var(--ok)" } }, r.miss || "FC"), h("span", "miss")),
            h("div.stat", h("b", { style: { color: "var(--r100)" } }, r.c100), h("span", "100")),
            h("div.stat", h("b", { style: { color: "var(--r50)" } }, r.c50), h("span", "50")))),
        h("button.btn.primary.big", { disabled: !r.found || !r.supported, onclick: () => analyze(r) }, icon("play"), "Analyze replay")),
      !r.supported ? h("p.muted", "Relax and Autopilot replays can't be analysed.")
        : r.found ? null : h("p.muted", "This replay's map is not in your Songs folder: download it to analyse the replay."));
  }

  async function analyze(r) {
    if (R.viewer) { R.viewer.destroy(); R.viewer = null; }
    const box = progressBox("Analyzing the replay…");
    put(content, h("div.card", h("h2", r.map), box.el));
    try {
      const res = await runJob("analyze", { id: r.id }, box);
      if (R.selected?.id === r.id) renderAnalysis(res);
    } catch (e) {
      put(content, h("div.card", emptyState("x", "Analysis failed", e.message,
        h("button.btn", { onclick: () => analyze(r) }, "Retry"))));
    }
  }

  function renderAnalysis(a) {
    const m = a.map, s = a.stats;
    put(content);
    // header
    const head = h("div.card.play-head",
      m.set_id ? h("div.cover", { style: { backgroundImage: `url(${coverUrl(m.set_id)})` } }) : null,
      h("div",
        h("h2", m.name),
        h("div.row", modsChip(m.mods), starChip(m.stars), h("span.muted", `${m.player} · CS ${num(m.cs, 1)} · AR ${num(m.ar, 1)} · OD ${num(m.od, 1)}`)),
        h("div.stat-row", { style: { marginTop: "14px" } },
          h("div.stat", h("b", pct(s.acc, 2)), h("span", "accuracy")),
          h("div.stat", h("b", `${s.combo}x`), h("span", "max combo")),
          h("div.stat", h("div.hits", h("span.h300", s.c300), h("span.h100", s.c100), h("span.h50", s.c50), h("span.hmiss", s.miss)), h("span", "300 / 100 / 50 / miss")),
          h("div.stat", h("b", num(s.ur, 0)), h("span", "UR")),
          h("div.stat", h("b", `${s.mean_error > 0 ? "+" : ""}${num(s.mean_error, 1)}ms`), h("span", s.mean_error < 0 ? "early on average" : "late on average")))),
      h("div.row", { style: { flexDirection: "column", alignItems: "stretch" } },
        m.beatmap_id ? h("button.btn", { onclick: () => openOsu(`https://osu.ppy.sh/b/${m.beatmap_id}`) }, icon("link"), "Open on osu!") : null,
        h("button.btn.ghost", { onclick: () => layout.classList.toggle("collapsed") }, "Show/hide list")));
    content.append(head);

    // viewer
    const sections = [];
    for (const p of a.priorities) for (const e of p.episodes) sections.push({ start: e.time - 1200, end: e.time + 2200, label: `${CAT_NAMES[p.category] || p.name}: ${e.title}` });
    const vwrap = h("div", { style: { marginTop: "16px" } });
    content.append(vwrap);
    R.viewer = new ReplayViewer(vwrap, a.viewer, { sections });
    const focus = t => { R.viewer.focus(t - 1500, t + 2500); vwrap.scrollIntoView({ behavior: "smooth", block: "start" }); };

    // problems
    const probs = h("div.grid");
    content.append(h("div.section",
      h("div.section-title", h("h2", "Problems in this play"),
        h("span.count", a.habit_plays ? `compared with your bad habits over your last ${a.habit_plays} plays` : "")),
      a.priorities.length ? probs : h("div.card", h("p.muted", "Nothing to flag: clean play, and no relevant bad habits for this map."))));
    a.priorities.forEach((p, i) => probs.append(problemCard(p, i < 2, focus)));

    // every mistake, in order
    content.append(mistakesSection(a.viewer, focus));

    // setup
    content.append(h("div.section",
      h("div.section-title", h("h2", "Area and rapid trigger"), h("span.count", a.setup_desc)),
      h("div.advice-grid",
        adviceCard("Area / sensitivity", "tablet", a.setup.area, "No advice with the current cutoffs."),
        adviceCard("Keyboard / rapid trigger", "keyboard", a.setup.keys, "No key problems with the current cutoffs."))));

    // training
    content.append(h("div.section",
      h("div.section-title", h("h2", "What to train")),
      h("div.card",
        a.training.length ? a.training.map(t => h("div.train",
          h("div.ico", { html: ICON.target }),
          h("div", h("h3", `${CAT_NAMES[t.category] || t.name} → ${t.skill_name} maps${t.dt ? " with DT" : ""}`), h("div.small.muted", t.why)),
          h("button.btn", { onclick: () => trainOn(t) }, icon("search"), "Find maps")))
          : h("p.muted", "Nothing specific to train for this play."),
        a.trends?.length ? h("div", { style: { marginTop: "16px" } }, h("h3", { style: { marginBottom: "8px" } }, "Trend over your recent plays"), trendTable(a.trends)) : null)));
  }

  function mistakesSection(v, focus) {
    const PATTERN = { first: "first note", double: "double", triple: "triple", burst: "burst", stream: "stream",
      deathstream: "deathstream", alt: "alt", irregular: "irregular rhythm", jump: "jump", slider: "slider", spaced: "spaced note" };
    const WHY = { aim: "clicked in time, off the circle", timing: "clicked too early", notelock: "notelock: tapped while the previous note was pending",
      no_click: "no click", wrong_note: "clicked another note on screen (misread)" };
    const KIND = { c: "circle", s: "slider", p: "spinner" };
    const list = [];
    for (const o of v.objects) {
      const what = `#${o.i + 1} ${KIND[o.k]}` + (o.pat && o.k !== "p" ? ` · ${PATTERN[o.pat] || o.pat}` : "") + (o.dist != null && o.pat !== "first" && o.k !== "p" ? ` · ${o.dist.toFixed(1)} radii` : "");
      const err = o.ht != null ? Math.round((o.ht - o.t) / v.rate) : null;
      const errText = err == null ? "" : `${Math.abs(err)} ms ${err < 0 ? "early" : "late"}`;
      // a missed slider head breaks the combo, but the slider only counts as a miss when nothing of it was held
      if (o.k === "s" && o.hr === 0 && o.res !== 0)
        list.push({ t: o.t, type: "break", label: "Break", what, detail: `slider head missed: ${WHY[o.why] || "no click"}` });
      if (o.res === 0) list.push({ t: o.k === "c" ? o.t : o.e, type: "miss", label: "Miss", what, detail: WHY[o.why] || (o.k === "p" ? "spinner not cleared" : "") });
      else if (o.res === 100 || o.res === 50) {
        const detail = o.k === "c" ? errText : o.k === "p" ? "spinner not fully cleared"
          : o.hr === 0 ? "slider head missed" : o.sk === "end" ? "slider end dropped" : o.sk ? "part of the slider dropped" : `head ${errText}`;
        list.push({ t: o.k === "c" ? o.ht ?? o.t : o.e, type: String(o.res), label: String(o.res), what, detail });
      }
      if (o.k === "s" && o.sb != null && o.hr !== 0 && o.sk !== "end")
        list.push({ t: o.sb, type: "break", label: "Break", what, detail: `slider ${o.sk} dropped: combo lost` });
    }
    list.sort((x, y) => x.t - y.t);
    const count = type => list.filter(x => type === "all" || x.type === type).length;
    const filters = [["all", "All"], ["miss", "Misses"], ["break", "Combo breaks"], ["100", "100s"], ["50", "50s"]];
    const body = h("tbody");
    const chip = { miss: "red", break: "warn", 100: "green", 50: "warn" };
    const show = type => put(body, list.filter(x => type === "all" || x.type === type).map(x => h("tr",
      h("td", h("button.btn.sm.time-btn", { title: "Watch this moment", onclick: () => focus(x.t) }, h("span", { html: ICON.play }), fmtTime(x.t))),
      h("td", h(`span.chip.${chip[x.type]}`, x.label)),
      h("td", x.what),
      h("td.muted", x.detail))));
    const sel = seg(filters.map(([k, l]) => [k, `${l} (${count(k)})`]), "all", show);
    show("all");
    return h("div.section",
      h("div.section-title", h("h2", "All mistakes"), h("span.count", "every miss, combo break, 100 and 50 in order; click a time to watch it")),
      h("div", { style: { marginBottom: "12px" } }, sel.el),
      h("div.card.flush.mistakes", list.length ? h("table.trend-table.stat-table", h("thead", h("tr", h("th", "Time"), h("th", "Result"), h("th", "Object"), h("th", "Details"))), body)
        : h("p.muted", { style: { padding: "16px 20px", margin: 0 } }, "No mistakes: a perfect play.")));
  }

  function problemCard(p, open, focus) {
    const card = h("div.card.problem" + (open ? "" : ".closed"));
    const headEl = h("div.p-head", { onclick: () => card.classList.toggle("closed") },
      h("div",
        h("div.row", h("h3", CAT_NAMES[p.category] || p.name), p.content ? h("span.chip", p.content) : null),
        h("div.rates", { style: { marginTop: "6px" } },
          p.play_rate != null ? h("span", `${p.rate_label === "missed" ? "missed" : p.rate_label === "100s/50s" ? "100s/50s" : "broken"} here `, h("b", pct(p.play_rate))) : null,
          p.usual_rate != null ? h("span", "usually ", h("b", pct(p.usual_rate))) : null,
          p.episodes.length ? h("span", `${p.episodes.length} moments`) : null)),
      h("div.scores",
        h("div", h("b", `${Math.round(p.map_score)}%`), h("span", "of the mistakes here")),
        p.habits_score != null ? h("div", h("b", `${Math.round(p.habits_score)}%`), h("span", "of the bad habits")) : null));
    const body = h("div.p-body");
    for (const i of p.insights) {
      body.append(h("div.habit-inline",
        h("div.row", h("span.chip.pink", "bad habit"), h("b", i.title)),
        h("div.small", { style: { color: "var(--text-2)" } }, i.detail),
        i.solution ? h("div.solution", h("span", { html: ICON.bulb }), h("div", h("b", "Fix: "), i.solution)) : null));
    }
    for (const e of p.episodes) {
      body.append(h("div.episode",
        h("button.go", { title: "Watch this moment", onclick: () => focus(e.time) }, h("span", { html: ICON.play }), e.stamp),
        h("div", h("b", e.title), e.reasons.length ? h("ul", e.reasons.map(x => h("li", x))) : null)));
    }
    card.append(headEl, body);
    return card;
  }

  load(false);
}

function trainOn(t) {
  show("search");
  S.searchApi?.prefill({ include: [t.skill], mods: t.dt ? ["DT"] : null });
}

// --- beatmap search ----------------------------------------------------------------------------------------

const RANGES = [
  { key: "stars", label: "Stars", min: 0, max: 12, step: 0.1, d: 1 },
  { key: "ar", label: "AR", min: 0, max: 11, step: 0.1, d: 1 },
  { key: "cs", label: "CS", min: 0, max: 10, step: 0.1, d: 1 },
  { key: "od", label: "OD", min: 0, max: 11, step: 0.1, d: 1 },
  { key: "bpm", label: "BPM", min: 60, max: 400, step: 1, d: 0 },
  { key: "length", label: "Length", min: 0, max: 600, step: 5, len: true },
];

function dualRange(cfg, onChange) {
  const lo = h("input", { type: "range", min: cfg.min, max: cfg.max, step: cfg.step, value: cfg.min });
  const hi = h("input", { type: "range", min: cfg.min, max: cfg.max, step: cfg.step, value: cfg.max });
  const fill = h("div.fill"), val = h("span");
  const f = v => cfg.len ? fmtLen(v) : Number(v).toFixed(cfg.d);
  function sync(src) {
    let a = +lo.value, b = +hi.value;
    if (a > b) { if (src === lo) lo.value = b; else hi.value = a; a = +lo.value; b = +hi.value; }
    const span = cfg.max - cfg.min;
    fill.style.left = `${(a - cfg.min) / span * 100}%`;
    fill.style.right = `${100 - (b - cfg.min) / span * 100}%`;
    const la = a <= cfg.min, lb = b >= cfg.max;
    val.className = la && lb ? "any" : "";
    dual.classList.toggle("any", la && lb);
    val.textContent = la && lb ? "any" : la ? `up to ${f(b)}` : lb ? `from ${f(a)}` : `${f(a)} – ${f(b)}`;
    onChange?.();
  }
  lo.addEventListener("input", () => sync(lo));
  hi.addEventListener("input", () => sync(hi));
  const dual = h("div.dual", h("div.track"), fill, lo, hi);
  const el = h("div.range-field", h("div.top", h("b", cfg.label), val), dual);
  sync();
  return {
    el,
    get() {
      const a = +lo.value, b = +hi.value;
      if (a <= cfg.min && b >= cfg.max) return null;
      return [a <= cfg.min ? null : a, b >= cfg.max ? null : b];
    },
    set(r) { lo.value = r?.[0] ?? cfg.min; hi.value = r?.[1] ?? cfg.max; sync(); },
  };
}

function seg(options, value, onChange, multi = false) {
  const el = h("div.seg");
  let cur = multi ? new Set(value) : value;
  const buttons = options.map(([v, label]) => {
    const b = h("button", { onclick: () => {
      if (b.disabled) return;
      if (multi) { cur.has(v) ? cur.delete(v) : cur.add(v); if (!cur.size) cur.add(v); } else cur = v;
      paint(); onChange?.(multi ? [...cur] : cur);
    } }, label);
    b.dataset.v = v;
    return b;
  });
  const paint = () => buttons.forEach(b => b.classList.toggle("on", multi ? cur.has(b.dataset.v) : cur === b.dataset.v));
  el.append(...buttons);
  paint();
  return {
    el, buttons,
    get: () => multi ? [...cur] : cur,
    set(v) { cur = multi ? new Set(v) : v; paint(); },
  };
}

// where to search and which statuses: lists now (older settings kept one value)
const toSources = v => Array.isArray(v) ? v : [v || "online"];
const toStatuses = v => Array.isArray(v) ? v : v === "loved" ? ["ranked", "loved"] : v === "any" ? ["ranked", "loved", "other"] : ["ranked"];
const SOURCE_OPTIONS = [["online", "On the osu! site (maps you don't have)"], ["songs", "In my Songs folder"]];
const STATUS_OPTIONS = [["ranked", "Ranked"], ["loved", "Loved"], ["other", "Other (Songs only)"]];

function buildSearch(root) {
  const cfg = S.state.settings.search;
  const page = h("div.page");
  root.append(page);
  page.append(h("div.page-head", h("div", h("h1", "Beatmap search"),
    h("p", "Pick the skillsets to include (click) or exclude (second click) and adjust the filters."))));

  // skill toggles: neutral -> include -> exclude
  const skillState = {};
  const toggles = S.state.skills.map(sk => {
    const t = h("div.skill-toggle", h("span.mark"), S.state.skill_names[sk] || sk);
    skillState[sk] = 0;
    const paint = () => {
      t.classList.toggle("inc", skillState[sk] === 1);
      t.classList.toggle("exc", skillState[sk] === 2);
      t.querySelector(".mark").textContent = skillState[sk] === 1 ? "✓" : skillState[sk] === 2 ? "✕" : "";
      t.title = ["neutral", "included", "excluded"][skillState[sk]];
    };
    t.addEventListener("click", () => { skillState[sk] = (skillState[sk] + 1) % 3; paint(); paintShares(); });
    t.addEventListener("contextmenu", e => { e.preventDefault(); skillState[sk] = 0; paint(); paintShares(); });
    t.paint = paint;
    paint();
    return t;
  });

  // a share slider for each included (at least) or excluded (at most) skillset that has a share of the notes
  const SHARED = ["jump", "stream", "alt", "finger control", "tech"];
  const shares = { min: {}, max: {} };
  const shareBox = h("div.share-sliders");
  function paintShares() {
    put(shareBox, SHARED.filter(sk => skillState[sk]).map(sk => {
      const inc = skillState[sk] === 1, bag = inc ? shares.min : shares.max;
      const any = inc ? 0 : 100;
      const input = h("input.slider-single", { type: "range", min: 0, max: 100, step: 5, value: bag[sk] ?? any });
      const val = h("span.val");
      const show = () => {
        const v = +input.value;
        bag[sk] = v;
        val.textContent = v === any ? "any" : `${inc ? "at least" : "at most"} ${v}%`;
        val.classList.toggle("any", v === any);
      };
      input.addEventListener("input", show);
      show();
      return h("div.share-slider", h("span", h("b", S.state.skill_names[sk] || sk), h("span.muted", inc ? " included" : " excluded")), input, val);
    }));
  }
  paintShares();

  const source = seg(SOURCE_OPTIONS, toSources(cfg.source), () => syncSource(), true);
  const mods = seg([["NM", "NM"], ["DT", "DT"], ["HR", "HR"], ["HD", "HD"], ["EZ", "EZ"], ["HT", "HT"]], cfg.mods, null, true);
  const status = seg(STATUS_OPTIONS, toStatuses(cfg.status), null, true);
  const unplayed = h("label.check", h("input", { type: "checkbox", checked: cfg.unplayed }), "Only maps never played");
  const limit = h("input.input.num", { type: "number", min: 1, max: 1000, value: cfg.limit });
  const ranges = RANGES.map(r => dualRange(r));
  const goBtn = h("button.btn.primary.big", { onclick: () => go() }, icon("search"), "Search");
  const depth = h("span.small.muted");

  let recOn = false;   // "Recommended for me" is ticked
  function syncSource() {
    const online = source.get().includes("online"), songs = source.get().includes("songs");
    // the site can only be searched with NM and DT: other mods only with the Songs folder
    mods.buttons.forEach(b => {
      b.disabled = !songs && !["NM", "DT"].includes(b.dataset.v);
      b.style.opacity = b.disabled ? .35 : "";
    });
    if (!songs) {
      const siteMods = mods.get().filter(m => m === "NM" || m === "DT");
      mods.set(siteMods.length ? siteMods : ["NM"]);
    }
    unplayed.classList.toggle("hidden", !songs || recOn);
    status.buttons[2].classList.toggle("hidden", !songs);
    if (!songs && status.get().includes("other")) status.set(status.get().filter(x => x !== "other").length ? status.get().filter(x => x !== "other") : ["ranked"]);
    depth.textContent = online ? `site depth: up to ${S.state.settings.search.max_pages} pages of 50 sets (Settings)` : "";
  }
  syncSource();

  const text = h("input.input.name-search", { type: "search", placeholder: "Artist, title, difficulty or mapper (optional)" });
  text.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  // recommended: maps made to train the included skillsets, a step above your level (the filters don't apply)
  const recommended = h("input", { type: "checkbox" });
  const recHint = h("div.small.muted.hidden", "Maps you've never played that train the included skillsets (none included: the 3 that cost "
    + "you most), a step above your level, from easiest to hardest. Name, sliders and shares don't apply; exclusions are ignored.");
  const limitLabel = h("span.muted", "Maximum number of maps");
  const filterParts = [];
  function syncRecommended() {
    const on = recOn = recommended.checked;
    filterParts.forEach(el => el.classList.toggle("hidden", on));
    recHint.classList.toggle("hidden", !on);
    limitLabel.textContent = on ? "Maps per skillset" : "Maximum number of maps";
    if (on && +limit.value > 30) limit.value = 15;
    if (!on) syncSource();   // "only maps never played" follows the sources again
  }
  recommended.addEventListener("change", syncRecommended);
  const nameField = h("label.field", "Name", text);
  const rangeGrid = h("div.range-grid", ranges.map(r => r.el));
  filterParts.push(nameField, shareBox, rangeGrid, unplayed);
  const panel = h("div.card.search-panel",
    h("div.rec-row", h("label.check.rec-check", recommended, h("b", "Recommended for me")), recHint),
    nameField,
    h("div", h("h3", { style: { marginBottom: "10px" } }, "Skillset"), h("div.skill-toggles", toggles),
      h("div.small.muted", { style: { marginTop: "8px" } }, "✓ the map must have it · ✕ the map must not have it · right click: neutral"),
      shareBox),
    h("div.panel-row",
      h("div", h("div.small.muted", { style: { marginBottom: "6px" } }, "Where to search"), source.el),
      h("div", h("div.small.muted", { style: { marginBottom: "6px" } }, "Mod"), mods.el),
      h("div", h("div.small.muted", { style: { marginBottom: "6px" } }, "Status"), status.el),
      unplayed),
    rangeGrid,
    h("div.row", h("label.row", limitLabel, limit), depth, h("div.grow"),
      h("button.btn.ghost", { onclick: () => reset() }, "Reset"), goBtn));
  const prog = h("div", { style: { marginTop: "16px" } });
  const results = h("div.section");
  page.append(panel, prog, results);

  function reset() {
    Object.keys(skillState).forEach(k => skillState[k] = 0);
    toggles.forEach(t => t.paint());
    text.value = "";
    shares.min = {}; shares.max = {};
    paintShares();
    ranges.forEach(r => r.set(null));
    mods.set(cfg.mods); status.set(toStatuses(cfg.status)); source.set(toSources(cfg.source)); syncSource();
  }

  S.searchApi = {
    prefill({ include, mods: m }) {
      Object.keys(skillState).forEach(k => skillState[k] = include?.includes(k) ? 1 : 0);
      toggles.forEach(t => t.paint());
      paintShares();
      if (m) mods.set(m);
      syncSource();
      page.parentElement.scrollTop = 0;
      toast("Filters set: adjust stars and values, then press Search.");
    },
  };

  let running = false;
  async function go() {
    if (running) return;
    running = true; goBtn.disabled = true;
    const q = {
      include: Object.keys(skillState).filter(k => skillState[k] === 1),
      exclude: Object.keys(skillState).filter(k => skillState[k] === 2),
      min_share: Object.fromEntries(Object.entries(shares.min).filter(([k, v]) => skillState[k] === 1 && v > 0)),
      max_share: Object.fromEntries(Object.entries(shares.max).filter(([k, v]) => skillState[k] === 2 && v < 100)),
      source: source.get(), mods: mods.get(), status: status.get(),
      unplayed: unplayed.querySelector("input").checked, limit: +limit.value || 30, text: text.value.trim(),
      recommended: recommended.checked,
    };
    RANGES.forEach((r, i) => { q[r.key] = ranges[i].get(); });
    const box = progressBox("Searching…");
    put(prog, box.el);
    put(results);
    shown = { keys: [], grid: null, title: null, count: null };
    try {
      const res = await runJob("search", q, box, partial => renderResults({ maps: partial, total: partial.length, note: "" }, true));
      renderResults(res, false);
    } catch (e) {
      toast(e.message, true);
    } finally {
      put(prog);
      running = false; goBtn.disabled = false;
    }
  }

  const downloaded = new Set();
  let last = { maps: [], note: "" };
  const cardKey = m => `${m.beatmap_id}:${m.mods}:${m.name}`;
  let shown = { keys: [], grid: null, title: null, count: null };

  function renderResults(res, partial, force = false) {
    if (!partial) last = res;
    const maps = res.maps, keys = maps.map(cardKey);
    // while the search runs, the maps found so far keep their cards (redrawing them under the mouse would flicker)
    if (!force && shown.grid && shown.keys.length <= keys.length && shown.keys.every((k, i) => k === keys[i])
        && (partial || shown.keys.length === keys.length)) {
      shown.grid.append(...maps.slice(shown.keys.length).map(mapCard));
      shown.keys = keys;
      shown.title.textContent = `${maps.length} maps`;
      shown.count.textContent = partial ? "searching…" : res.note || "";
      if (partial) return;
    }
    const toDownload = [...new Map(maps.filter(m => !m.local && m.set_id && !downloaded.has(m.set_id)).map(m => [m.set_id, m])).values()];
    const allBtn = h("button.btn.primary", { disabled: partial || !toDownload.length, onclick: () => download(toDownload, allBtn) },
      icon("download"), `Download all (${toDownload.length})`);
    let grid = maps.length ? h("div.results", maps.map(mapCard)) : null;
    if (res.recommended && maps.length) {   // one ladder per skillset, easiest first
      const groups = [...new Set(maps.map(m => m.group))];
      grid = h("div", groups.map(g => h("div.rec-group", h("h3", g), h("div.results", maps.filter(m => m.group === g).map(mapCard)))));
      force = true;
    }
    const title = h("h2", `${maps.length} maps`), count = h("span.count", partial ? "searching…" : res.note || "");
    shown = { keys, grid, title, count };
    put(results,
      h("div.section-title", title, count, h("div.grow"),
        maps.some(m => m.local) ? h("button.btn.ghost", { onclick: () => api("open", { url: "songs" }) }, icon("folder"), "Open Songs") : null,
        toDownload.length ? allBtn : null),
      grid || (partial ? null : h("div.card", emptyState("search", "No maps found", "Widen the filters or drop a skillset."))));
  }

  function mapCard(m) {
    const dl = m.local ? h("span.chip.green", "in Songs")
      : downloaded.has(m.set_id) ? h("span.chip.green", "downloaded ✓")
      : h("button.btn.sm", { onclick: e => { e.stopPropagation(); download([m], e.currentTarget); } }, icon("download"), "Download");
    return h("div.card.map-card", { title: "Open the map's page", onclick: () => m.beatmap_id && openOsu(`https://osu.ppy.sh/b/${m.beatmap_id}`) },
      h("div.cover", { style: m.set_id ? { backgroundImage: `url(https://assets.ppy.sh/beatmaps/${m.set_id}/covers/card.jpg)` } : {} },
        h("div.badges", m.step ? h("span.chip.step-chip", `#${m.step}`) : null, starChip(m.stars), modsChip(m.mods), h("div.grow"), dl)),
      h("div.body",
        h("div", h("div.title", m.title), h("div.sub", `${m.artist} · [${m.version}] by ${m.creator}`)),
        h("div.vals",
          h("span", h("b", "BPM "), num(m.bpm)), h("span", h("b", "AR "), num(m.ar, 1)), h("span", h("b", "CS "), num(m.cs, 1)),
          h("span", h("b", "OD "), num(m.od, 1)), h("span", h("b", "⏱ "), fmtLen(m.length))),
        h("div.tags", m.tags.map(t => h("span.chip.pink", S.state.skill_names[t] || t))),
        h("div.label", m.label),
        m.why ? h("div.why", m.why) : null));
  }

  async function download(list, btn) {
    const box = progressBox(`Downloading ${list.length} maps…`);
    put(prog, box.el);
    if (btn) btn.disabled = true;
    try {
      const res = await runJob("download", { sets: list.map(m => ({ set_id: m.set_id, artist: m.artist, title: m.title })) }, box);
      res.done.forEach(id => downloaded.add(id));
      if (res.failed.length) toast(`${res.failed.length} not downloaded: ${res.failed.map(f => f.error).join("; ")}`, true);
      if (res.done.length) toast(`${res.done.length} maps saved to Songs: press F5 in osu!'s song select to import them.`);
      renderResults(last, false, true);
    } catch (e) {
      toast(e.message, true);
      if (btn) btn.disabled = false;
    } finally {
      put(prog);
    }
  }
}

// --- setup wizard ------------------------------------------------------------------------------------------

/** First run (and Settings > "Run the setup wizard"): osu! folder, osu! API, tablet or mouse, keyboard. */
function buildSetup(root) {
  const page = h("div.page.wizard");
  root.append(page);
  const W = { step: 0, detected: null, osuDir: "", device: "", keyboard: "" };
  const steps = ["osu! folder", "osu! API", "Tablet or mouse", "Keyboard", "Done"];
  const stepper = h("div.stepper");
  const body = h("div.card.wizard-body");
  const back = h("button.btn.ghost", { onclick: () => go(W.step - 1) }, "Back");
  const next = h("button.btn.primary.big", { onclick: () => advance() }, "Next");
  const skip = h("button.btn.ghost", { onclick: () => go(W.step + 1) }, "Skip");
  page.append(h("div.page-head", h("div", h("h1", "Set up osu!coach"), h("p", "A few steps; everything can be changed later in Settings."))),
    stepper, body, h("div.row.wizard-nav", back, h("div.grow"), skip, next));

  const field = (label, input, hint) => h("label.field", label, input, hint ? h("span.hint", hint) : null);
  const num = (v, ph) => h("input.input", { type: "number", step: "any", value: v ?? "", placeholder: ph });
  const note = (ok, text) => h("div.wizard-note" + (ok === true ? ".ok" : ok === false ? ".bad" : ""), text);

  // step 1: the osu! folder
  const dirInput = h("input.input.grow", { placeholder: "e.g. C:\\Users\\you\\AppData\\Local\\osu!" });
  const dirResult = h("div");
  async function checkDir() {
    const r = await api("check-osu", { path: dirInput.value.trim() });
    W.osuOk = r.ok;
    if (r.ok) dirInput.value = r.path;
    put(dirResult, r.ok ? note(true, `osu! found: ${r.replays.toLocaleString("en")} replays, ${r.songs.toLocaleString("en")} beatmap sets.`)
      : note(false, r.message));
  }
  const stepFolder = () => [
    h("h2", "Where is osu! installed?"),
    h("p.muted", "osu!coach reads your replays (Data\\r), your beatmaps (Songs) and osu!.db from osu! stable's folder. It never changes them."),
    h("div.row", dirInput, h("button.btn", { onclick: async () => { const r = await api("pick-folder", {}); if (r.path) { dirInput.value = r.path; checkDir(); } } }, "Browse…"),
      h("button.btn", { onclick: checkDir }, "Check")),
    dirResult];

  // step 2: the osu! API
  const clientId = h("input.input", { placeholder: "e.g. 12345" });
  const secret = h("input.input", { type: "password", placeholder: "client secret" });
  const apiResult = h("div");
  const stepApi = () => [
    h("h2", "Connect to the osu! API"),
    h("p.muted", "Needed to search beatmaps on the osu! site (maps you don't have). The Songs search, the profile and the replay analysis work without it."),
    h("ol.wizard-list",
      h("li", "Open your ", h("a", { href: "#", onclick: e => { e.preventDefault(); openOsu("https://osu.ppy.sh/home/account/edit#oauth"); } }, "osu! account settings, OAuth section"), "."),
      h("li", "Click ", h("b", "New OAuth Application"), ": any name (e.g. osu-coach), callback URL ", h("code", "http://localhost"), "."),
      h("li", "Copy the ", h("b", "Client ID"), " and the ", h("b", "Client Secret"), " here. They are saved on this PC only.")),
    h("div.two", field("Client ID", clientId), field("Client secret", secret, W.detected?.api.has_secret ? "a secret is already saved: leave empty to keep it" : "")),
    h("div.row", h("button.btn", { onclick: saveApi }, "Save and test")), apiResult];
  async function saveApi() {
    put(apiResult, note(null, "Testing…"));
    try {
      await api("api", { client_id: clientId.value, client_secret: secret.value });
      const t = await api("api/test", {});
      W.apiOk = t.ok;
      put(apiResult, note(t.ok, t.ok ? `Connected. ${t.message}` : `Not working: ${t.message}`));
    } catch (e) { put(apiResult, note(false, e.message)); }
  }

  // step 3: tablet or mouse
  const areaW = num(null, "width mm"), areaH = num(null, "height mm"), sens = num(null, "e.g. 1.0"), dpi = num(null, "e.g. 800");
  const deviceSeg = seg([["tablet", "Tablet"], ["mouse", "Mouse"]], "tablet", v => { W.device = v; paintDevice(); });
  const deviceBox = h("div");
  function paintDevice() {
    const otd = W.detected?.otd;
    if (W.device === "mouse") {
      put(deviceBox, h("p.muted", "Your sensitivity lets the advice give exact numbers when you over- or underaim."),
        h("div.two", field("In-game sensitivity", sens), field("DPI", dpi)));
    } else if (otd?.found) {
      put(deviceBox, note(true, `OpenTabletDriver found (${otd.tablet.replace(/^OpenTabletDriver \(|\)$/g, "")}): area ${otd.area[0]}×${otd.area[1]} mm, read automatically every time.`),
        h("p.muted", "Nothing to enter: change the area in OpenTabletDriver and osu!coach follows it."));
    } else {
      put(deviceBox,
        note(null, "OpenTabletDriver wasn't found. osu!coach reads the tablet area from it; with another driver, enter the area yourself."),
        h("div.row", h("button.btn", { onclick: () => openOsu("https://opentabletdriver.net/") }, icon("link"), "Get OpenTabletDriver"),
          h("span.muted.small", "recommended for osu!: low latency, precise area; set your area there, then reopen this step")),
        h("p.muted", "Or, with Wacom / Huion / XP-Pen drivers: your area in millimetres, from the driver's mapping settings."),
        h("div.two", field("Area width (mm)", areaW), field("Area height (mm)", areaH)));
    }
  }
  const stepDevice = () => [h("h2", "Tablet or mouse?"), h("div.row", deviceSeg.el), deviceBox];

  // step 4: keyboard
  const act = num(null, "e.g. 2.0"), rtP = num(null, "mm"), rtR = num(null, "mm");
  const keyBox = h("div");
  const keySeg = seg([["rt", "Rapid trigger (Hall effect / analog)"], ["mechanical", "Mechanical"], ["", "Don't know"]], "", v => { W.keyboard = v; paintKeys(); });
  function paintKeys() {
    put(keyBox, W.keyboard === "rt" ? h("div.two", field("Actuation point (mm)", act), h("div"), field("Rapid trigger: press (mm)", rtP), field("Rapid trigger: release (mm)", rtR))
      : W.keyboard === "mechanical" ? h("div.two", field("Actuation point (mm)", act, "usually 2.0 mm; 1.2 mm on speed switches"), h("div"))
      : h("p.muted", "The key advice will cover both kinds of keyboard."));
  }
  const stepKeys = () => [h("h2", "Your keyboard"), h("p.muted", "For the ghost-tap and missed-tap advice (which distance to raise or lower)."), h("div.row", keySeg.el), keyBox];

  // step 5: done
  const stepDone = () => [
    h("h2", "All set"),
    h("p", "osu!coach now reads your recent plays to build your profile: skill levels, bad habits and setup advice. It takes a minute or two the first time."),
    h("p.muted", "Everything you entered can be changed in Settings; this wizard is there too.")];

  const STEPS = [stepFolder, stepApi, stepDevice, stepKeys, stepDone];

  function go(n) {
    W.step = Math.max(0, Math.min(STEPS.length - 1, n));
    put(stepper, steps.map((name, i) => h("div.step" + (i === W.step ? ".on" : i < W.step ? ".done" : ""), h("span", i < W.step ? "✓" : i + 1), name)));
    put(body, STEPS[W.step]());
    back.disabled = W.step === 0;
    skip.classList.toggle("hidden", W.step === 0 || W.step === STEPS.length - 1);
    next.textContent = W.step === STEPS.length - 1 ? "Finish and build my profile" : "Next";
  }

  async function advance() {
    try {
      if (W.step === 0) {
        await checkDir();
        if (!W.osuOk) return toast("Pick your osu! folder first.", true);
      }
      if (W.step === 1 && (clientId.value.trim() || secret.value.trim()) && !W.apiOk) await saveApi();
      if (W.step === 2 || W.step === 3) {
        await api("setup", {
          device: W.device || "tablet", keyboard: W.keyboard,
          ...(W.device === "mouse" ? { sens: sens.value, dpi: dpi.value } : {}),
          ...(W.device !== "mouse" && !W.detected?.otd.found ? { area_w: areaW.value, area_h: areaH.value } : {}),
          ...(W.step === 3 ? { actuation: act.value, rt_press: rtP.value, rt_release: rtR.value } : {}),
        });
      }
      if (W.step === STEPS.length - 1) return finish();
      go(W.step + 1);
    } catch (e) { toast(e.message, true); }
  }

  async function finish() {
    const r = await api("settings", { ...S.state.settings, osu_dir: dirInput.value.trim() });
    S.state.settings = r.settings;
    S.state.first_run = false;
    for (const name of Object.keys(S.pages)) if (name !== "setup") { S.pages[name].remove(); delete S.pages[name]; }
    show("profile");
    S.startProfile?.();
  }

  api("detect").then(d => {
    W.detected = d;
    dirInput.value = d.osu.path || "";
    if (d.osu.ok) { W.osuOk = true; put(dirResult, note(true, `osu! found: ${d.osu.replays.toLocaleString("en")} replays, ${d.osu.songs.toLocaleString("en")} beatmap sets.`)); }
    else put(dirResult, note(false, d.osu.path ? d.osu.message : "osu! wasn't found by itself: pick its folder."));
    clientId.value = d.api.client_id || "";
    const su = d.setup;
    W.device = su.device === "mouse" ? "mouse" : "tablet";
    deviceSeg.set(W.device);
    W.keyboard = su.keyboard || "";
    keySeg.set(W.keyboard);
    areaW.value = su.area_w ?? ""; areaH.value = su.area_h ?? ""; sens.value = su.sens ?? ""; dpi.value = su.dpi ?? "";
    act.value = su.actuation ?? ""; rtP.value = su.rt_press ?? ""; rtR.value = su.rt_release ?? "";
    paintDevice(); paintKeys(); go(W.step);
  }).catch(e => toast(e.message, true));
  go(0);
}

// --- settings ----------------------------------------------------------------------------------------------

function sensSlider(label, hint, value, min, max, step, unit, onInput) {
  const val = h("span.val");
  const input = h("input.slider-single", { type: "range", min, max, step, value });
  const paint = () => { val.textContent = `${Number(input.value)}${unit}`; onInput?.(+input.value); };
  input.addEventListener("input", paint);
  paint();
  return { el: h("div.sens", h("label.field", label, input), val, h("div.hint", hint)), get: () => +input.value };
}

function buildSettings(root) {
  const st = S.state;
  const cfg = structuredClone(st.settings);
  const page = h("div.page");
  root.append(page);
  page.append(h("div.page-head", h("div", h("h1", "Settings"), h("p", "osu! API, search filters and advice sensitivity.")),
    h("button.btn", { onclick: () => { if (S.pages.setup) { S.pages.setup.remove(); delete S.pages.setup; } show("setup"); } }, "Run the setup wizard")));
  const wrap = h("div.settings");
  page.append(wrap);

  // osu! folder
  const osuDir = h("input.input.grow", { value: cfg.osu_dir, placeholder: st.osu_dir || "found automatically" });
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.folder }), h("h3", "osu! folder")),
    h("div.row", osuDir, h("button.btn", { onclick: async () => { const r = await api("pick-folder", {}); if (r.path) osuDir.value = r.path; } }, "Browse…")),
    h("div.small.muted", `Empty = found automatically${st.osu_dir ? ` (${st.osu_dir})` : ""}. Replays are read from Data\\r.`)));

  // API
  const apiDot = h("span.status-dot" + (st.api.client_id && st.api.has_secret ? ".ok" : ".no"));
  const apiState = h("span.small", st.api.client_id && st.api.has_secret ? `configured (client ${st.api.client_id})` : "not configured");
  const clientId = h("input.input", { value: st.api.client_id, placeholder: "e.g. 12345" });
  const secret = h("input.input", { type: "password", placeholder: st.api.has_secret ? "•••••••• saved (leave empty to keep it)" : "client secret" });
  const apiMsg = h("span.small.muted");
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.link }), h("h3.grow", "osu! API"), apiDot, apiState),
    h("div.small.muted", "Needed to search maps on the site. Create an OAuth application in your osu! account settings (callback: http://localhost) and copy its ID and secret here. ",
      h("a", { href: "#", onclick: e => { e.preventDefault(); openOsu("https://osu.ppy.sh/home/account/edit#oauth"); } }, "Open the OAuth settings")),
    h("div.two", h("label.field", "Client ID", clientId), h("label.field", "Client secret", secret)),
    h("div.row",
      h("button.btn.primary", { onclick: async () => {
        try {
          const r = await api("api", { client_id: clientId.value, client_secret: secret.value });
          st.api = r; secret.value = ""; secret.placeholder = r.has_secret ? "•••••••• saved" : "client secret";
          const ok = r.client_id && r.has_secret;
          apiDot.className = "status-dot " + (ok ? "ok" : "no"); apiState.textContent = ok ? `configured (client ${r.client_id})` : "not configured";
          toast("Credentials saved.");
        } catch (e) { toast(e.message, true); }
      } }, "Save"),
      h("button.btn", { onclick: async () => { apiMsg.textContent = "testing…"; const r = await api("api/test", {}); apiMsg.textContent = r.message; apiMsg.style.color = r.ok ? "var(--ok)" : "var(--miss)"; } }, "Test connection"),
      apiMsg)));

  // hardware setup
  const su = st.setup;
  const device = seg([["tablet", "Tablet"], ["mouse", "Mouse"], ["", "Don't know"]], su.device || "", () => paintSetup());
  const keyboard = seg([["rt", "Rapid trigger"], ["mechanical", "Mechanical"], ["", "Don't know"]], su.keyboard || "", () => paintSetup());
  const inp = (v, ph) => h("input.input", { type: "number", step: "any", value: v ?? "", placeholder: ph });
  const areaW = inp(su.area_w, "width mm"), areaH = inp(su.area_h, "height mm"), sens = inp(su.sens, "e.g. 1.0"), dpi = inp(su.dpi, "e.g. 800");
  const rtP = inp(su.rt_press, "mm"), rtR = inp(su.rt_release, "mm"), act = inp(su.actuation, "e.g. 2.0");
  const tabletRow = h("div.two", h("label.field", "Area: width (mm)", areaW), h("label.field", "Area: height (mm)", areaH));
  const mouseRow = h("div.two", h("label.field", "In-game sensitivity", sens), h("label.field", "DPI", dpi));
  const actRow = h("div.two", h("label.field", h("span", "Actuation point (mm) ", h("span.hint", "(how deep a press registers)")), act), h("div"));
  const rtRow = h("div.two", h("label.field", "Rapid trigger: press (mm)", rtP), h("label.field", "Rapid trigger: release (mm)", rtR));
  const setupDesc = h("div.small.muted", su.describe);
  function paintSetup() {
    tabletRow.classList.toggle("hidden", device.get() !== "tablet");
    mouseRow.classList.toggle("hidden", device.get() !== "mouse");
    rtRow.classList.toggle("hidden", keyboard.get() !== "rt");
    actRow.classList.toggle("hidden", !keyboard.get());
  }
  paintSetup();
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.tablet }), h("h3", "Your setup")),
    h("div.small.muted", "Used to give exact numbers in the area and rapid trigger advice. The tablet area is read from OpenTabletDriver unless you enter it."),
    h("div.row", h("span.muted", "Device"), device.el), tabletRow, mouseRow,
    h("div.row", h("span.muted", "Keyboard"), keyboard.el), actRow, rtRow, setupDesc,
    h("div.row", h("button.btn.primary", { onclick: async () => {
      try {
        const r = await api("setup", { device: device.get(), keyboard: keyboard.get(), area_w: areaW.value, area_h: areaH.value,
          sens: sens.value, dpi: dpi.value, rt_press: rtP.value, rt_release: rtR.value, actuation: act.value });
        setupDesc.textContent = r.describe; toast("Setup saved.");
        reloadProfile();
      } catch (e) { toast(e.message, true); }
    } }, "Save setup"))));

  // search defaults
  const sc = cfg.search;
  const sSource = seg([["online", "On the osu! site"], ["songs", "Songs folder"]], toSources(sc.source), null, true);
  const sMods = seg([["NM", "NM"], ["DT", "DT"], ["HR", "HR"], ["HD", "HD"], ["EZ", "EZ"], ["HT", "HT"]], sc.mods, null, true);
  const sStatus = seg(STATUS_OPTIONS, toStatuses(sc.status), null, true);
  const sUnplayed = h("input", { type: "checkbox", checked: sc.unplayed });
  const sLimit = h("input.input.num", { type: "number", min: 1, value: sc.limit });
  const sPages = h("input.input.num", { type: "number", min: 1, max: 1000, value: sc.max_pages });
  const sMirror = h("input.input", { value: sc.mirror });
  const sTag = sensSlider("Tag a skillset from", "A map is tagged (and found by the skillset filters) with every skillset that has at least this share of its intense notes; its main type always. Applied to the next search.",
    sc.tag_min_pct, 0, 60, 1, "%");
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.search }), h("h3", "Beatmap search")),
    h("div.small.muted", "Starting values of the search page (applied the next time the window opens)."),
    h("div.row", h("span.muted", "Where to search"), sSource.el),
    h("div.row", h("span.muted", "Mod"), sMods.el),
    h("div.row", h("span.muted", "Status"), sStatus.el, h("label.check", sUnplayed, "Only maps never played (Songs)")),
    h("div.two",
      h("label.field", "Number of maps shown", sLimit),
      h("label.field", h("span", "Search depth on the site ", h("span.hint", "(pages of 50 sets, one a second)")), sPages)),
    h("label.field", h("span", "Download mirror ", h("span.hint", "({set_id} = the beatmapset's number)")), sMirror),
    sTag.el));

  // advice sensitivity
  const a = cfg.advice;
  const sl = {
    area_scale_pct: sensSlider("Minimum error to change the area", "Overaim/underaim below this share of the jump distance gives no advice. Lower = more sensitive.", a.area_scale_pct, 0, 10, 0.5, "%"),
    area_rotation_deg: sensSlider("Minimum rotation", "Below this angle, rotating the area isn't advised.", a.area_rotation_deg, 0.5, 6, 0.5, "°"),
    area_offset_radii: sensSlider("Minimum click shift", "How far (in circle radii) your clicks must be shifted on average to flag it.", a.area_offset_radii, 0.05, 0.6, 0.05, " r"),
    rt_ghosts_per_1000: sensSlider("Ghost presses per 1000 presses", "Double presses within 10 ms above which a higher rapid trigger distance is advised.", a.rt_ghosts_per_1000, 0.2, 5, 0.1, ""),
    rt_stuck_pct: sensSlider("Keys that don't reset (% of stream misses)", "Above this share a lower rapid trigger release distance is advised.", a.rt_stuck_pct, 3, 30, 1, "%"),
  };
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.target }), h("h3", "Advice sensitivity")),
    h("h3.small.muted", "Area / sensitivity"), sl.area_scale_pct.el, sl.area_rotation_deg.el, sl.area_offset_radii.el,
    h("h3.small.muted", "Rapid trigger"), sl.rt_ghosts_per_1000.el, sl.rt_stuck_pct.el));

  // skills
  const urTol = sensSlider("Stream UR tolerance", "Minimum stream BPM: the slowest speed whose UR is within this of the UR at the comfort speed. Applied at the next recompute.",
    cfg.skills.stream_ur_tolerance_pct, 0, 50, 1, "%");
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.chart }), h("h3", "Skills")), urTol.el));

  // profile
  const habitPlays = h("input.input.num", { type: "number", min: 10, max: 1000, value: cfg.habit_plays });
  const skillPlays = h("input.input.num", { type: "number", min: 20, max: 2000, value: cfg.skill_plays });
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.user }), h("h3", "Profile and analysis")),
    h("div.two",
      h("label.field", h("span", "Recent plays for bad habits ", h("span.hint", "(also used when analysing replays)")), habitPlays),
      h("label.field", h("span", "Recent plays for levels ", h("span.hint", "(+ as many below AR 9 and above AR 10)")), skillPlays))));

  wrap.append(h("div.row", { style: { position: "sticky", bottom: "0", padding: "14px 0", background: "linear-gradient(0deg, var(--bg) 60%, transparent)" } },
    h("div.grow"),
    h("button.btn.primary.big", { onclick: async () => {
      const data = {
        osu_dir: osuDir.value.trim(), habit_plays: +habitPlays.value || 50, skill_plays: +skillPlays.value || 100,
        search: { source: sSource.get(), mods: sMods.get(), status: sStatus.get(), unplayed: sUnplayed.checked,
          limit: +sLimit.value || 30, max_pages: +sPages.value || 100, mirror: sMirror.value.trim(), tag_min_pct: sTag.get() },
        advice: Object.fromEntries(Object.entries(sl).map(([k, v]) => [k, v.get()])),
        skills: { stream_ur_tolerance_pct: urTol.get() },
      };
      try {
        const r = await api("settings", data);
        const dirChanged = r.settings.osu_dir !== S.state.settings.osu_dir;
        S.state.settings = r.settings;
        toast("Settings saved.");
        reloadProfile();
        if (dirChanged && S.pages.replays) { S.pages.replays.remove(); delete S.pages.replays; }
      } catch (e) { toast(e.message, true); }
    } }, "Save settings")));
}

boot();
