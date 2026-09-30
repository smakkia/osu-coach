/* Replay viewer in the spirit of Rewind: the playfield as it was played (stacked, HR-flipped objects, the
   cursor and its keys), judgements, a hit error bar and a timeline with the mistakes and the sections the
   analysis talks about. Map time is in ms; at 1x the map runs at the replay's own rate (DT 1.5x). */

(function () {
  const W = 512, H = 384;
  const COMBO = ["#ff66aa", "#66ccff", "#b98cff", "#ffcf5c"];
  const RES_COLOR = { 300: "#66ccff", 100: "#7ddc4f", 50: "#ffc93c", 0: "#ff4d6a" };
  const KEY_COLOR = { 0: "rgba(255,255,255,.9)", 1: "#ff66aa", 2: "#66ccff", 3: "#c77dff" };
  const SPEEDS = [0.1, 0.25, 0.5, 0.75, 1];            // playback speeds of the slider's ticks
  const TRAIL_MS = 140;
  const MUSIC_DRIFT = 120;                          // ms the song may drift from the replay before it's re-seeked
  // slider % -> gain: a curve like the ear hears it, and quieter overall (100% = 40% of full scale)
  const gain = v => .4 * (v / 100) ** 2;
  const CLICK_MARK_MS = 450;
  const JUDGE_MS = 700;
  const ERROR_BAR_MS = 4000;
  // the input strip shows this much before now (real time; a quarter of it after): - and + next to it pick one
  const STRIP_SPANS = [500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500], STRIP_DEFAULT = 5;
  const STRIP_CONTROLS = 22;                        // css px on the strip's left kept for its + and - buttons
  const AIM_MS = 8000, AIM_MAX = 40;                // the aim error meter: the last clicks, fading
  const TOP_STRIP = 70;                             // css px kept free above the playfield for the strip

  function lowerBound(arr, v) {
    let lo = 0, hi = arr.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m + 1; else hi = m; }
    return lo;
  }
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const fmt = ms => {
    ms = Math.max(0, ms);
    const m = Math.floor(ms / 60000), s = (ms % 60000) / 1000;
    return `${m}:${s.toFixed(1).padStart(4, "0")}`;
  };

  /** The player's osu! skin: its images (tinted to the combo colours when needed) and hitsounds. Sizes are in the
      skin's @1x pixels, where a 128-pixel circle is a circle of the map's radius (so a scale of radius / 64). */
  class SkinAssets {
    constructor(info) {
      this.info = info;
      this.img = {};
      this.buf = {};
      this.tints = new Map();
      this.volume = gain(50);           // set by the viewer's hitsounds slider
      this.ini = info.ini || {};
    }

    static async load(info) {
      const skin = new SkinAssets(info);
      const url = file => "/skin/" + file.split("/").map(encodeURIComponent).join("/");
      await Promise.all(Object.entries(info.images || {}).map(([key, v]) => new Promise(done => {
        const im = new Image();
        im.onload = () => { if (im.width > 1 || im.height > 1) skin.img[key] = { im, hd: v.hd }; done(); };
        im.onerror = done;
        im.src = url(v.file);
      })));
      try { skin.audio = new AudioContext(); } catch { skin.audio = null; }
      if (skin.audio) {
        await Promise.all(Object.entries(info.sounds || {}).map(async ([key, file]) => {
          try { skin.buf[key] = await skin.audio.decodeAudioData(await (await fetch(url(file))).arrayBuffer()); } catch { }
        }));
      }
      return skin;
    }

    has(key) { return !!this.img[key]; }

    size(key) {
      const e = this.img[key], k = e.hd ? 2 : 1;
      return [e.im.width / k, e.im.height / k];
    }

    /** The image multiplied by a colour (hit circles, approach circles), cached per colour. */
    tinted(key, color) {
      const id = `${key}|${color}`;
      if (!this.tints.has(id)) {
        const { im } = this.img[key], c = document.createElement("canvas");
        c.width = im.width; c.height = im.height;
        const g = c.getContext("2d");
        g.drawImage(im, 0, 0);
        g.globalCompositeOperation = "multiply"; g.fillStyle = color; g.fillRect(0, 0, c.width, c.height);
        g.globalCompositeOperation = "destination-in"; g.drawImage(im, 0, 0);
        this.tints.set(id, c);
      }
      return this.tints.get(id);
    }

    /** Draw an element centred at (x, y): `scale` playfield pixels per skin pixel. */
    sprite(ctx, key, x, y, scale, alpha = 1, color = null, angle = 0, centred = true) {
      if (!this.has(key) || alpha <= 0) return;
      const [w, h] = this.size(key), dw = w * scale, dh = h * scale;
      const src = color ? this.tinted(key, color) : this.img[key].im;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(x, y);
      if (angle) ctx.rotate(angle);
      ctx.drawImage(src, centred ? -dw / 2 : 0, centred ? -dh / 2 : 0, dw, dh);
      ctx.restore();
    }

    /** The combo number with the skin's digits, overlapping as its skin.ini says. */
    number(ctx, n, x, y, scale, alpha) {
      const digits = String(n).split("").map(d => `default-${d}`);
      if (!digits.every(d => this.has(d))) return false;
      const overlap = (this.ini.hitcircle_overlap ?? -2) * scale;
      const widths = digits.map(d => this.size(d)[0] * scale);
      let px = x - (widths.reduce((a, b) => a + b, 0) - overlap * (digits.length - 1)) / 2;
      digits.forEach((d, i) => {
        this.sprite(ctx, d, px + widths[i] / 2, y, scale, alpha);
        px += widths[i] - overlap;
      });
      return true;
    }

    play(names, volume) {
      if (!this.audio) return;
      for (const name of names) {
        const buf = this.buf[name];
        if (!buf) continue;
        const src = this.audio.createBufferSource(), gain = this.audio.createGain();
        gain.gain.value = this.volume * (volume ?? 100) / 100;
        src.buffer = buf; src.connect(gain).connect(this.audio.destination);
        src.start();
      }
    }
  }

  class ReplayViewer {
    constructor(root, data, opts = {}) {
      this.root = root;
      this.d = data;
      this.opts = opts;
      this.sections = opts.sections || [];     // [{start, end, label}]
      this.speed = 1;
      this.playing = false;
      this.loop = null;                        // [start, end] while a section loops
      this.prepare();
      this.build();
      this.now = this.start;
      this.last = null;
      this.frame = this.frame.bind(this);
      this.onKey = this.onKey.bind(this);
      document.addEventListener("keydown", this.onKey);
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(this.stage);
      this.ro.observe(this.tl);
      this.resize();
      requestAnimationFrame(this.frame);
      this.sounds = data.sounds || [];
      this.soundTimes = this.sounds.map(e => e[0]);
      if (data.music) {
        this.music = new Audio(data.music.url);
        this.music.preload = "auto";
        this.music.preservesPitch = !data.music.nightcore;   // DT keeps the pitch, NC raises it
        this.music.volume = gain(this.volumes.music);
      }
      if (opts.skin) opts.skin.then(skin => { if (!this.dead) this.setSkin(skin); }).catch(() => { });
    }

    setSkin(skin) {
      this.skin = skin;
      skin.volume = gain(this.volumes.hitsounds);
      const colours = (skin.ini.combo || []).map(c => `rgb(${c[0]},${c[1]},${c[2]})`);
      if (colours.length) for (const o of this.d.objects) o.color = colours[o.combo % colours.length];
      this.draw();
    }

    prepare() {
      const d = this.d, objs = d.objects;
      let color = -1, num = 0;
      this.maxDur = 0;
      for (const o of objs) {
        if (o.nc || color < 0 || o.k === "p") { color += 1; num = 0; }
        num += 1;
        o.combo = color;
        o.color = COMBO[color % COMBO.length];
        o.num = num;
        this.maxDur = Math.max(this.maxDur, o.e - o.t);
        const w50 = d.windows[2];
        // when the object's result is known, and where to show it
        if (o.k === "s") { o.jt = o.e; o.jx = o.path[o.rep % 2 === 0 ? 0 : o.path.length - 1]; }
        else if (o.k === "p") { o.jt = o.e; o.jx = [256, 192]; }
        else { o.jt = o.res === 0 || o.ht == null ? o.t + w50 : o.ht; o.jx = [o.x, o.y]; }
        o.jx = { x: o.jx[0], y: o.jx[1] };
      }
      this.times = objs.map(o => o.t);
      const f = d.frames;
      this.ft = f.t;
      // key presses (a key newly down), for the click marks and the key counters
      this.presses = [];
      let prev = 0;
      for (let i = 0; i < f.t.length; i++) {
        const k = f.k[i] & 3, fresh = k & ~prev;
        if (fresh & 1) this.presses.push({ t: f.t[i], x: f.x[i], y: f.y[i], key: 1 });
        if (fresh & 2) this.presses.push({ t: f.t[i], x: f.x[i], y: f.y[i], key: 2 });
        prev = k;
      }
      this.pressTimes = this.presses.map(p => p.t);
      // how long each key was held: [down, up] per key, for the input strip
      this.keyRuns = { 1: [], 2: [] };
      const down = { 1: null, 2: null };
      for (let i = 0; i < f.t.length; i++) {
        for (const bit of [1, 2]) {
          const on = (f.k[i] & bit) !== 0;
          if (on && down[bit] === null) down[bit] = f.t[i];
          if (!on && down[bit] !== null) { this.keyRuns[bit].push([down[bit], f.t[i]]); down[bit] = null; }
        }
      }
      for (const bit of [1, 2]) if (down[bit] !== null) this.keyRuns[bit].push([down[bit], f.t[f.t.length - 1]]);
      this.keyStarts = { 1: this.keyRuns[1].map(r => r[0]), 2: this.keyRuns[2].map(r => r[0]) };
      // every click on a circle or slider head: its offset from the centre in radii, and the incoming movement
      this.aims = [];
      let before = null;
      for (const o of objs) {
        if (o.k === "p") { before = null; continue; }
        if (o.cx != null && (o.k === "c" ? o.res > 0 : o.hr > 0)) {
          const dx = (o.cx - o.x) / d.radius, dy = (o.cy - o.y) / d.radius;
          let dir = null;
          if (before) {
            const mx = o.x - before.x, my = o.y - before.y, len = Math.hypot(mx, my);
            if (len >= d.radius) dir = [mx / len, my / len];
          }
          this.aims.push({ t: o.ht ?? o.t, dx, dy, dir, res: o.k === "c" ? o.res : o.hr });
        }
        before = o.k === "s" ? { x: o.path[o.rep % 2 === 0 ? 0 : o.path.length - 1][0], y: o.path[o.rep % 2 === 0 ? 0 : o.path.length - 1][1] } : o;
      }
      this.aims.sort((a, b) => a.t - b.t);
      this.aimTimes = this.aims.map(a => a.t);
      this.aimTurned = false;
      this.stripSpan = STRIP_DEFAULT;
      this.judged = objs.filter(o => o.res != null).slice().sort((a, b) => a.jt - b.jt);
      this.judgedTimes = this.judged.map(o => o.jt);
      this.hits = objs.filter(o => o.k === "c" && o.ht != null && o.res > 0).map(o => ({ t: o.ht, err: o.ht - o.t, res: o.res }))
        .sort((a, b) => a.t - b.t);
      this.hitTimes = this.hits.map(h => h.t);
      this.start = Math.min(objs.length ? objs[0].t - 1500 : 0, f.t.length ? f.t[0] : 0);
      this.start = Math.max(this.start, objs.length ? objs[0].t - 3000 : 0);
      this.end = Math.max(objs.length ? objs[objs.length - 1].e + 1500 : 0, 1);
    }

    build() {
      const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
      this.root.innerHTML = "";
      this.root.classList.add("viewer");
      this.stage = el("div", "viewer-stage");
      this.canvas = el("canvas");
      this.stage.appendChild(this.canvas);
      this.stage.addEventListener("click", () => this.toggle());
      const bar = el("div", "viewer-bar");
      this.playBtn = el("button", "btn primary play-btn", "");
      this.playBtn.title = "Play / pause (space)";
      this.playBtn.addEventListener("click", () => this.toggle());
      this.timeLbl = el("div", "time");
      this.tl = el("div", "timeline");
      this.tlCanvas = el("canvas");
      this.tl.appendChild(this.tlCanvas);
      let dragging = false;
      const seekAt = e => {
        const r = this.tl.getBoundingClientRect();
        this.seek(this.start + clamp((e.clientX - r.left) / r.width, 0, 1) * (this.end - this.start));
      };
      this.tl.addEventListener("pointerdown", e => { dragging = true; this.tl.setPointerCapture(e.pointerId); this.loop = null; this.syncLoop(); seekAt(e); });
      this.tl.addEventListener("pointermove", e => { if (dragging) seekAt(e); });
      this.tl.addEventListener("pointerup", () => { dragging = false; });
      // playback speed: a slider that stops on the ticks
      const speeds = el("div", "speed-slider");
      const range = el("input");
      Object.assign(range, { type: "range", min: 0, max: SPEEDS.length - 1, step: 1, value: SPEEDS.length - 1 });
      const speedLbl = el("span", "speed-val", "1x");
      const ticks = el("div", "speed-ticks");
      for (const v of SPEEDS) ticks.appendChild(el("span", "", `${v}`));
      range.addEventListener("input", () => { this.speed = SPEEDS[+range.value]; speedLbl.textContent = `${this.speed}x`; });
      speeds.append(el("div", "speed-track"), speedLbl);
      speeds.firstChild.append(range, ticks);
      this.loopChk = el("label", "check small hidden", `<input type="checkbox" checked> loop section`);
      this.loopChk.querySelector("input").addEventListener("change", e => { if (!e.target.checked) { this.loop = null; this.syncLoop(); } });
      // the input strip's scrolling speed: + scrolls faster (a shorter stretch of time), - slower
      const strip = this.stripControls = el("div", "strip-speed");
      strip.addEventListener("click", e => e.stopPropagation());
      const stepBtn = (text, title, d) => {
        const btn = el("button", "btn sm icon-btn", text);
        btn.title = title;
        btn.addEventListener("click", e => {
          e.stopPropagation();
          this.stripSpan = clamp(this.stripSpan + d, 0, STRIP_SPANS.length - 1);
          this.draw();
        });
        return btn;
      };
      strip.append(stepBtn("+", "Input strip: faster scrolling", -1), stepBtn("−", "Input strip: slower scrolling", 1));
      this.volumes = { music: 50, hitsounds: 50 };
      try { Object.assign(this.volumes, JSON.parse(localStorage.getItem("osucoach.volumes") || "{}")); } catch { }
      const volumes = el("div", "volumes");
      for (const [key, label] of [["music", "music"], ["hitsounds", "hitsounds"]]) {
        const range = el("input");
        Object.assign(range, { type: "range", min: 0, max: 100, step: 5, value: this.volumes[key], title: `${label} volume` });
        const val = el("span", "vol-val", `${this.volumes[key]}%`);
        range.addEventListener("input", () => {
          this.volumes[key] = +range.value;
          val.textContent = `${range.value}%`;
          if (key === "music" && this.music) this.music.volume = gain(this.volumes.music);
          if (key === "hitsounds" && this.skin) this.skin.volume = gain(this.volumes.hitsounds);
          try { localStorage.setItem("osucoach.volumes", JSON.stringify(this.volumes)); } catch { }
        });
        const row = el("label", "vol", `<span>${label}</span>`);
        row.append(range, val);
        volumes.append(row);
      }
      this.aimChk = el("label", "check small", `<input type="checkbox"> aim meter along the movement`);
      this.aimChk.title = "Turn every click so the movement into the note points up: below the centre = short, above = past it";
      this.aimChk.querySelector("input").addEventListener("change", e => { this.aimTurned = e.target.checked; this.draw(); });
      const help = el("div", "small muted", `<span class="kbd">space</span> play · <span class="kbd">← →</span> 1 s · <span class="kbd">,</span> <span class="kbd">.</span> frame`);
      this.stage.appendChild(strip);
      bar.append(this.playBtn, this.timeLbl, this.tl, speeds, volumes, this.loopChk, this.aimChk, help);
      this.root.append(this.stage, bar);
      this.syncButton();
    }

    destroy() {
      this.dead = true;
      if (this.music) { this.music.pause(); this.music.src = ""; }
      document.removeEventListener("keydown", this.onKey);
      this.ro.disconnect();
    }

    resize() {
      const dpr = window.devicePixelRatio || 1;
      for (const [c, host] of [[this.canvas, this.stage], [this.tlCanvas, this.tl]]) {
        const r = host.getBoundingClientRect();
        c.width = Math.max(1, Math.round(r.width * dpr));
        c.height = Math.max(1, Math.round(r.height * dpr));
      }
      this.drawTimelineBase();
      this.draw();
    }

    onKey(e) {
      if (!this.root.isConnected) return;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (e.code === "Space") { e.preventDefault(); this.toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); this.seek(this.now - 1000); }
      else if (e.key === "ArrowRight") { e.preventDefault(); this.seek(this.now + 1000); }
      else if (e.key === "," || e.key === ".") {
        e.preventDefault();
        this.pause();
        const i = lowerBound(this.ft, this.now);
        this.seek(e.key === "," ? this.ft[Math.max(0, i - 1)] : this.ft[Math.min(this.ft.length - 1, i + 1)]);
      }
    }

    toggle() { this.playing ? this.pause() : this.play(); }
    play() {
      if (this.now >= this.end) this.now = this.start;
      this.playing = true; this.last = null; this.syncButton();
      this.skin?.audio?.resume?.();   // browsers start audio only after a click
    }
    pause() { this.playing = false; this.syncButton(); }
    syncButton() {
      this.playBtn.innerHTML = this.playing
        ? `<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>`
        : `<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>`;
    }
    syncLoop() { this.loopChk.classList.toggle("hidden", !this.loop); if (this.loop) this.loopChk.querySelector("input").checked = true; }

    seek(t) { this.now = clamp(t, this.start, this.end); this.soundDone = null; this.draw(); }

    /** Music offset in ms from Settings (opts.musicOffset, read live): positive = the song earlier. */
    get musicOffset() { return clamp(+(this.opts.musicOffset?.() ?? 0) || 0, -100, 100); }

    /** Play a section from a little before it, looping it until the timeline is used. */
    focus(start, end) {
      this.loop = [Math.max(this.start, start), Math.min(this.end, end)];
      this.syncLoop();
      this.seek(this.loop[0]);
      this.play();
    }

    frame(ts) {
      if (this.dead) return;
      if (this.playing) {
        if (this.last != null) {
          const dt = Math.min(ts - this.last, 100);
          this.now += dt * this.d.rate * this.speed;
          this.followMusic();
          // hitsounds between the last one played and now (the clock may step back a little to follow the song)
          const from = this.soundDone ?? this.now;
          if (this.skin && this.volumes.hitsounds > 0 && this.now > from) {
            for (let i = lowerBound(this.soundTimes, from + 1e-6); i < this.sounds.length && this.soundTimes[i] <= this.now; i++)
              this.skin.play(this.sounds[i][1], this.sounds[i][2]);
          }
          this.soundDone = Math.max(this.soundDone ?? this.now, this.now);
          if (this.loop && this.now > this.loop[1]) { this.now = this.loop[0]; this.soundDone = null; }
          if (this.now >= this.end) { this.now = this.end; this.pause(); }
        }
        this.last = ts;
        this.draw();
      } else {
        this.last = null;
      }
      this.syncMusic();
      requestAnimationFrame(this.frame);
    }

    /** Keep the song where the replay is: playing at the replay's speed, re-seeked when it drifts. */
    syncMusic() {
      const m = this.music;
      if (!m) return;
      const rate = this.d.rate * this.speed;
      const on = this.playing && this.now >= 0 && (!m.duration || this.now < m.duration * 1000) && this.volumes.music > 0;
      if (!on) { if (!m.paused) m.pause(); return; }
      if (Math.abs(m.playbackRate - rate) > 1e-3) m.playbackRate = clamp(rate, 0.0625, 16);
      if (m.paused) {
        m.currentTime = (this.now + this.musicOffset) / 1000;
        m.play().catch(() => { });
      }
    }

    /** While the song plays the replay follows it (the song takes a moment to start after a play or a seek),
        so what's drawn, the hitsounds and the music stay together; a big gap is a seek: the song jumps there. */
    followMusic() {
      const m = this.music;
      if (!m || m.paused || m.seeking || m.readyState < 3) return;
      const err = m.currentTime * 1000 - this.musicOffset - this.now;
      if (Math.abs(err) > MUSIC_DRIFT * Math.max(1, this.d.rate * this.speed)) m.currentTime = (this.now + this.musicOffset) / 1000;
      else this.now += err * .25;
    }

    // --- drawing ---------------------------------------------------------------------------------------------

    cursorAt(t) {
      const f = this.d.frames, i = lowerBound(f.t, t);
      if (i <= 0) return { x: f.x[0], y: f.y[0], k: f.k[0] & 3 };
      if (i >= f.t.length) { const j = f.t.length - 1; return { x: f.x[j], y: f.y[j], k: f.k[j] & 3 }; }
      const a = i - 1, span = f.t[i] - f.t[a], p = span > 0 ? (t - f.t[a]) / span : 0;
      return { x: f.x[a] + (f.x[i] - f.x[a]) * p, y: f.y[a] + (f.y[i] - f.y[a]) * p, k: f.k[a] & 3 };
    }

    ballAt(o, t) {
      const span = (o.e - o.t) / o.rep;
      let p = clamp((t - o.t) / span, 0, o.rep);
      let rep = Math.min(Math.floor(p), o.rep - 1);
      let q = p - rep;
      if (rep % 2 === 1) q = 1 - q;
      const path = o.path, x = q * (path.length - 1), i = Math.floor(x), j = Math.min(i + 1, path.length - 1), k = x - i;
      return { x: path[i][0] + (path[j][0] - path[i][0]) * k, y: path[i][1] + (path[j][1] - path[i][1]) * k };
    }

    draw() {
      const c = this.canvas, ctx = c.getContext("2d"), d = this.d, now = this.now;
      const cw = c.width, ch = c.height;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      // background: a faint playfield grid
      const bg = ctx.createRadialGradient(cw / 2, ch / 2, 0, cw / 2, ch / 2, Math.max(cw, ch) * .7);
      bg.addColorStop(0, "#1a1220"); bg.addColorStop(1, "#0b080e");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, cw, ch);
      const top = TOP_STRIP * (window.devicePixelRatio || 1);
      const pad = 36, s = Math.min(cw / (W + pad * 2), (ch - top) / (H + pad * 2 + 30));
      const ox = (cw - W * s) / 2, oy = top + (ch - top - H * s) / 2 - 8 * s;
      ctx.setTransform(s, 0, 0, s, ox, oy);
      ctx.strokeStyle = "rgba(255,255,255,.05)";
      ctx.lineWidth = 1 / s;
      ctx.strokeRect(0, 0, W, H);
      ctx.beginPath();
      for (let x = 64; x < W; x += 64) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
      for (let y = 64; y < H; y += 64) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
      ctx.strokeStyle = "rgba(255,255,255,.022)"; ctx.stroke();

      const r = d.radius, pre = d.preempt, fadeIn = d.fadein;
      const objs = d.objects;
      const from = lowerBound(this.times, now - this.maxDur - 800);
      const to = lowerBound(this.times, now + pre + 1);
      // objects: later ones underneath, so draw them first
      for (let n = to - 1; n >= from; n--) {
        const o = objs[n];
        const appear = o.t - pre;
        if (now < appear) continue;
        const alphaIn = clamp((now - appear) / fadeIn, 0, 1);
        if (o.k === "p") { this.drawSpinner(ctx, o, now); continue; }
        if (o.k === "s") {
          const endVis = o.e + 180;
          if (now > endVis) continue;
          const a = now > o.e ? 1 - (now - o.e) / 180 : alphaIn;
          this.drawSlider(ctx, o, now, a * 0.9);
        }
        // head / circle
        let gone = o.k === "s" ? (o.hr === 0 ? o.t + d.windows[2] : o.ht ?? o.t) : o.jt;
        if (o.k === "c" || now < gone + 200) {
          if (now <= gone + 200) {
            const out = now > gone ? (now - gone) / 200 : 0;
            const scale = 1 + (now > gone && (o.k === "c" ? o.res > 0 : o.hr > 0) ? out * .35 : 0);
            this.drawCircle(ctx, o.x, o.y, r * scale, o.color, alphaIn * (1 - out), o.num,
              now > gone && (o.k === "c" ? o.res === 0 : o.hr === 0), o.k === "s");
          }
        }
        // approach circle
        if (now < o.t && this.skin?.has("approachcircle")) {
          const p = (now - appear) / pre;
          this.skin.sprite(ctx, "approachcircle", o.x, o.y, r / 64 * (1 + 3 * (1 - p)), alphaIn * .9, o.color);
        } else if (now < o.t) {
          const p = (now - appear) / pre;
          ctx.globalAlpha = alphaIn * .9;
          ctx.beginPath(); ctx.arc(o.x, o.y, r * (1 + 3 * (1 - p)), 0, Math.PI * 2);
          ctx.strokeStyle = o.color; ctx.lineWidth = 3; ctx.stroke();
          ctx.globalAlpha = 1;
        }
      }
      this.drawJudgements(ctx, now, r);
      this.drawClicks(ctx, now);
      this.drawCursor(ctx, now);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.drawHud(ctx, cw, ch, now);
      this.drawPlayhead();
      this.timeLbl.textContent = `${fmt(now)} / ${fmt(this.end)}`;
    }

    drawCircle(ctx, x, y, r, color, alpha, num, missed, head = false) {
      if (alpha <= 0) return;
      const sk = this.skin;
      if (sk?.has("hitcircle")) {
        const cs = r / 64;
        const base = head && sk.has("sliderstartcircle") ? "sliderstartcircle" : "hitcircle";
        const over = head && sk.has("sliderstartcircleoverlay") ? "sliderstartcircleoverlay" : "hitcircleoverlay";
        sk.sprite(ctx, base, x, y, cs, alpha, color);
        if (!sk.number(ctx, num, x, y, cs * .8, alpha)) {
          ctx.globalAlpha = alpha; ctx.fillStyle = "#fff";
          ctx.font = `700 ${Math.round(r * .78)}px "Segoe UI", sans-serif`;
          ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(String(num), x, y + r * .04);
          ctx.globalAlpha = 1;
        }
        sk.sprite(ctx, over, x, y, cs, alpha);
        if (missed) {
          ctx.globalAlpha = alpha; ctx.beginPath(); ctx.arc(x, y, r * .95, 0, Math.PI * 2);
          ctx.strokeStyle = "#ff4d6a"; ctx.lineWidth = r * .1; ctx.stroke(); ctx.globalAlpha = 1;
        }
        return;
      }
      ctx.globalAlpha = alpha;
      ctx.beginPath(); ctx.arc(x, y, r * .93, 0, Math.PI * 2);
      const g = ctx.createRadialGradient(x, y - r * .3, r * .1, x, y, r);
      g.addColorStop(0, color); g.addColorStop(1, shade(color, -.45));
      ctx.fillStyle = g; ctx.fill();
      ctx.lineWidth = r * .12; ctx.strokeStyle = missed ? "#ff4d6a" : "#fff"; ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.font = `700 ${Math.round(r * .78)}px "Segoe UI", sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(num), x, y + r * .04);
      ctx.globalAlpha = 1;
    }

    drawSlider(ctx, o, now, alpha) {
      const r = this.d.radius, path = o.path;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(path[0][0], path[0][1]);
      for (let i = 1; i < path.length; i++) ctx.lineTo(path[i][0], path[i][1]);
      const ini = this.skin?.ini || {}, rgb = c => c && `rgb(${c[0]},${c[1]},${c[2]})`;
      ctx.strokeStyle = o.sb != null && now >= o.sb ? "#ff4d6a" : rgb(ini.slider_border) || "rgba(255,255,255,.95)";
      ctx.lineWidth = r * 2; ctx.stroke();
      if (ini.slider_track) {   // the skin's own track colour
        ctx.strokeStyle = rgb(ini.slider_track); ctx.lineWidth = r * 1.76; ctx.stroke();
      } else {
        ctx.strokeStyle = shade(o.color, -.62); ctx.lineWidth = r * 1.8; ctx.stroke();
        ctx.strokeStyle = shade(o.color, -.5); ctx.lineWidth = r * 1.1; ctx.stroke();
      }
      // reverse arrows while repeats remain
      if (o.rep > 1 && now < o.e) {
        const span = (o.e - o.t) / o.rep, done = Math.max(0, Math.floor((now - o.t) / span));
        if (done < o.rep - 1) {
          const atEnd = done % 2 === 0, p = atEnd ? path[path.length - 1] : path[0], q = atEnd ? path[Math.max(0, path.length - 3)] : path[Math.min(path.length - 1, 2)];
          const ang = Math.atan2(q[1] - p[1], q[0] - p[0]);
          if (this.skin?.has("reversearrow")) {
            ctx.restore();
            this.skin.sprite(ctx, "reversearrow", p[0], p[1], r / 64, alpha, null, ang);
            ctx.save();
          } else {
          ctx.translate(p[0], p[1]); ctx.rotate(ang);
          ctx.beginPath(); ctx.moveTo(-r * .35, -r * .45); ctx.lineTo(r * .3, 0); ctx.lineTo(-r * .35, r * .45);
          ctx.strokeStyle = "#fff"; ctx.lineWidth = r * .16; ctx.stroke();
          }
        }
      }
      ctx.restore();
      // ball and follow circle while the slider runs
      if (now >= o.t && now <= o.e) {
        const b = this.ballAt(o, now);
        const ball = this.skin?.has("sliderb0") ? "sliderb0" : this.skin?.has("sliderb") ? "sliderb" : null;
        if (ball) {
          this.skin.sprite(ctx, ball, b.x, b.y, r / 64, 1);
          if (this.skin.has("sliderfollowcircle") && !(o.sb != null && now >= o.sb))
            this.skin.sprite(ctx, "sliderfollowcircle", b.x, b.y, r / 64, 1);
          return;
        }
        ctx.globalAlpha = 1;
        ctx.beginPath(); ctx.arc(b.x, b.y, r * .85, 0, Math.PI * 2);
        ctx.fillStyle = o.color; ctx.fill();
        ctx.lineWidth = 3; ctx.strokeStyle = "#fff"; ctx.stroke();
        ctx.beginPath(); ctx.arc(b.x, b.y, r * 2.4, 0, Math.PI * 2);
        ctx.strokeStyle = o.sb != null && now >= o.sb ? "rgba(255,77,106,.8)" : "rgba(255,255,255,.55)";
        ctx.lineWidth = 2.5; ctx.stroke();
      }
    }

    drawSpinner(ctx, o, now) {
      if (now > o.e + 200) return;
      const a = now < o.t ? clamp((now - (o.t - 400)) / 400, 0, 1) : now > o.e ? 1 - (now - o.e) / 200 : 1;
      if (a <= 0) return;
      const p = clamp((now - o.t) / (o.e - o.t), 0, 1);
      ctx.globalAlpha = a;
      ctx.beginPath(); ctx.arc(256, 192, 170, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 4; ctx.stroke();
      ctx.beginPath(); ctx.arc(256, 192, 170 * (1 - p) + 8, 0, Math.PI * 2);
      ctx.strokeStyle = "#ff66aa"; ctx.lineWidth = 3; ctx.stroke();
      ctx.beginPath(); ctx.arc(256, 192, 6, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill();
      ctx.globalAlpha = 1;
    }

    drawJudgements(ctx, now, r) {
      const from = lowerBound(this.judgedTimes, now - JUDGE_MS), to = lowerBound(this.judgedTimes, now + 1);
      for (let i = from; i < to; i++) {
        const o = this.judged[i];
        if (o.res === 300) continue;
        const age = (now - o.jt) / JUDGE_MS, a = age < .7 ? 1 : 1 - (age - .7) / .3;
        const { x, y } = o.jx;
        if (this.skin?.has(`hit${o.res}`)) {
          this.skin.sprite(ctx, `hit${o.res}`, x, y - age * r * .3, r / 64 * .8, a);
          continue;
        }
        ctx.globalAlpha = a;
        if (o.res === 0) {
          const k = r * .45 * (1 + age * .2);
          ctx.strokeStyle = RES_COLOR[0]; ctx.lineWidth = r * .2; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(x - k, y - k); ctx.lineTo(x + k, y + k); ctx.moveTo(x + k, y - k); ctx.lineTo(x - k, y + k); ctx.stroke();
        } else {
          ctx.fillStyle = RES_COLOR[o.res];
          ctx.font = `800 ${Math.round(r * .8)}px "Segoe UI", sans-serif`;
          ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText(String(o.res), x, y - age * r * .4);
        }
      }
      // slider breaks
      for (const o of this.d.objects) {
        if (o.sb == null || now < o.sb || now > o.sb + JUDGE_MS) continue;
        const b = this.ballAt(o, o.sb), age = (now - o.sb) / JUDGE_MS;
        ctx.globalAlpha = 1 - age;
        ctx.fillStyle = "#ff8f5a"; ctx.font = `700 ${Math.round(r * .5)}px "Segoe UI", sans-serif`;
        ctx.textAlign = "center"; ctx.fillText("sb", b.x, b.y - r * 1.3);
      }
      ctx.globalAlpha = 1;
    }

    drawClicks(ctx, now) {
      const from = lowerBound(this.pressTimes, now - CLICK_MARK_MS), to = lowerBound(this.pressTimes, now + 1);
      for (let i = from; i < to; i++) {
        const p = this.presses[i], age = (now - p.t) / CLICK_MARK_MS;
        ctx.globalAlpha = 1 - age;
        ctx.beginPath(); ctx.arc(p.x, p.y, 4 + age * 6, 0, Math.PI * 2);
        ctx.strokeStyle = KEY_COLOR[p.key]; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    drawCursor(ctx, now) {
      const f = this.d.frames;
      if (!f.t.length) return;
      const sk = this.skin;
      if (sk?.has("cursor")) {
        const centred = sk.ini.cursor_centre !== false;
        if (sk.has("cursortrail")) {
          for (let i = Math.max(0, lowerBound(f.t, now - TRAIL_MS)); i < f.t.length && f.t[i] <= now; i++)
            sk.sprite(ctx, "cursortrail", f.x[i], f.y[i], .8, clamp(1 - (now - f.t[i]) / TRAIL_MS, 0, 1) * .8, null, 0, centred);
        }
        const cur = this.cursorAt(now);
        sk.sprite(ctx, "cursor", cur.x, cur.y, cur.k ? .9 : .8, 1, null, 0, centred);
        if (sk.has("cursormiddle")) sk.sprite(ctx, "cursormiddle", cur.x, cur.y, .8, 1, null, 0, centred);
        return;
      }
      const from = Math.max(0, lowerBound(f.t, now - TRAIL_MS) - 1), to = lowerBound(f.t, now);
      ctx.lineCap = "round";
      let px = null, py = null;
      for (let i = from; i < to; i++) {
        const age = (now - f.t[i]) / TRAIL_MS;
        if (px !== null) {
          ctx.globalAlpha = clamp(1 - age, 0, 1) * .9;
          ctx.strokeStyle = KEY_COLOR[f.k[i] & 3];
          ctx.lineWidth = 3.2 * (1 - age * .6);
          ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(f.x[i], f.y[i]); ctx.stroke();
        }
        px = f.x[i]; py = f.y[i];
      }
      const cur = this.cursorAt(now);
      if (px !== null) {
        ctx.globalAlpha = .9; ctx.strokeStyle = KEY_COLOR[cur.k]; ctx.lineWidth = 3.2;
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(cur.x, cur.y); ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.arc(cur.x, cur.y, cur.k ? 8 : 6.5, 0, Math.PI * 2);
      ctx.fillStyle = cur.k ? KEY_COLOR[cur.k] : "#ffd1e6"; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
      ctx.shadowBlur = 0;
    }

    drawHud(ctx, cw, ch, now) {
      const dpr = window.devicePixelRatio || 1, u = dpr;
      // counts so far
      const upto = lowerBound(this.judgedTimes, now + 1), counts = { 300: 0, 100: 0, 50: 0, 0: 0 };
      for (let i = 0; i < upto; i++) counts[this.judged[i].res] = (counts[this.judged[i].res] || 0) + 1;
      ctx.font = `700 ${13 * u}px "Cascadia Mono", Consolas, monospace`;
      ctx.textBaseline = "top"; ctx.textAlign = "left";
      let x = 14 * u;
      for (const k of [300, 100, 50, 0]) {
        const label = k === 0 ? "miss" : String(k);
        ctx.fillStyle = RES_COLOR[k];
        ctx.fillText(`${counts[k]}×${label}`, x, 12 * u);
        x += ctx.measureText(`${counts[k]}×${label}`).width + 14 * u;
      }
      // keys
      const pUpTo = lowerBound(this.pressTimes, now + 1);
      let k1 = 0, k2 = 0;
      for (let i = 0; i < pUpTo; i++) this.presses[i].key === 1 ? k1++ : k2++;
      const cur = this.cursorAt(now);
      const boxes = [["K1", 1, k1], ["K2", 2, k2]];
      boxes.forEach(([name, bit, count], i) => {
        const bw = 58 * u, bh = 38 * u, bx = cw - bw - 12 * u, by = ch / 2 - bh - 4 * u + i * (bh + 8 * u);
        const on = cur.k & bit;
        ctx.fillStyle = on ? KEY_COLOR[bit] : "rgba(255,255,255,.06)";
        roundRect(ctx, bx, by, bw, bh, 8 * u); ctx.fill();
        ctx.strokeStyle = on ? "#fff" : "rgba(255,255,255,.18)"; ctx.lineWidth = 1.5 * u; ctx.stroke();
        ctx.fillStyle = on ? "#15101a" : "#cdbfd6";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.font = `700 ${11 * u}px "Segoe UI", sans-serif`;
        ctx.fillText(name, bx + bw / 2, by + bh * .32);
        ctx.font = `600 ${12 * u}px "Cascadia Mono", Consolas, monospace`;
        ctx.fillText(String(count), bx + bw / 2, by + bh * .7);
      });
      // the - and + buttons right after the counts, the strip right after them
      const controls = Math.max(x + 24 * u, cw * .34);
      const left = `${Math.round(controls / u)}px`;
      if (this.stripControls.style.left !== left) this.stripControls.style.left = left;
      this.drawKeyStrip(ctx, cw, now, u, controls + (STRIP_CONTROLS + 8) * u);
      this.drawAimMeter(ctx, cw, ch, now, u);
      // hit error bar
      const w = this.d.windows, bwid = Math.min(cw * .42, 380 * u), scale = bwid / 2 / w[2];
      const cx = cw / 2, cy = ch - 16 * u;
      ctx.globalAlpha = .9;
      for (const [win, col] of [[w[2], RES_COLOR[50]], [w[1], RES_COLOR[100]], [w[0], RES_COLOR[300]]]) {
        ctx.fillStyle = col; ctx.globalAlpha = .35;
        ctx.fillRect(cx - win * scale, cy - 2 * u, win * 2 * scale, 4 * u);
      }
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#fff"; ctx.fillRect(cx - 1 * u, cy - 9 * u, 2 * u, 18 * u);
      const hFrom = lowerBound(this.hitTimes, now - ERROR_BAR_MS), hTo = lowerBound(this.hitTimes, now + 1);
      let sum = 0, n = 0;
      for (let i = hFrom; i < hTo; i++) {
        const hh = this.hits[i], age = (now - hh.t) / ERROR_BAR_MS;
        ctx.globalAlpha = (1 - age) * .9;
        ctx.fillStyle = RES_COLOR[hh.res];
        ctx.fillRect(cx + hh.err * scale - 1.2 * u, cy - 8 * u, 2.4 * u, 16 * u);
        sum += hh.err; n++;
      }
      if (n) {
        ctx.globalAlpha = 1; ctx.fillStyle = "#fff";
        const mx = cx + (sum / n) * scale;
        ctx.beginPath(); ctx.moveTo(mx, cy - 12 * u); ctx.lineTo(mx - 5 * u, cy - 19 * u); ctx.lineTo(mx + 5 * u, cy - 19 * u); ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.font = `600 ${10.5 * u}px "Segoe UI", sans-serif`; ctx.fillStyle = "rgba(255,255,255,.45)";
      ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillText("early", cx - bwid / 2 - 8 * u, cy);
      ctx.textAlign = "left"; ctx.fillText("late", cx + bwid / 2 + 8 * u, cy);
      // section label
      const sec = this.sections.find(s => now >= s.start && now <= s.end);
      if (sec) {
        ctx.font = `600 ${12.5 * u}px "Segoe UI", sans-serif`;
        const txt = sec.label.length > 90 ? sec.label.slice(0, 88) + "…" : sec.label;
        const tw = ctx.measureText(txt).width + 22 * u;
        ctx.fillStyle = "rgba(255,102,170,.18)"; roundRect(ctx, 12 * u, 72 * u, tw, 26 * u, 13 * u); ctx.fill();
        ctx.strokeStyle = "rgba(255,102,170,.5)"; ctx.lineWidth = 1 * u; ctx.stroke();
        ctx.fillStyle = "#ffb3d4"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
        ctx.fillText(txt, 23 * u, 85 * u);
      }
    }

    /** Rewind-style input strip at the top: K1 and K2 held down as bars, the notes' times as ticks coloured by
        result, scrolling past a playhead. */
    drawKeyStrip(ctx, cw, now, u, x0) {
      const rate = this.d.rate, past = STRIP_SPANS[this.stripSpan] * rate, ahead = past / 4;
      // K1 and K2 side by side, the notes' lane over the line between them (half on each)
      const x1 = cw - 18 * u, top = 10 * u, lane = 16 * u, noteLane = 16 * u, gap = 2 * u;
      const lanes = { k1: top, k2: top + lane + gap, notes: top + lane + gap / 2 - noteLane / 2 };
      const bottom = lanes.k2 + lane;
      const X = t => x0 + (t - (now - past)) / (past + ahead) * (x1 - x0);
      const inside = x => Math.max(x0, Math.min(x1, x));
      ctx.fillStyle = "rgba(255,255,255,.05)";
      for (const y of [lanes.k1, lanes.k2]) { roundRect(ctx, x0, y, x1 - x0, lane, 3 * u); ctx.fill(); }
      const from = lowerBound(this.times, now - past - this.maxDur), to = lowerBound(this.times, now + ahead);
      const notes = [];
      for (let i = from; i < to; i++) {
        const o = this.d.objects[i];
        if (o.k === "p" || (o.k === "s" ? o.e : o.t) < now - past) continue;
        const res = o.k === "s" ? (o.hr === 0 ? 0 : o.res) : o.res;
        notes.push({ o, color: o.t > now ? "#ffffff" : RES_COLOR[res] ?? "#ffffff" });
      }
      // the keys held down: white, see-through, so the notes' colours stand out
      [["k1", 1], ["k2", 2]].forEach(([name, bit]) => {
        const y = lanes[name], runs = this.keyRuns[bit];
        for (let j = Math.max(0, lowerBound(this.keyStarts[bit], now - past) - 1); j < runs.length && runs[j][0] <= now + ahead; j++) {
          const [a, b] = runs[j];
          if (b < now - past) continue;
          const xa = inside(X(a)), xb = Math.min(X(Math.min(b, now)), x1);
          if (xb <= xa) continue;
          ctx.fillStyle = "rgba(255,255,255,.32)";
          roundRect(ctx, xa, y, Math.max(xb - xa, 2 * u), lane, 3 * u); ctx.fill();
        }
      });
      // the notes, as in Rewind: a line per circle, a bar as long as the slider (it has to be held that long),
      // coloured by result; the ones still to come in white
      // drawn over the key presses, across the line between K1 and K2
      const tick = 3 * u, cy = lanes.notes + noteLane / 2, inset = 0;
      for (const { o, color } of notes) {
        const xh = X(o.t);
        if (o.k === "s") {
          const xa = inside(xh), xb = inside(X(o.e));
          if (xb > xa) {   // drawn like a key press: a rounded, see-through bar as tall as a lane
            ctx.fillStyle = color; ctx.globalAlpha = .35;
            roundRect(ctx, xa, lanes.notes, xb - xa, noteLane, 3 * u); ctx.fill();
            ctx.globalAlpha = 1;
          }
          if (o.sb != null && o.sb >= now - past && o.sb <= now) {   // where the slider was dropped
            ctx.fillStyle = "#ff8f5a";
            ctx.fillRect(X(o.sb) - tick / 2, lanes.notes + inset, tick, noteLane - 2 * inset);
          }
        }
        if (xh < x0 || xh > x1) continue;
        ctx.fillStyle = color;
        ctx.fillRect(xh - tick / 2, lanes.notes + inset, tick, noteLane - 2 * inset);
      }
      ctx.fillStyle = "#fff";
      ctx.fillRect(X(now) - 1 * u, top - 3 * u, 2 * u, bottom - top + 6 * u);
    }

    /** danser-style aim error meter: where the recent clicks landed around the circle centre (the ring is the circle's
        edge), fading with age, and their mean; optionally turned so the movement into each note points up. */
    drawAimMeter(ctx, cw, ch, now, u) {
      const R = 48 * u, cx = cw - R - 18 * u, cy = ch - R - 40 * u;
      ctx.fillStyle = "rgba(11,8,14,.75)"; ctx.beginPath(); ctx.arc(cx, cy, R * 1.25, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 1.5 * u;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,.12)"; ctx.lineWidth = 1 * u;
      ctx.beginPath(); ctx.arc(cx, cy, R / 2, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
      if (this.aimTurned) {   // the direction of the movement
        ctx.fillStyle = "rgba(255,255,255,.35)";
        ctx.beginPath(); ctx.moveTo(cx, cy - R * 1.2); ctx.lineTo(cx - 4 * u, cy - R * 1.2 + 7 * u); ctx.lineTo(cx + 4 * u, cy - R * 1.2 + 7 * u); ctx.fill();
      }
      const span = AIM_MS * this.d.rate;
      const to = lowerBound(this.aimTimes, now + 1), from = Math.max(lowerBound(this.aimTimes, now - span), to - AIM_MAX);
      let sx = 0, sy = 0, n = 0;
      for (let i = from; i < to; i++) {
        const a = this.aims[i];
        let dx = a.dx, dy = a.dy;
        if (this.aimTurned) {
          if (!a.dir) continue;
          const [ux, uy] = a.dir;
          [dx, dy] = [dx * -uy + dy * ux, -(dx * ux + dy * uy)];
        }
        const px = cx + Math.max(-1.2, Math.min(1.2, dx)) * R, py = cy + Math.max(-1.2, Math.min(1.2, dy)) * R;
        ctx.globalAlpha = .25 + .75 * (1 - (now - a.t) / span);
        ctx.fillStyle = RES_COLOR[a.res];
        ctx.beginPath(); ctx.arc(px, py, 2.6 * u, 0, Math.PI * 2); ctx.fill();
        sx += dx; sy += dy; n++;
      }
      ctx.globalAlpha = 1;
      if (n) {
        const mx = cx + sx / n * R, my = cy + sy / n * R;
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 2 * u;
        ctx.beginPath(); ctx.moveTo(mx - 6 * u, my); ctx.lineTo(mx + 6 * u, my); ctx.moveTo(mx, my - 6 * u); ctx.lineTo(mx, my + 6 * u); ctx.stroke();
      }
      ctx.font = `600 ${10.5 * u}px "Segoe UI", sans-serif`; ctx.fillStyle = "rgba(255,255,255,.5)";
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      ctx.fillText(this.aimTurned ? "aim error · movement ↑" : "aim error", cx, cy + R * 1.25 + 4 * u);
    }

    drawTimelineBase() {
      const c = this.tlCanvas, ctx = c.getContext("2d"), w = c.width, h = c.height, span = this.end - this.start;
      const X = t => (t - this.start) / span * w;
      ctx.clearRect(0, 0, w, h);
      // note density
      const bins = Math.max(1, Math.floor(w / 3)), dens = new Array(bins).fill(0);
      for (const o of this.d.objects) dens[clamp(Math.floor((o.t - this.start) / span * bins), 0, bins - 1)]++;
      const mx = Math.max(...dens, 1);
      ctx.fillStyle = "rgba(255,255,255,.07)";
      dens.forEach((v, i) => { const bh = v / mx * h * .6; ctx.fillRect(i * w / bins, h - bh, w / bins, bh); });
      // sections the analysis talks about
      for (const s of this.sections) {
        ctx.fillStyle = "rgba(255,102,170,.16)";
        ctx.fillRect(X(s.start), 0, Math.max(2, X(s.end) - X(s.start)), h);
      }
      for (const o of this.d.objects) {
        if (o.sb != null) { ctx.fillStyle = "#ff8f5a"; ctx.fillRect(X(o.sb) - 1, h * .55, 2, h * .45); }
        if (o.res == null || o.res === 300) continue;
        ctx.fillStyle = RES_COLOR[o.res];
        const hh = o.res === 0 ? h : o.res === 50 ? h * .55 : h * .4;
        ctx.fillRect(X(o.jt) - (o.res === 0 ? 1 : .5), h - hh, o.res === 0 ? 2 : 1, hh);
      }
      this.tlBase = ctx.getImageData(0, 0, w, h);
    }

    drawPlayhead() {
      const c = this.tlCanvas, ctx = c.getContext("2d");
      if (!this.tlBase) return;
      ctx.putImageData(this.tlBase, 0, 0);
      const x = (this.now - this.start) / (this.end - this.start) * c.width;
      if (this.loop) {
        const a = (this.loop[0] - this.start) / (this.end - this.start) * c.width, b = (this.loop[1] - this.start) / (this.end - this.start) * c.width;
        ctx.strokeStyle = "rgba(255,143,195,.9)"; ctx.lineWidth = 2; ctx.strokeRect(a, 1, b - a, c.height - 2);
      }
      ctx.fillStyle = "#fff"; ctx.fillRect(x - 1, 0, 2, c.height);
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function shade(hex, k) {
    const n = parseInt(hex.slice(1), 16);
    let r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    const f = v => Math.round(k < 0 ? v * (1 + k) : v + (255 - v) * k);
    return `rgb(${f(r)},${f(g)},${f(b)})`;
  }

  window.ReplayViewer = ReplayViewer;
  window.SkinAssets = SkinAssets;
  window.fmtTime = fmt;
})();
