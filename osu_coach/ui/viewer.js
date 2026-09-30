/* Replay viewer in the spirit of Rewind: the playfield as it was played (stacked, HR-flipped objects, the
   cursor and its keys), judgements, a hit error bar and a timeline with the mistakes and the sections the
   analysis talks about. Map time is in ms; at 1x the map runs at the replay's own rate (DT 1.5x). */

(function () {
  const W = 512, H = 384;
  const COMBO = ["#ff66aa", "#66ccff", "#b98cff", "#ffcf5c"];
  const RES_COLOR = { 300: "#66ccff", 100: "#7ddc4f", 50: "#ffc93c", 0: "#ff4d6a" };
  const KEY_COLOR = { 0: "rgba(255,255,255,.9)", 1: "#ff66aa", 2: "#66ccff", 3: "#c77dff" };
  const TRAIL_MS = 140;
  const CLICK_MARK_MS = 450;
  const JUDGE_MS = 700;
  const ERROR_BAR_MS = 4000;

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
    }

    prepare() {
      const d = this.d, objs = d.objects;
      let color = -1, num = 0;
      this.maxDur = 0;
      for (const o of objs) {
        if (o.nc || color < 0 || o.k === "p") { color = (color + 1) % COMBO.length; num = 0; }
        num += 1;
        o.color = COMBO[color];
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
      const speeds = el("div", "seg");
      for (const s of [0.25, 0.5, 0.75, 1]) {
        const b = el("button", s === 1 ? "on" : "", `${s}x`);
        b.addEventListener("click", () => { this.speed = s; [...speeds.children].forEach(c => c.classList.toggle("on", c === b)); });
        speeds.appendChild(b);
      }
      this.loopChk = el("label", "check small hidden", `<input type="checkbox" checked> loop section`);
      this.loopChk.querySelector("input").addEventListener("change", e => { if (!e.target.checked) { this.loop = null; this.syncLoop(); } });
      const help = el("div", "small muted", `<span class="kbd">space</span> play · <span class="kbd">← →</span> 1 s · <span class="kbd">,</span> <span class="kbd">.</span> frame`);
      bar.append(this.playBtn, this.timeLbl, this.tl, speeds, this.loopChk, help);
      this.root.append(this.stage, bar);
      this.syncButton();
    }

    destroy() {
      this.dead = true;
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
    play() { if (this.now >= this.end) this.now = this.start; this.playing = true; this.last = null; this.syncButton(); }
    pause() { this.playing = false; this.syncButton(); }
    syncButton() {
      this.playBtn.innerHTML = this.playing
        ? `<svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>`
        : `<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>`;
    }
    syncLoop() { this.loopChk.classList.toggle("hidden", !this.loop); if (this.loop) this.loopChk.querySelector("input").checked = true; }

    seek(t) { this.now = clamp(t, this.start, this.end); this.draw(); }

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
          if (this.loop && this.now > this.loop[1]) this.now = this.loop[0];
          if (this.now >= this.end) { this.now = this.end; this.pause(); }
        }
        this.last = ts;
        this.draw();
      } else {
        this.last = null;
      }
      requestAnimationFrame(this.frame);
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
      const pad = 36, s = Math.min(cw / (W + pad * 2), ch / (H + pad * 2 + 30));
      const ox = (cw - W * s) / 2, oy = (ch - H * s) / 2 - 8 * s;
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
              now > gone && (o.k === "c" ? o.res === 0 : o.hr === 0));
          }
        }
        // approach circle
        if (now < o.t) {
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

    drawCircle(ctx, x, y, r, color, alpha, num, missed) {
      if (alpha <= 0) return;
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
      ctx.strokeStyle = o.sb != null && now >= o.sb ? "#ff4d6a" : "rgba(255,255,255,.95)";
      ctx.lineWidth = r * 2; ctx.stroke();
      ctx.strokeStyle = shade(o.color, -.62); ctx.lineWidth = r * 1.8; ctx.stroke();
      ctx.strokeStyle = shade(o.color, -.5); ctx.lineWidth = r * 1.1; ctx.stroke();
      // reverse arrows while repeats remain
      if (o.rep > 1 && now < o.e) {
        const span = (o.e - o.t) / o.rep, done = Math.max(0, Math.floor((now - o.t) / span));
        if (done < o.rep - 1) {
          const atEnd = done % 2 === 0, p = atEnd ? path[path.length - 1] : path[0], q = atEnd ? path[Math.max(0, path.length - 3)] : path[Math.min(path.length - 1, 2)];
          const ang = Math.atan2(q[1] - p[1], q[0] - p[0]);
          ctx.translate(p[0], p[1]); ctx.rotate(ang);
          ctx.beginPath(); ctx.moveTo(-r * .35, -r * .45); ctx.lineTo(r * .3, 0); ctx.lineTo(-r * .35, r * .45);
          ctx.strokeStyle = "#fff"; ctx.lineWidth = r * .16; ctx.stroke();
          ctx.setTransform(ctx.getTransform());
        }
      }
      ctx.restore();
      // ball and follow circle while the slider runs
      if (now >= o.t && now <= o.e) {
        const b = this.ballAt(o, now);
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
        ctx.textAlign = "center"; ctx.fillText("slider break", b.x, b.y - r * 1.3);
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
        ctx.fillStyle = "rgba(255,102,170,.18)"; roundRect(ctx, 12 * u, 34 * u, tw, 26 * u, 13 * u); ctx.fill();
        ctx.strokeStyle = "rgba(255,102,170,.5)"; ctx.lineWidth = 1 * u; ctx.stroke();
        ctx.fillStyle = "#ffb3d4"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
        ctx.fillText(txt, 23 * u, 47 * u);
      }
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
  window.fmtTime = fmt;
})();
