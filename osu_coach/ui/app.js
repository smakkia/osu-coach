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
  let jobId = null, stop;
  const stopped = new Promise(resolve => { stop = resolve; });
  cancel.addEventListener("click", () => {
    if (jobId) api(`job/${jobId}/cancel`, {}).catch(() => {});
    cancel.disabled = true;
    lbl.textContent = "Stopping…";
    stop();
  });
  return {
    el, stopped,
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

/** Run a job and follow it. Stop (the box's button) ends the wait at once, without waiting for the server to
    notice: the error then has .stopped and the last partial result. */
async function runJob(route, body, box, onPartial) {
  const job = await api(route, body);
  box?.attach(job.id);
  let partial = null, stopped = false;
  const never = new Promise(() => {});
  box?.stopped?.then(() => { stopped = true; });
  const halt = () => Object.assign(new Error("Stopped."), { stopped: true, partial });
  for (;;) {
    await Promise.race([sleep(350), box?.stopped || never]);
    if (stopped) throw halt();
    const j = await api("job/" + job.id);
    if (stopped) throw halt();
    box?.update(j);
    if (j.partial) partial = j.partial;
    if (onPartial && j.partial) onPartial(j.partial);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
    if (j.status === "cancelled") throw halt();
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
  checkUpdate();
}

/** A newer release on GitHub: a card in the sidebar. The installed app updates itself (the installer runs silently,
    closes the window and starts the new version); a copy run with Python opens the release page. */
async function checkUpdate() {
  let u;
  try { u = await api("update"); } catch { return; }
  if (!u.newer) return;
  const card = $("#update");
  const prog = h("div");
  const btn = h("button.btn.primary.sm", u.can_install ? "Update now" : "Download");
  btn.addEventListener("click", async () => {
    if (!u.can_install) return openOsu(u.url);
    btn.disabled = true;
    const box = progressBox("Downloading the update…");
    put(prog, box.el);
    try {
      await runJob("update/install", {}, box);
      put(card, h("div.update-card", h("b", `Installing osu!coach ${u.latest}…`),
        h("div.small", "The window closes now and the new version starts by itself in a few seconds.")));
    } catch (e) {
      toast(e.message, true);
      put(prog);
      btn.disabled = false;
    }
  });
  put(card, h("div.update-card", { title: u.notes || "" },
    h("b", `osu!coach ${u.latest} is out`),
    h("div.small", `You have ${u.current}.` + (u.can_install ? " Your settings and profile stay as they are." : "")),
    btn, prog));
}

const BUILDERS = { profile: buildProfile, skills: buildSkills, improvement: buildImprovement, replays: buildReplays,
  search: buildSearch, settings: buildSettings, setup: buildSetup };

function show(page) {
  if (S.navLocked && page !== S.current) return;
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
  // a drop-down: the headline and its cost, the explanation and the fix when opened
  return h("details.habit",
    h("summary",
      h("div.rank" + (isAcc ? ".acc" : ""), n),
      h("span.chip" + (isAcc ? ".warn" : ".pink"), CAT_NAMES[group.category] || group.category),
      h("h3.grow", top.title),
      h("div.impact", h("span", impact), h("div.meter", h("div.com", { style: { width: `${Math.max(3, group.impact / maxImpact * 100)}%` } }))),
      h("span.chev", { html: ICON.chev })),
    h("div.body",
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

/** A number with its unit ("5.8%", "13.1px", "100x75 -> 94.2x70.7mm", "1.2 -> 1.27") in an advice text. */
const ADVICE_NUM = /[+−-]?\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?(?:mm|px|%| deg|x\b)?(?: -> [+−-]?\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?(?:mm|px|%| deg)?)?/g;

/** An advice text with its numbers highlighted ("->" as an arrow), but not the counts of hits. */
function hiNums(text) {
  const s = String(text ?? ""), out = [];
  let last = 0;
  for (const m of s.matchAll(ADVICE_NUM)) {
    if (s.startsWith(" hits", m.index + m[0].length)) continue;
    out.push(s.slice(last, m.index), h("span.num", m[0].replace(" -> ", " → ")));
    last = m.index + m[0].length;
  }
  out.push(s.slice(last));
  return out;
}

function adviceCard(title, ico, items, emptyText, top) {
  return h("div.card",
    h("div.card-head", h("div.ico", { html: ICON[ico] }), h("h3", title)),
    top || null,
    items.length ? items.map(i => h("div.advice-item",
      h("div.row", h("b", hiNums(i.title)), i.kind === "info" ? h("span.chip.green", "ok") : h("span.chip.pink", "change")),
      h("p", hiNums(i.detail)))) : h("p.muted", { style: { margin: "8px 0 0" } }, emptyText));
}

/** Overaim or underaim in one play (setup_advice.play_aim): a line over the area advice of the recent plays. */
function playAimLine(p) {
  if (!p) return null;
  let text, color = "var(--text-2)";
  if (p.scale == null) text = `Only ${p.jumps} hits in this play: at least ${p.min_jumps} hits are needed to measure overaim or underaim.`;
  else {
    const pct = `${p.scale > 0 ? "+" : "−"}${Math.abs(p.scale * 100).toFixed(1)}%`;
    const where = `aim meter along the movement ${p.scale > 0 ? "above" : "below"} the centre, ${pct} of the distance, ${p.jumps} hits`;
    if (Math.abs(p.scale) < p.ok) text = `In this play: on target (${where}).`;
    else if (Math.abs(p.t ?? 0) < (p.t_min ?? 3)) text = `In this play: no clear overaim or underaim (${where}, but it changes too much from note to note).`;
    else {
      text = `In this play: ${p.scale > 0 ? "overaim" : "underaim"} (${where}).`;
      color = "var(--warn)";
    }
  }
  return h("div.advice-item", h("p", { style: { margin: 0, color } }, hiNums(text)),
    h("p.small.muted", { style: { margin: "4px 0 0" } }, "One play varies: the advice below comes from your recent plays."));
}

function trendTable(trends) {
  if (!trends?.length) return h("p.muted", "At least 10 recent plays are needed for a trend.");
  return h("table.trend-table",
    h("thead", h("tr", h("th", "Category"), h("th", "Older plays"), h("th", "Newer plays"), h("th", ""))),
    h("tbody", trends.map(t => h("tr",
      h("td", t.name || CAT_NAMES[t.category]),
      h("td.mono", t.old == null ? "—" : pct(t.old) + " misses"),
      h("td.mono", t.new == null ? "—" : pct(t.new) + " misses"),
      h("td", t.trend === "worse" ? h("span.chip.red", "▲ worse") : t.trend === "better" ? h("span.chip.green", "▼ better")
        : t.trend === "none" ? h("span.small.muted", "not enough notes") : h("span.chip", "steady"))))));
}

/** Recomputing the profile feeds both the Profile and the Skills pages. */
/** While the profile is computed the other pages wait: they show what it produces. */
function lockNav(on) {
  S.navLocked = on;
  document.querySelectorAll(".nav-item").forEach(b => {
    const lock = on && b.dataset.page !== S.current;
    b.disabled = lock;
    b.title = lock ? "Available when the profile has been computed" : "";
  });
}

async function recomputeProfile(btn, prog) {
  btn.disabled = true;
  lockNav(true);
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
    lockNav(false);
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
        adviceCard("Area / sensitivity", "tablet", hb.setup?.area || [], hb.setup?.area_note || "No advice with the current cutoffs."),
        adviceCard("Keyboard / rapid trigger", "keyboard", hb.setup?.keys || [], hb.setup?.keys_note || "No key problems with the current cutoffs."))));
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
const ELO_MIN_PAD = 50;   // rating points a chart shows at least below its lowest and above its highest rating
const ELO_GRID = 50;      // a grid line every this many rating points

function eloChart(dates, values, plays) {
  const W = 420, H = 150, L = 40, R = 10, T = 10, B = 24;
  // the chart's own scale, around its values: the lowest and highest rating of the period, with some room
  const known = values.filter(v => v != null);
  const vmin = known.length ? Math.min(...known) : 1200, vmax = known.length ? Math.max(...known) : 1200;
  const pad = Math.max(ELO_MIN_PAD, (vmax - vmin) * .15);
  const lo = vmin - pad, hi = vmax + pad;
  const x = i => L + i / Math.max(1, dates.length - 1) * (W - L - R);
  const y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "elo-chart", role: "img" });
  // dashed lines at the lowest and highest rating of the period
  const apart = Math.max(0, 12 - (y(vmin) - y(vmax))) / 2;   // labels too close: pushed away from each other
  const labels = [];
  for (const [v, dy] of known.length ? (vmax > vmin ? [[vmin, apart], [vmax, -apart]] : [[vmin, 0]]) : []) {
    root.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "ref" }));
    const t = svg("text", { x: L - 6, y: y(v) + 4 + dy, class: "axis", "text-anchor": "end" }); t.textContent = Math.round(v); root.append(t);
    labels.push(y(v) + dy);
  }
  // a faint line every 50 points, labelled where it doesn't crowd the other labels
  for (let v = Math.ceil(lo / ELO_GRID) * ELO_GRID; v <= hi; v += ELO_GRID) {
    root.prepend(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }));
    if (labels.every(l => Math.abs(l - y(v)) >= 12)) {
      const t = svg("text", { x: L - 6, y: y(v) + 4, class: "axis faint", "text-anchor": "end" }); t.textContent = v; root.append(t);
      labels.push(y(v));
    }
  }
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
  const prog = h("div.job-prog");   // the cards right under it have no margin of their own
  const body = h("div");
  page.append(h("div.page-head", h("div", h("h1", "Improvement"), sub), btn), prog, body);

  function render(e) {
    if (!e) {
      put(body, h("div.card", emptyState("chart", "No ratings yet", "They are computed from your plays of the last 90 days (and the month before): a minute or two the first time.",
        h("button.btn.primary.big", { onclick: () => recompute() }, "Compute my ratings"))));
      return;
    }
    sub.textContent = `Your rating in each skillset at the end of every day, from ${e.plays} plays · updated ${fmtDate(e.updated)}. `
      + "The dashed lines are your lowest and highest rating of the period; 1200 is the reference player, and +400 means ten times the odds of passing the same challenge.";
    put(body, h("div.elo-grid", Object.entries(e.skills).map(([key, s]) => {
      const change = s.change == null ? null : Math.round(s.change);
      const trend = change == null ? h("span.chip", "no data")
        : change > 0 ? h("span.chip.green", `▲ +${change} in 90 days`)
        : change < 0 ? h("span.chip.red", `▼ ${change} in 90 days`) : h("span.chip", "unchanged");
      return h("div.card.elo-card",
        h("div.head", h("div", h("h3", s.label), trend), h("div.big-num", s.current ?? "—")),
        eloChart(e.dates, s.values, e.plays_per_day));
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
  content.append(h("div.card", emptyState("replay", "Pick a replay", "Select a replay from the list (or import one, or drop .osr files on the window) and press Analyze.")));

  search.addEventListener("input", () => filter());
  player.addEventListener("change", () => filter());
  list.addEventListener("scroll", () => { if (list.scrollTop + list.clientHeight > list.scrollHeight - 400) more(); });
  file.addEventListener("change", async () => {
    const files = [...file.files];
    file.value = "";
    await importFiles(files);
  });
  S.importReplays = importFiles;   // for the .osr files dropped on the window

  /** Copies .osr files into the imported replays, then selects the last one and analyses it. */
  async function importFiles(files) {
    let last = null;
    for (const f of files) {
      try {
        const r = await fetch(`/api/import?name=${encodeURIComponent(f.name)}`, { method: "POST", body: await f.arrayBuffer() });
        const d = await r.json();
        if (d.error) throw new Error(d.error);
        last = d.id;
      } catch (e) { toast(`${f.name}: ${e.message}`, true); }
    }
    if (last) {
      await load(false);
      const row = R.rows.find(r => r.id === last);
      if (row) {
        select(row);
        if (row.found) analyze(row);
        else if (row.supported) fetchMap(row);
      }
    }
  }

  async function load(reload) {
    count.textContent = "loading…";
    put(list, h("div.empty", h("div.spinner", { style: { margin: "0 auto 10px" } }), "Reading your replays (the first time takes a while)…"));
    try {
      const d = await api("replays" + (reload ? "?reload=1" : ""));
      R.rows = d.replays;
      R.owner = d.owner;
      player.options[0].textContent = d.owner ? `${d.owner}'s plays` : "My plays";
      filter();
      countBreaks();
    } catch (e) {
      put(list, h("div.empty", e.message));
      count.textContent = "";
    }
  }

  /** No miss but no full combo: the slider breaks aren't in the replay's header, so the plays are judged in the
      background (newest first) and each row shows its count as it comes. */
  async function countBreaks() {
    if (R.breaksJob) api(`job/${R.breaksJob}/cancel`, {}).catch(() => {});
    const ids = R.rows.filter(r => !r.miss && !r.perfect && r.se == null && r.found && r.supported).map(r => r.id);
    if (!ids.length) return;
    const byId = new Map(R.rows.map(r => [r.id, r]));
    const paint = counts => {
      for (const [id, n] of Object.entries(counts || {})) {
        const r = byId.get(id);
        if (!r || r.se != null) continue;
        Object.assign(r, n);
        repaintRow(r);
      }
    };
    try {
      paint(await runJob("combo-breaks", { ids }, { attach: id => { R.breaksJob = id; }, update: j => paint(j.partial) }));
    } catch { }
  }

  function repaintRow(r) {
    const old = list.querySelector(`.rp[data-id="${CSS.escape(r.id)}"]`);
    if (old) old.replaceWith(rowEl(r));
    if (R.selected?.id === r.id && !R.viewer && R.selected.header) put(R.selected.header, playResult(r));
  }

  /** Misses, or FC, or for a play with no miss what kept it from a full combo: slider breaks (SB) and dropped
      slider ends (SE), once counted. */
  function resultTag(r) {
    if (r.miss) return h("span.x", `${r.miss}✕`);
    if (r.perfect) return h("span.fc", "FC");
    const parts = [r.sb ? `${r.sb} SB` : null, r.se ? `${r.se} SE` : null].filter(Boolean);
    return h("span.sb", { title: "No miss, but not a full combo. SB: slider breaks (combo lost); SE: slider ends missed (no combo break, but not FC)" },
      parts.length ? parts.join(" ") : "not FC");
  }

  function playResult(r) {
    if (r.miss || r.perfect)
      return h("div.stat", h("b", { style: { color: r.miss ? "var(--miss)" : "var(--ok)" } }, r.miss || "FC"), h("span", "miss"));
    return [h("div.stat", h("b", { style: { color: "var(--warn)" } }, r.sb ?? "…"), h("span", "slider breaks")),
      r.se ? h("div.stat", h("b", { style: { color: "var(--warn)" } }, r.se), h("span", "slider ends missed")) : null];
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
        h("span.acc", pct(r.acc, 2)), h("span", `${r.combo}x`), resultTag(r)));
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
            r.header = h("div", { style: { display: "contents" } }, playResult(r)),
            h("div.stat", h("b", { style: { color: "var(--r100)" } }, r.c100), h("span", "100")),
            h("div.stat", h("b", { style: { color: "var(--r50)" } }, r.c50), h("span", "50")))),
        h("button.btn.primary.big", { disabled: !r.found || !r.supported, onclick: () => analyze(r) }, icon("play"), "Analyze replay")),
      !r.supported ? h("p.muted", "Relax and Autopilot replays can't be analysed.")
        : r.found ? null : h("div.row", { style: { marginTop: "12px" } },
          h("span.muted", "This replay's map is not in your Songs folder."),
          h("button.btn", { onclick: () => fetchMap(r) }, icon("download"), "Download the map")));
  }

  /** Download a replay's missing map (looked up on the osu! site), then analyse the replay. */
  async function fetchMap(r) {
    if (R.viewer) { R.viewer.destroy(); R.viewer = null; }
    const box = progressBox("Downloading the map…");
    put(content, h("div.card", h("h2", "Downloading the replay's map"), box.el));
    try {
      await runJob("fetch-map", { id: r.id }, box);
      toast("Map saved to Songs: press F5 in osu!'s song select to import it.");
      await load(false);
      const row = R.rows.find(x => x.id === r.id);
      if (row) { select(row); if (row.found) analyze(row); }
    } catch (e) {
      if (R.selected?.id !== r.id) return;
      put(content, h("div.card", emptyState("x", "The map couldn't be downloaded", e.message,
        h("button.btn", { onclick: () => fetchMap(r) }, "Retry"))));
    }
  }

  async function analyze(r) {
    if (R.viewer) { R.viewer.destroy(); R.viewer = null; }
    const box = progressBox("Analyzing the replay…");
    put(content, h("div.card", h("h2", r.map), box.el));
    try {
      const res = await runJob("analyze", { id: r.id }, box);
      if (r.sb !== res.stats.sb || r.se !== res.stats.se) { r.sb = res.stats.sb; r.se = res.stats.se; repaintRow(r); }
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
          !s.miss && s.sb ? h("div.stat", h("b", { style: { color: "var(--warn)" } }, s.sb), h("span", "slider breaks")) : null,
          !s.miss && s.se ? h("div.stat", h("b", { style: { color: "var(--warn)" } }, s.se), h("span", "slider ends missed")) : null,
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
    R.viewer = new ReplayViewer(vwrap, a.viewer, { sections, skin: loadSkin(), musicOffset: () => S.state.settings.viewer.offset,
      cursorSize: () => S.state.settings.viewer.cursor_size, fullscreen: on => api("fullscreen", { on }) });
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

    // offset
    if (a.offset) content.append(offsetSection(a.offset));

    // setup
    content.append(h("div.section",
      h("div.section-title", h("h2", "Area and rapid trigger"), h("span.count", a.setup_desc)),
      h("div.advice-grid",
        adviceCard("Area / sensitivity", "tablet", a.setup.area, a.setup.area_note || "No advice with the current cutoffs.",
          playAimLine(a.setup.play_aim)),
        adviceCard("Keyboard / rapid trigger", "keyboard", a.setup.keys, a.setup.keys_note || "No key problems with the current cutoffs."))));

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

  /** The local offset this play asks for: osu!'s local offset moves the hit objects later as it goes up, so hitting
      late on average asks for more. When the player is off by about as much on most maps, the universal one first. */
  function offsetSection(o) {
    const ms = x => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(Math.round(x))} ms`;
    const side = x => x > 0 ? "late" : "early";
    const body = h("div.card");
    const head = h("div.section-title", h("h2", "Offset"), h("span.count", "from how early or late your hits were on average"));
    if (o.hits < o.min_hits) {
      body.append(h("p.muted", { style: { margin: 0 } }, `Too few hits in this play (${o.hits}) to say anything about the offset.`));
      return h("div.section", head, body);
    }
    const u = o.usual;
    const universal = u && Math.abs(u.median) >= 5 && u.same_side >= .7 && Math.sign(u.median) === Math.sign(o.mean);
    const diff = universal ? o.mean - u.median : o.mean;       // what's left to this map
    const change = Math.abs(diff) >= o.ok_ms ? Math.round(diff) : 0;
    const target = o.current != null ? o.current + change : null;
    body.append(h("div.stat-row",
      h("div.stat", h("b", ms(o.mean)), h("span", `on average (${o.hits.toLocaleString("en")} hits)`)),
      h("div.stat", h("b", o.current != null ? ms(o.current) : "—"), h("span", "local offset now")),
      h("div.stat", h("b", { style: { color: change ? "var(--pink-2)" : "var(--ok)" } }, change ? (target != null ? ms(target) : `${ms(change)}`) : "keep it"),
        h("span", change ? (target != null ? "suggested local offset" : "change to the local offset") : "local offset"))));
    const lines = [];
    if (universal) {
      lines.push(h("p", h("b", "Universal offset first. "),
        `You're ${side(u.median)} by about ${Math.abs(Math.round(u.median))} ms on most maps (median of your last ${u.plays} plays), so it isn't this map: `
        + `${u.median > 0 ? "raise" : "lower"} the Universal offset in osu!'s options (Audio section) by ${Math.abs(Math.round(u.median))} ms.`
        + (change ? " Then this map still needs the local offset below." : " Then this map needs nothing of its own.")));
    }
    if (change) {
      lines.push(h("p", `You hit ${Math.abs(Math.round(diff))} ms ${side(diff)}${universal ? " more than usual" : ""} on this map: `
        + `${change > 0 ? "raise" : "lower"} its local offset${target != null ? ` to ${ms(target)}` : ` by ${Math.abs(change)} ms`} `
        + "(higher values bring the notes later). In osu!, at the start of the map press + or − to change it by 5 ms, Alt and + or − by 1 ms."));
    } else if (!universal) {
      lines.push(h("p", `In time: ${Math.abs(Math.round(o.mean * 10) / 10)} ms ${side(o.mean)} on average is within ${o.ok_ms} ms, no offset to change.`));
    }
    if (o.rate !== 1)
      lines.push(h("p.small.muted", `Measured on a ${o.rate > 1 ? "DT" : "HT"} play: check it on a nomod play too before relying on it.`));
    lines.push(h("p.small.muted", "It's worth changing only if you're off on this map in more than one play: a single play can be off by a few ms by chance."));
    body.append(h("div", { style: { marginTop: "12px" } }, lines));
    return h("div.section", head, body);
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

/** The mods to search with, picked as in osu!: they add up to one combination (HD + DT = HDDT); NM clears the others,
    DT/HT and HR/EZ exclude each other. get() gives a one-item list (["HDDT"]), as the search expects. */
const MOD_CODES = ["EZ", "HD", "HR", "DT", "HT"], MOD_CLASH = { DT: "HT", HT: "DT", HR: "EZ", EZ: "HR" };
function modPicker(value) {
  const el = h("div.seg");
  let cur = new Set();
  const buttons = ["NM", ...MOD_CODES].map(v => {
    const b = h("button", { onclick: () => {
      if (b.disabled) return;
      if (v === "NM") cur.clear();
      else if (cur.has(v)) cur.delete(v);
      else { cur.add(v); cur.delete(MOD_CLASH[v]); }
      paint();
    } }, v);
    b.dataset.v = v;
    return b;
  });
  const paint = () => buttons.forEach(b => b.classList.toggle("on", b.dataset.v === "NM" ? !cur.size : cur.has(b.dataset.v)));
  // older settings kept a list of separate mods (["NM", "DT"]): their mods are read as one combination
  const set = v => {
    cur = new Set();
    for (const code of [].concat(v || []).join("").toUpperCase().match(/../g) || [])
      if (MOD_CODES.includes(code)) { cur.add(code); cur.delete(MOD_CLASH[code]); }
    paint();
  };
  el.append(...buttons);
  set(value);
  return { el, buttons, get: () => [MOD_CODES.filter(m => cur.has(m)).join("") || "NM"], set };
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

  // a share slider for each included skillset that has a share of the notes: at least this much of them
  // stops: any, then from SHARE_FIRST% up in 5% steps (the slider's first stop, SHARE_FIRST - 5, stands for "any")
  const SHARE_FIRST = 25;
  const SHARED = ["jump", "stream", "alt", "finger control", "tech"];
  const shares = { min: {} };
  const shareBox = h("div.share-sliders");
  function paintShares() {
    put(shareBox, SHARED.filter(sk => skillState[sk] === 1).map(sk => {
      const input = h("input.slider-single", { type: "range", min: SHARE_FIRST - 5, max: 100, step: 5,
        value: shares.min[sk] ? shares.min[sk] : SHARE_FIRST - 5 });
      const val = h("span.val");
      const show = () => {
        const v = +input.value < SHARE_FIRST ? 0 : +input.value;
        shares.min[sk] = v;
        val.textContent = v === 0 ? "any" : `at least ${v}%`;
        val.classList.toggle("any", v === 0);
      };
      input.addEventListener("input", show);
      show();
      return h("div.share-slider", h("span", h("b", S.state.skill_names[sk] || sk), h("span.muted", " included")), input, val);
    }));
  }
  paintShares();

  const source = seg(SOURCE_OPTIONS, toSources(cfg.source), () => syncSource(), true);
  const mods = modPicker(cfg.mods);
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
    if (!songs) mods.set(mods.get()[0].includes("DT") ? ["DT"] : ["NM"]);
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
  const dlProg = h("div", { style: { marginTop: "16px" } });
  const results = h("div.section");
  page.append(panel, prog, dlProg, results);

  function reset() {
    Object.keys(skillState).forEach(k => skillState[k] = 0);
    toggles.forEach(t => t.paint());
    text.value = "";
    shares.min = {};
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
      source: source.get(), mods: mods.get(), status: status.get(),
      unplayed: unplayed.querySelector("input").checked, limit: +limit.value || 30, text: text.value.trim(),
      recommended: recommended.checked,
    };
    RANGES.forEach((r, i) => { q[r.key] = ranges[i].get(); });
    const box = progressBox("Searching…");
    put(prog, box.el);
    put(results);
    shown = { keys: [], grid: null, title: null, count: null, partial: true, maps: [] };
    try {
      const res = await runJob("search", q, box, partial => renderResults({ maps: partial, total: partial.length, note: "" }, true));
      renderResults(res, false);
    } catch (e) {
      if (e.stopped) {    // what was found so far stays
        const maps = Array.isArray(e.partial) ? e.partial : e.partial?.maps || [];
        renderResults({ maps, total: maps.length, note: "Search stopped." }, false, true);
      } else toast(e.message, true);
    } finally {
      put(prog);
      running = false; goBtn.disabled = false;
    }
  }

  // downloads: one queue for the whole page (a map's own button and "Download all" add to it), one set at a time
  const downloaded = new Set(), queued = new Set(), cleared = new Set(), dlQueue = [];
  let dlRunning = false, dlStopped = false, dlCount = { total: 0, seen: 0, ok: 0 }, dlFailed = [];
  let last = { maps: [], note: "" };
  const cardKey = m => `${m.beatmap_id}:${m.mods}:${m.name}`;
  let shown = { keys: [], grid: null, title: null, count: null, partial: false, maps: [] };
  const pendingSets = maps => [...new Map(maps.filter(m => !m.local && m.set_id && !downloaded.has(m.set_id) && !queued.has(m.set_id))
    .map(m => [m.set_id, m])).values()];

  function renderResults(res, partial, force = false) {
    if (!partial) last = res;
    const maps = res.maps.filter(m => !cleared.has(m.set_id)), keys = maps.map(cardKey);
    // while the search runs, the maps found so far keep their cards (redrawing them under the mouse would flicker)
    if (!force && shown.grid && shown.keys.length <= keys.length && shown.keys.every((k, i) => k === keys[i])
        && (partial || shown.keys.length === keys.length)) {
      shown.grid.append(...maps.slice(shown.keys.length).map(mapCard));
      Object.assign(shown, { keys, maps, partial });
      shown.title.textContent = `${maps.length} maps`;
      shown.count.textContent = partial ? "searching…" : res.note || "";
      paintDownloadButtons();
      if (partial) return;
    }
    let grid = maps.length ? h("div.results", maps.map(mapCard)) : null;
    if (res.recommended && maps.length) {   // one ladder per skillset, easiest first
      const groups = [...new Set(maps.map(m => m.group))];
      grid = h("div", groups.map(g => h("div.rec-group", h("h3", g), h("div.results", maps.filter(m => m.group === g).map(mapCard)))));
      force = true;
    }
    const title = h("h2", `${maps.length} maps`), count = h("span.count", partial ? "searching…" : res.note || "");
    const allBtn = h("button.btn.primary", { onclick: () => enqueue(pendingSets(shown.maps)) }, icon("download"), h("span"));
    const clearBtn = h("button.btn.ghost", { title: "Remove the downloaded maps from these results", onclick: clearDownloaded },
      icon("x"), h("span"));
    shown = { keys, grid, title, count, partial, maps, allBtn, clearBtn };
    put(results,
      h("div.section-title", title, count, h("div.grow"),
        maps.some(m => m.local) ? h("button.btn.ghost", { onclick: () => api("open", { url: "songs" }) }, icon("folder"), "Open Songs") : null,
        clearBtn, allBtn),
      grid || (partial ? null : h("div.card", emptyState("search", "No maps found", "Widen the filters or drop a skillset."))));
    paintDownloadButtons();
  }

  /** "Download all (n)" and "Clear downloaded (n)" follow the queue as it goes. */
  function paintDownloadButtons() {
    const { allBtn, clearBtn, maps, partial } = shown;
    if (!allBtn) return;
    const left = pendingSets(maps).length, done = new Set(maps.filter(m => downloaded.has(m.set_id)).map(m => m.set_id)).size;
    allBtn.lastChild.textContent = `Download all (${left})`;
    allBtn.disabled = partial || !left;
    allBtn.classList.toggle("hidden", !left);
    clearBtn.lastChild.textContent = `Clear downloaded (${done})`;
    clearBtn.classList.toggle("hidden", !done);
  }

  /** Take the downloaded maps out of the results. */
  function clearDownloaded() {
    downloaded.forEach(id => cleared.add(id));
    renderResults(shown.partial ? { maps: shown.maps, note: "" } : last, shown.partial, true);
  }

  function dlSlot(m) {
    return m.local ? h("span.chip.green", "in Songs")
      : downloaded.has(m.set_id) ? h("span.chip.green", "downloaded ✓")
      : queued.has(m.set_id) ? h("span.chip", dlQueue.some(x => x.set_id === m.set_id) ? "queued…" : "downloading…")
      : h("button.btn.sm", { onclick: e => { e.stopPropagation(); enqueue([m]); } }, icon("download"), "Download");
  }

  /** Redraw the download badge of every card of a beatmapset. */
  function paintSet(setId) {
    results.querySelectorAll(`.map-card[data-set="${setId}"]`).forEach(card => put(card.querySelector(".dl-slot"), dlSlot(card.map)));
  }

  function mapCard(m) {
    const card = h("div.card.map-card", { title: "Open the map's page", onclick: () => m.beatmap_id && openOsu(`https://osu.ppy.sh/b/${m.beatmap_id}`) },
      h("div.cover", { style: m.set_id ? { backgroundImage: `url(https://assets.ppy.sh/beatmaps/${m.set_id}/covers/card.jpg)` } : {} },
        h("div.badges", m.step ? h("span.chip.step-chip", `#${m.step}`) : null, starChip(m.stars), modsChip(m.mods), h("div.grow"),
          h("span.dl-slot", dlSlot(m)))),
      h("div.body",
        h("div", h("div.title", m.title), h("div.sub", `${m.artist} · [${m.version}] by ${m.creator}`)),
        h("div.vals",
          h("span", h("b", "BPM "), num(m.bpm)), h("span", h("b", "AR "), num(m.ar, 1)), h("span", h("b", "CS "), num(m.cs, 1)),
          h("span", h("b", "OD "), num(m.od, 1)), h("span", h("b", "⏱ "), fmtLen(m.length))),
        h("div.tags", m.tags.map(t => h("span.chip.pink", S.state.skill_names[t] || t))),
        m.why ? h("div.why", m.why) : null));
    card.dataset.set = m.set_id || "";
    card.map = m;
    return card;
  }

  function enqueue(list) {
    for (const m of list) {
      if (!m.set_id || m.local || downloaded.has(m.set_id) || queued.has(m.set_id)) continue;
      queued.add(m.set_id);
      dlQueue.push(m);
      dlCount.total++;
      paintSet(m.set_id);
    }
    paintDownloadButtons();
    if (!dlRunning) runQueue();
  }

  async function runQueue() {
    dlRunning = true; dlStopped = false;
    const box = progressBox("Downloading…");
    box.el.querySelector("button").addEventListener("click", () => { dlStopped = true; });   // Stop: the whole queue
    put(dlProg, box.el);
    while (dlQueue.length && !dlStopped) {
      const m = dlQueue.shift();
      paintSet(m.set_id);   // queued -> downloading
      const label = `Downloading ${m.artist} - ${m.title}`;
      const show = () => box.update({ label, total: dlCount.total, done: dlCount.seen });
      show();
      try {
        const res = await runJob("download", { sets: [{ set_id: m.set_id, artist: m.artist, title: m.title }] },
          { attach: id => box.attach(id), update: show });
        if (res.done.length) { downloaded.add(m.set_id); dlCount.ok++; }
        else dlFailed.push(`${m.title}: ${res.failed[0]?.error || "failed"}`);
      } catch (e) {
        if (!dlStopped) dlFailed.push(`${m.title}: ${e.message}`);
      }
      dlCount.seen++;
      queued.delete(m.set_id);
      paintSet(m.set_id);
      paintDownloadButtons();
    }
    for (const m of dlQueue.splice(0)) { queued.delete(m.set_id); paintSet(m.set_id); }   // stopped: the rest isn't downloaded
    if (dlFailed.length) toast(`${dlFailed.length} not downloaded: ${dlFailed.join("; ")}`, true);
    if (dlCount.ok) toast(`${dlCount.ok} maps saved to Songs: press F5 in osu!'s song select to import them.`);
    dlCount = { total: 0, seen: 0, ok: 0 }; dlFailed = [];
    put(dlProg);
    dlRunning = false;
    paintDownloadButtons();
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

/** The player's osu! skin for the replay viewer, loaded once (again after the settings change). */
function loadSkin() {
  if (!S.skin) S.skin = api("skin").then(info => SkinAssets.load(info)).catch(e => { S.skin = null; throw e; });
  return S.skin;
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
  const fromOtd = (su.source || "").startsWith("OpenTabletDriver");
  if (fromOtd) { areaW.disabled = true; areaH.disabled = true; }
  const tabletRow = h("div", h("div.two", h("label.field", "Area: width (mm)", areaW), h("label.field", "Area: height (mm)", areaH)),
    fromOtd ? h("div.small.muted", "Read from OpenTabletDriver: change the area there, osu!coach follows it. After a change the area advice waits for 5 plays with the new area.") : null);
  const mouseRow = h("div.two", h("label.field", "In-game sensitivity", sens), h("label.field", "DPI", dpi));
  const rtFields = [h("label.field", "Rapid trigger: press (mm)", rtP), h("label.field", "Rapid trigger: release (mm)", rtR)];
  const keyRow = h("div.three", h("label.field", h("span", "Actuation point (mm) ", h("span.hint", "(how deep a press registers)")), act), ...rtFields);
  const setupDesc = h("div.small.muted", su.describe);
  function paintSetup() {
    tabletRow.classList.toggle("hidden", device.get() !== "tablet");
    mouseRow.classList.toggle("hidden", device.get() !== "mouse");
    rtFields.forEach(f => f.classList.toggle("hidden", keyboard.get() !== "rt"));
    keyRow.classList.toggle("hidden", !keyboard.get());
  }
  paintSetup();
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.tablet }), h("h3", "Your setup")),
    h("div.small.muted", "Used to give exact numbers in the area and rapid trigger advice. After you change the area or the keyboard settings, their advice starts again and waits for 5 plays made with the new ones."),
    h("div.row", h("span.muted", "Device"), device.el), tabletRow, mouseRow,
    h("div.row", h("span.muted", "Keyboard"), keyboard.el), keyRow, setupDesc,
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
  const sMods = modPicker(sc.mods);
  const sStatus = seg(STATUS_OPTIONS, toStatuses(sc.status), null, true);
  const sUnplayed = h("input", { type: "checkbox", checked: sc.unplayed });
  const sLimit = h("input.input.num", { type: "number", min: 1, value: sc.limit });
  const sPages = h("input.input.num", { type: "number", min: 1, max: 1000, value: sc.max_pages });
  const sMirror = h("input.input", { value: sc.mirror });
  const sTag = sensSlider("Tag a skillset from", "A map is tagged (and found by the skillset filters) with every skillset that has at least this share of its intense notes; its main type always. Applied to the next search.",
    sc.tag_min_pct, 0, 60, 1, "%");
  const sReadAr = sensSlider("Reading up to AR", "A map can be tagged reading only at this effective AR or lower (with the mods it's played with). Lower AR also raises its reading score. Applied to the next search.",
    sc.reading_max_ar ?? 8.5, 7, 10, 0.1, "");
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
    sTag.el, sReadAr.el));

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

  // replay viewer
  const vc = cfg.viewer;
  const skinSel = h("select.input.grow", h("option", { value: "" }, "osu!'s current skin"));
  if (vc.skin) skinSel.append(h("option", { value: vc.skin, selected: true }, vc.skin));
  const skinNote = h("div.small.muted", "Hit circles, cursor, judgements and hitsounds of the replay viewer come from this skin; what it lacks is drawn by osu!coach. Breaking a combo of 20 or more plays the skin's combobreak. Music and hitsound volumes are set in the viewer.");
  api("skin").then(info => {
    skinSel.replaceChildren(h("option", { value: "" }, `osu!'s current skin${info.current ? ` (${info.current})` : ""}`),
      ...info.skins.map(name => h("option", { value: name, selected: name === vc.skin }, name)));
  }).catch(() => { });
  const musicOffset = sensSlider("Music offset", "Raise it if the viewer's music comes late, lower it if it comes early.",
    vc.offset ?? 0, -100, 100, 1, " ms");
  musicOffset.el.querySelector("input").classList.add("no-fill");   // 0 is the middle: no side is "filled"
  const cursorSize = sensSlider("Cursor size", "The cursor in the replay viewer (the skin's, or the one drawn by osu!coach), as a multiple of its usual size.",
    vc.cursor_size ?? 1, 0.5, 2, 0.1, "x");
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.play }), h("h3", "Replay viewer")),
    h("label.field", "Skin", skinSel), skinNote, cursorSize.el, musicOffset.el));

  // profile
  const habitPlays = h("input.input.num", { type: "number", min: 10, max: 1000, value: cfg.habit_plays });
  const skillPlays = h("input.input.num", { type: "number", min: 20, max: 2000, value: cfg.skill_plays });
  wrap.append(h("div.card",
    h("div.card-head", h("div.ico", { html: ICON.user }), h("h3", "Profile and analysis")),
    h("div.two",
      h("label.field", h("span", "Recent plays for bad habits ", h("span.hint", "(also used when analysing replays)")), habitPlays),
      h("label.field", h("span", "Recent plays for levels ", h("span.hint", "(+ as many below AR 9 and above AR 10)")), skillPlays))));

  // jump to a section: one link per card, the one in view lit
  const cards = [...wrap.querySelectorAll(":scope > .card")];
  const links = cards.map(card => h("button.chip", { onclick: () => card.scrollIntoView({ behavior: "smooth", block: "start" }) },
    card.querySelector("h3").textContent));
  const nav = h("nav.settings-nav", links);
  wrap.before(nav);
  const spy = e => {
    const sc = e?.target;
    if (!page.isConnected || page.offsetParent === null || sc && sc !== document && !sc.contains?.(page)) return;
    const top = nav.getBoundingClientRect().bottom + 12;
    // at the bottom the last cards can't reach the top: the one in the upper half of the window then
    const bottom = sc && sc.scrollHeight != null && sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2;
    let on = 0;
    cards.forEach((c, i) => { if (c.getBoundingClientRect().top <= (bottom ? innerHeight / 2 : top)) on = i; });
    links.forEach((l, i) => l.classList.toggle("pink", i === on));
  };
  document.addEventListener("scroll", spy, { passive: true, capture: true });
  spy();

  wrap.append(h("div.row", { style: { position: "sticky", bottom: "0", padding: "14px 0", background: "linear-gradient(0deg, var(--bg) 60%, transparent)" } },
    h("div.grow"),
    h("button.btn.primary.big", { onclick: async () => {
      const data = {
        osu_dir: osuDir.value.trim(), habit_plays: +habitPlays.value || 50, skill_plays: +skillPlays.value || 100,
        search: { source: sSource.get(), mods: sMods.get(), status: sStatus.get(), unplayed: sUnplayed.checked,
          limit: +sLimit.value || 30, max_pages: +sPages.value || 100, mirror: sMirror.value.trim(), tag_min_pct: sTag.get(), reading_max_ar: sReadAr.get() },
        advice: Object.fromEntries(Object.entries(sl).map(([k, v]) => [k, v.get()])),
        skills: { stream_ur_tolerance_pct: urTol.get() },
        viewer: { skin: skinSel.value, offset: musicOffset.get(), cursor_size: cursorSize.get() },
      };
      try {
        const r = await api("settings", data);
        const dirChanged = r.settings.osu_dir !== S.state.settings.osu_dir;
        S.state.settings = r.settings;
        S.skin = null;   // the viewer's skin may have changed
        toast("Settings saved.");
        reloadProfile();
        if (dirChanged && S.pages.replays) { S.pages.replays.remove(); delete S.pages.replays; }
      } catch (e) { toast(e.message, true); }
    } }, "Save settings")));
}

// --- replays dropped on the window -------------------------------------------------------------------------

/** .osr files dragged onto the app go to the Replay page and are imported there; anything else dropped is
    ignored (the web view would otherwise open the file in place of the app). */
function setupDrop() {
  const overlay = h("div.drop-overlay.hidden", h("div.drop-box", icon("upload"), h("b", "Drop replays to analyse them"),
    h("div.small.muted", ".osr files")));
  document.body.append(overlay);
  const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
  const blocked = () => S.navLocked && S.current !== "replays";   // the other pages wait for the profile
  let depth = 0;   // dragenter/dragleave also fire on every child crossed
  document.addEventListener("dragenter", e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (depth++ === 0 && !blocked()) overlay.classList.remove("hidden");
  });
  document.addEventListener("dragleave", e => {
    if (!hasFiles(e)) return;
    if (--depth <= 0) { depth = 0; overlay.classList.add("hidden"); }
  });
  document.addEventListener("dragover", e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = blocked() ? "none" : "copy";
  });
  document.addEventListener("drop", e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    overlay.classList.add("hidden");
    const all = [...e.dataTransfer.files];
    const files = all.filter(f => f.name.toLowerCase().endsWith(".osr"));
    if (!files.length) return toast(all.length ? "Only .osr replay files can be dropped here." : "Nothing to import.", true);
    if (blocked()) return toast("Wait for the profile to be computed, then drop the replays again.", true);
    show("replays");
    if (S.current === "replays" && S.importReplays) S.importReplays(files);
  });
}

setupDrop();
boot();
