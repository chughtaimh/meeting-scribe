/* Meeting Scribe front-end — no frameworks, hash-routed single page app. */
"use strict";

/* ---------------- helpers ---------------- */
const $view = document.getElementById("view");
const $toast = document.getElementById("toast");
const $modalRoot = document.getElementById("modal-root");

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${m}:${String(s).padStart(2, "0")}`;
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const today = new Date(); const y = new Date(); y.setDate(today.getDate() - 1);
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay(d, today)) return `Today, ${time}`;
  if (sameDay(d, y)) return `Yesterday, ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" }) + `, ${time}`;
}
let toastTimer = null;
function toast(msg, isErr) {
  $toast.textContent = msg;
  $toast.className = "show" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $toast.className = ""; }, isErr ? 6000 : 2600);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* noop */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
function mdLite(md) {
  // Minimal, safe markdown rendering for AI summaries.
  const lines = String(md || "").split("\n");
  let out = "", inList = false;
  const inline = (t) => esc(t).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  for (const raw of lines) {
    const line = raw.trim();
    const li = line.match(/^[-*•]\s+(.*)/);
    if (li) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(li[1])}</li>`; continue; }
    if (inList) { out += "</ul>"; inList = false; }
    const hd = line.match(/^#{1,4}\s+(.*)/);
    if (hd) { out += `<h3>${inline(hd[1])}</h3>`; continue; }
    if (line) out += `<p>${inline(line)}</p>`;
  }
  if (inList) out += "</ul>";
  return out;
}
const SPEAKER_COLORS = ["#4f46e5", "#0e9384", "#dc6803", "#c11574", "#175cd3",
                        "#7839ee", "#3b7c0f", "#b42318", "#9c2bad", "#475467"];
function speakerColor(label, order) {
  const i = Math.max(0, order.indexOf(label));
  return SPEAKER_COLORS[i % SPEAKER_COLORS.length];
}

const I = {
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="11" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
};

/* ---------------- modal ---------------- */
function showModal(html) {
  $modalRoot.innerHTML = `<div class="modal-bg"><div class="modal">${html}</div></div>`;
  $modalRoot.querySelector(".modal-bg").addEventListener("click", (e) => {
    if (e.target.classList.contains("modal-bg")) closeModal();
  });
  return $modalRoot.querySelector(".modal");
}
function closeModal() { $modalRoot.innerHTML = ""; }

/* ---------------- recorder ---------------- */
const Rec = {
  active: false, paused: false, mode: "meeting", id: null,
  mediaStream: null, recorder: null, mime: "", startTs: 0, pausedTotal: 0,
  pauseStart: 0, uploadChain: Promise.resolve(), pendingUploads: 0,
  audioCtx: null, analyser: null, raf: 0, meterHistory: [], wakeLock: null,

  elapsed() {
    if (!this.startTs) return 0;
    const pausedExtra = this.paused ? (Date.now() - this.pauseStart) : 0;
    return (Date.now() - this.startTs - this.pausedTotal - pausedExtra) / 1000;
  },

  pickMime() {
    const cands = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"];
    for (const c of cands) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) return c;
    }
    return "";
  },

  async start(mode, deviceId) {
    if (this.active) throw new Error("Already recording");
    if (!navigator.mediaDevices || !window.MediaRecorder)
      throw new Error("This browser does not support audio recording.");
    const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false };
    if (deviceId) constraints.audio.deviceId = { exact: deviceId };
    this.mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
    const { id } = await api("/api/recordings/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    this.id = id; this.mode = mode;
    this.mime = this.pickMime();
    this.recorder = new MediaRecorder(this.mediaStream, this.mime ? { mimeType: this.mime, audioBitsPerSecond: 48000 } : undefined);
    this.mime = this.recorder.mimeType || this.mime || "audio/webm";
    this.uploadChain = Promise.resolve(); this.pendingUploads = 0;

    this.recorder.ondataavailable = (e) => {
      if (!e.data || !e.data.size) return;
      const blob = e.data;
      this.pendingUploads++;
      this.uploadChain = this.uploadChain.then(async () => {
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            await fetch(`/api/recordings/${this.id}/chunk`, { method: "POST", body: blob });
            break;
          } catch (err) {
            if (attempt === 2) toast("A chunk failed to save — check the app window is still running", true);
            await new Promise(r => setTimeout(r, 800 * (attempt + 1)));
          }
        }
      }).finally(() => { this.pendingUploads--; });
    };

    this.recorder.start(5000);  // flush every 5s — crash-safe
    this.active = true; this.paused = false;
    this.startTs = Date.now(); this.pausedTotal = 0;
    this.lastLoud = Date.now(); this.nudged = false;

    // level meter
    try {
      this.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = this.audioCtx.createMediaStreamSource(this.mediaStream);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      src.connect(this.analyser);
    } catch (e) { this.analyser = null; }

    try { this.wakeLock = await navigator.wakeLock?.request("screen"); } catch (e) {}
    window.onbeforeunload = () => "Recording in progress — leaving will stop it.";
  },

  pause() {
    if (!this.active || this.paused) return;
    this.recorder.pause(); this.paused = true; this.pauseStart = Date.now();
  },
  resume() {
    if (!this.active || !this.paused) return;
    this.recorder.resume(); this.paused = false;
    this.pausedTotal += Date.now() - this.pauseStart;
  },

  async stop(cancel) {
    if (!this.active) return null;
    const id = this.id, mode = this.mode, mime = this.mime;
    const duration = this.elapsed();
    this.active = false;
    window.onbeforeunload = null;

    document.title = "Meeting Scribe";
    const stopped = new Promise((res) => { this.recorder.onstop = res; });
    try { this.recorder.state !== "inactive" && this.recorder.stop(); } catch (e) {}
    await Promise.race([stopped, new Promise(r => setTimeout(r, 4000))]);
    this.mediaStream?.getTracks().forEach(t => t.stop());
    try { await this.wakeLock?.release(); } catch (e) {}
    try { this.audioCtx?.close(); } catch (e) {}
    cancelAnimationFrame(this.raf);
    this.analyser = null; this.recorder = null; this.mediaStream = null;
    this.id = null; this.startTs = 0;

    await this.uploadChain;             // drain pending chunk uploads
    if (cancel) return null;
    await api(`/api/recordings/${id}/finish`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, mime, duration }),
    });
    return id;
  },

  drawMeter(canvas) {
    if (!canvas) return;
    const ctx2 = canvas.getContext("2d");
    const W = canvas.width = canvas.offsetWidth * 2;
    const H = canvas.height = canvas.offsetHeight * 2;
    const data = new Uint8Array(this.analyser ? this.analyser.fftSize : 0);
    const loop = () => {
      if (!this.active) return;
      let level = 0;
      if (this.analyser && !this.paused) {
        this.analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
        level = Math.min(1, Math.sqrt(sum / data.length) * 4);
        // Forgotten-recording nudge: 8+ minutes of near-silence.
        if (level > 0.05) {
          this.lastLoud = Date.now();
          if (this.nudged) { this.nudged = false; document.title = "Meeting Scribe"; }
        } else if (!this.nudged && Date.now() - this.lastLoud > 8 * 60 * 1000) {
          this.nudged = true;
          document.title = "⏺ Still recording? — Meeting Scribe";
          toast("Still recording — over 8 minutes of silence. Forgot to stop?", true);
        }
      }
      this.meterHistory.push(level);
      const bars = Math.floor(W / 8);
      if (this.meterHistory.length > bars) this.meterHistory = this.meterHistory.slice(-bars);
      ctx2.clearRect(0, 0, W, H);
      this.meterHistory.forEach((lv, i) => {
        const h = Math.max(4, lv * H * 0.95);
        ctx2.fillStyle = this.paused ? "#d6dae3" : "#4f46e5";
        ctx2.globalAlpha = 0.35 + 0.65 * (i / this.meterHistory.length);
        if (ctx2.roundRect) {
          ctx2.beginPath();
          ctx2.roundRect(i * 8, (H - h) / 2, 5, h, 3);
          ctx2.fill();
        } else {
          ctx2.fillRect(i * 8, (H - h) / 2, 5, h);
        }
      });
      ctx2.globalAlpha = 1;
      this.raf = requestAnimationFrame(loop);
    };
    loop();
  },
};

/* ---------------- views ---------------- */
let appState = null;
async function refreshState() {
  try { appState = await api("/api/state"); } catch (e) { appState = null; }
  return appState;
}

function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach(a =>
    a.classList.toggle("active", a.dataset.nav === name));
}

/* ----- home ----- */
async function viewHome() {
  setNav("home");
  const st = await refreshState();
  let recents = [];
  try { recents = (await api("/api/recordings")).slice(0, 6); } catch (e) {}

  const keyBanner = st && !st.has_key ? `
    <div class="banner">⚠️ <div>Add your OpenAI API key to start transcribing —
    <a href="#/settings">open Settings</a></div></div>` : "";

  const jobsChip = st && st.active_jobs && st.active_jobs.length ? `
    <div class="banner" style="background:#eff6ff;border-color:#bfdbfe;color:#1e40af">
      ⏳ <div>Processing ${st.active_jobs.length} recording${st.active_jobs.length > 1 ? "s" : ""}…
      <a href="#/processing/${esc(st.active_jobs[0].rec_id)}" style="color:#1e40af">view progress</a></div>
    </div>` : "";

  $view.innerHTML = `
    <h1>What would you like to capture?</h1>
    <p class="sub">Record a quick voice note or a full meeting with automatic speaker detection.</p>
    ${keyBanner}${jobsChip}
    <div class="hero">
      <button class="hero-btn quick" id="btn-quick">
        <span class="ic">${I.mic}</span>
        <div class="t">Transcribe</div>
        <div class="d">Quick voice-to-text. Speak, stop, copy the text.</div>
      </button>
      <button class="hero-btn meeting" id="btn-meeting">
        <span class="ic">${I.users}</span>
        <div class="t">Meeting Recording</div>
        <div class="d">Record a meeting — transcript split by speaker, plus an AI summary.</div>
      </button>
    </div>
    <div class="searchbar">${I.search}
      <input type="search" id="home-search" placeholder="Search across all transcripts — keyword or meaning…">
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h2>Recent</h2>
      <button class="btn small" id="btn-import">${I.upload} Import audio file</button>
    </div>
    <div class="list" id="recent"></div>`;

  document.getElementById("btn-quick").onclick = () => location.hash = "#/record/quick";
  document.getElementById("btn-meeting").onclick = () => location.hash = "#/record/meeting";
  document.getElementById("btn-import").onclick = importAudio;
  const hs = document.getElementById("home-search");
  hs.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && hs.value.trim())
      location.hash = "#/library?q=" + encodeURIComponent(hs.value.trim());
  });
  renderRecList(document.getElementById("recent"), recents,
    `<div class="empty">${I.mic}<div>No recordings yet. Hit one of the buttons above to start.</div></div>`);
}

function renderRecList(el, recs, emptyHtml) {
  if (!recs.length) { el.innerHTML = emptyHtml || `<div class="empty">Nothing here yet.</div>`; return; }
  el.innerHTML = recs.map(r => {
    const ic = r.mode === "quick" ? I.mic : I.users;
    const status = r.status === "processing"
      ? `<span class="status processing">processing…</span>`
      : r.status === "error" ? `<span class="status error">failed</span>` : "";
    const dur = r.duration_s ? ` · ${fmtClock(r.duration_s)}` : "";
    const nSpeak = r.mode === "meeting" && r.speakers && Object.keys(r.speakers).length
      ? ` · ${Object.keys(r.speakers).length} speakers` : "";
    return `<div class="card item ${esc(r.mode)}" data-id="${esc(r.id)}" data-status="${esc(r.status)}">
      <div class="mode-ic">${ic}</div>
      <div class="body">
        <div class="t">${esc(r.title || "Untitled")}</div>
        <div class="m">${esc(fmtDate(r.created_at))}${dur}${nSpeak}</div>
      </div>${status}
    </div>`;
  }).join("");
  el.querySelectorAll(".item").forEach(it => {
    it.onclick = () => {
      const st = it.dataset.status;
      if (st === "processing") location.hash = "#/processing/" + it.dataset.id;
      else if (st === "error") location.hash = "#/processing/" + it.dataset.id;
      else location.hash = "#/transcript/" + it.dataset.id;
    };
  });
}

/* ----- import ----- */
function importAudio() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "audio/*,video/mp4,.m4a,.webm,.ogg,.opus,.flac";
  input.onchange = () => {
    const file = input.files[0];
    if (!file) return;
    const m = showModal(`
      <h3>Import “${esc(file.name)}”</h3>
      <p class="muted small">How should it be transcribed?</p>
      <div style="display:flex;flex-direction:column;gap:10px">
        <button class="btn big" id="imp-meeting">${I.users} Meeting — detect speakers</button>
        <button class="btn big" id="imp-quick">${I.mic} Quick note — plain text</button>
      </div>
      <div class="foot"><button class="btn" id="imp-cancel">Cancel</button></div>`);
    m.querySelector("#imp-cancel").onclick = closeModal;
    const go = async (mode) => {
      closeModal();
      toast("Uploading audio…");
      const fd = new FormData();
      fd.append("file", file); fd.append("mode", mode);
      try {
        const res = await api("/api/recordings/import", { method: "POST", body: fd });
        location.hash = "#/processing/" + res.id;
      } catch (e) { toast(e.message, true); }
    };
    m.querySelector("#imp-meeting").onclick = () => go("meeting");
    m.querySelector("#imp-quick").onclick = () => go("quick");
  };
  input.click();
}

/* ----- record ----- */
async function viewRecord(mode) {
  setNav("");
  const isMeeting = mode === "meeting";
  const st = appState || await refreshState();
  if (st && !st.has_key) {
    location.hash = "#/settings";
    toast("Add your OpenAI API key first", true);
    return;
  }
  $view.innerHTML = `
    <div class="card recwrap">
      <span class="modechip">${isMeeting ? I.users + " Meeting Recording — speakers will be detected" : I.mic + " Quick Transcribe"}</span>
      <div class="timer" id="timer">0:00</div>
      <div class="recstate" id="recstate">Ready when you are</div>
      <canvas id="meter"></canvas>
      <div class="devrow"><select id="mic-select"><option value="">Default microphone</option></select></div>
      <div class="recbtns" id="recbtns">
        <button class="btn rec big" id="btn-start">${I.mic} Start recording</button>
        <button class="btn big" id="btn-back">Back</button>
      </div>
      ${isMeeting ? `<p class="muted small" style="margin-top:22px">Tip: for video calls, play the other side through your speakers so the microphone hears everyone.</p>` : ""}
    </div>`;

  const $timer = document.getElementById("timer");
  const $state = document.getElementById("recstate");
  const $btns = document.getElementById("recbtns");
  const $mic = document.getElementById("mic-select");

  // populate device list (labels appear once permission has been granted)
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const mics = devs.filter(d => d.kind === "audioinput" && d.deviceId);
    if (mics.length) $mic.innerHTML = mics.map((d, i) =>
      `<option value="${esc(d.deviceId)}">${esc(d.label || "Microphone " + (i + 1))}</option>`).join("");
  } catch (e) {}

  let tick = null;
  const renderButtons = () => {
    if (!Rec.active) {
      $btns.innerHTML = `<button class="btn rec big" id="btn-start">${I.mic} Start recording</button>
        <button class="btn big" id="btn-back">Back</button>`;
      document.getElementById("btn-start").onclick = start;
      document.getElementById("btn-back").onclick = () => history.back();
    } else {
      $btns.innerHTML = `
        <button class="btn primary big" id="btn-stop">${I.stop} Stop &amp; transcribe</button>
        <button class="btn big" id="btn-pause">${Rec.paused ? I.play + " Resume" : I.pause + " Pause"}</button>
        <button class="btn big danger" id="btn-cancel">Discard</button>`;
      document.getElementById("btn-stop").onclick = stop;
      document.getElementById("btn-pause").onclick = () => {
        Rec.paused ? Rec.resume() : Rec.pause();
        $state.innerHTML = Rec.paused ? "Paused" : `<span class="dot"></span>Recording`;
        renderButtons();
      };
      document.getElementById("btn-cancel").onclick = async () => {
        if (!confirm("Discard this recording?")) return;
        clearInterval(tick);
        await Rec.stop(true);
        location.hash = "#/";
      };
    }
  };

  const start = async () => {
    try {
      await Rec.start(mode, $mic.value || undefined);
    } catch (e) {
      const msg = (e.name === "NotAllowedError" || e.name === "SecurityError")
        ? "Microphone access was blocked. Allow the microphone for this app in your browser, then try again."
        : e.message;
      toast(msg, true);
      return;
    }
    document.querySelector(".devrow").style.display = "none";
    $state.innerHTML = `<span class="dot"></span>Recording`;
    Rec.meterHistory = [];
    Rec.drawMeter(document.getElementById("meter"));
    tick = setInterval(() => { $timer.textContent = fmtClock(Rec.elapsed()); }, 300);
    renderButtons();
  };

  const stop = async () => {
    clearInterval(tick);
    $state.textContent = "Saving audio…";
    try {
      const id = await Rec.stop(false);
      if (id) location.hash = "#/processing/" + id;
    } catch (e) { toast(e.message, true); $state.textContent = "Something went wrong"; renderButtons(); }
  };

  renderButtons();
}

/* ----- processing ----- */
async function viewProcessing(recId) {
  setNav("");
  $view.innerHTML = `
    <div class="card recwrap" id="proccard">
      <div class="modechip">⏳ Working on it</div>
      <h1 id="proc-stage" style="margin:18px 0 4px;font-size:22px">Queued…</h1>
      <div class="muted" id="proc-detail"></div>
      <div class="progress"><div id="proc-bar" style="width:3%"></div></div>
      <p class="muted small">You can leave this page — processing continues in the background.</p>
    </div>`;

  const $stage = document.getElementById("proc-stage");
  const $detail = document.getElementById("proc-detail");
  const $bar = document.getElementById("proc-bar");
  const stageNames = { queued: "Queued…", preparing: "Preparing audio…",
    transcribing: "Transcribing…", summarizing: "Summarizing…",
    saving: "Saving…", indexing: "Indexing for search…", done: "Done", error: "Failed" };

  let stopped = false;
  const poll = async () => {
    if (stopped || !location.hash.includes(recId)) return;
    let info = null;
    try { info = await api("/api/jobs/" + recId); } catch (e) {}
    const job = info && info.job;
    const status = info && info.status;

    if (status === "done") { stopped = true; return showDone(); }
    if ((job && job.error) || status === "error") {
      stopped = true;
      const msg = (job && job.error) || (info && info.error) || "Unknown error";
      $stage.textContent = "Transcription failed";
      $detail.textContent = msg;
      $bar.style.width = "100%"; $bar.style.background = "var(--danger)";
      document.getElementById("proccard").insertAdjacentHTML("beforeend",
        `<div class="recbtns" style="margin-top:18px">
           <button class="btn primary" id="btn-retry">Try again</button>
           <button class="btn" onclick="location.hash='#/'">Home</button>
           <button class="btn danger" id="btn-del-failed">${I.trash} Remove this recording</button>
         </div>`);
      document.getElementById("btn-retry").onclick = async () => {
        try {
          await api("/api/recordings/" + recId + "/retry", { method: "POST" });
          viewProcessing(recId);
        } catch (e) { toast(e.message, true); }
      };
      document.getElementById("btn-del-failed").onclick = async () => {
        await api("/api/recordings/" + recId + "?files=0", { method: "DELETE" });
        location.hash = "#/";
      };
      return;
    }
    if (job) {
      $stage.textContent = stageNames[job.stage] || job.stage;
      $detail.textContent = job.detail || "";
      $bar.style.width = Math.max(3, job.pct) + "%";
    } else if (!job && status === "processing") {
      $stage.textContent = "Processing…";
      $detail.textContent = "Recovering job status…";
    }
    setTimeout(poll, 1200);
  };

  const showDone = async () => {
    let rec = null;
    try { rec = await api("/api/recordings/" + recId); } catch (e) {}
    if (rec && rec.mode === "quick") {
      const text = rec.turns && rec.turns[0] ? rec.turns[0].text : "";
      $view.innerHTML = `
        <div class="card recwrap" style="text-align:left">
          <div style="display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap">
            <h1 style="margin:0;font-size:22px">✅ ${esc(rec.title || "Transcribed")}</h1>
            <div style="display:flex;gap:8px">
              <button class="btn primary" id="btn-copy">${I.copy} Copy text</button>
              <button class="btn" onclick="location.hash='#/transcript/${esc(recId)}'">Open</button>
              <button class="btn" onclick="location.hash='#/'">Home</button>
            </div>
          </div>
          <div class="quicktext" id="quicktext">${esc(text)}</div>
        </div>`;
      document.getElementById("btn-copy").onclick = async () => {
        try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
        catch (e) {
          const r = document.createRange(); r.selectNodeContents(document.getElementById("quicktext"));
          const s = getSelection(); s.removeAllRanges(); s.addRange(r);
          document.execCommand("copy"); toast("Copied");
        }
      };
    } else {
      location.hash = "#/transcript/" + recId;
    }
  };

  poll();
}

/* ----- transcript ----- */
async function viewTranscript(recId, params) {
  setNav("library");
  let rec;
  try { rec = await api("/api/recordings/" + recId); }
  catch (e) { $view.innerHTML = `<div class="empty">Recording not found.</div>`; return; }
  if (rec.status === "processing") { location.hash = "#/processing/" + recId; return; }

  const speakers = rec.speakers || {};
  const order = Object.keys(speakers);
  const isMeeting = rec.mode === "meeting";
  const turns = rec.turns || [];
  const hlStart = params.get("t");

  const legend = isMeeting && order.length ? `
    <div class="legend">${order.map(lab => `
      <span class="chip" title="Click to rename">
        <span class="swatch" style="background:${speakerColor(lab, order)}"></span>
        <input data-label="${esc(lab)}" value="${esc(speakers[lab] || ("Speaker " + lab))}">
      </span>`).join("")}
      <span class="muted small" style="align-self:center">· click a name to edit</span>
    </div>` : "";

  const turnsHtml = turns.map(t => {
    const lab = t.speaker || "";
    const name = lab ? (speakers[lab] || "Speaker " + lab) : "";
    const color = lab ? speakerColor(lab, order) : "var(--line)";
    const hl = hlStart !== null && Math.abs((t.start_s || 0) - parseFloat(hlStart)) < 0.5 ? " hl" : "";
    return `<div class="turn${hl}" data-start="${t.start_s || 0}">
      <div class="bar" style="background:${color}"></div>
      <div style="flex:1;min-width:0">
        ${lab ? `<div class="who" style="color:${color}">${esc(name)}
          <span class="ts" title="Jump to this moment">${esc(fmtClock(t.start_s))}</span></div>` : ""}
        <div class="txt">${esc(t.text)}</div>
      </div>
    </div>`;
  }).join("");

  $view.innerHTML = `
    <div class="card theader">
      <div class="titlerow">
        <h1 contenteditable="true" id="rec-title" spellcheck="false">${esc(rec.title || "Untitled")}</h1>
      </div>
      <div class="meta">${esc(fmtDate(rec.created_at))} · ${esc(fmtClock(rec.duration_s))}
        · ${isMeeting ? `Meeting · ${order.length} speaker${order.length === 1 ? "" : "s"}` : "Voice note"}</div>
      <div class="actions">
        <button class="btn" id="btn-copy-all">${I.copy} Copy transcript</button>
        <a class="btn" href="/api/recordings/${esc(recId)}/file/md">${I.download} Markdown</a>
        <a class="btn" href="/api/recordings/${esc(recId)}/file/json">${I.doc} JSON</a>
        <button class="btn danger" id="btn-del">${I.trash} Delete</button>
      </div>
    </div>
    ${rec.summary ? `<div class="card summary">${mdLite(rec.summary)}</div>` : ""}
    ${legend}
    <div class="turns">${turnsHtml || `<div class="empty">Transcript is empty.</div>`}</div>
    ${renderPostMeeting(rec, speakers, order)}
    <div style="height:70px"></div>
    <div class="audiobar"><div class="inner">
      <audio id="player" controls preload="metadata" src="/api/recordings/${esc(recId)}/audio"></audio>
    </div></div>`;

  const player = document.getElementById("player");
  document.querySelectorAll(".turn .ts").forEach(ts => {
    ts.onclick = (e) => {
      const start = parseFloat(e.target.closest(".turn").dataset.start) || 0;
      player.currentTime = Math.max(0, start - 0.4);
      player.play();
    };
  });
  if (hlStart !== null) {
    const el = document.querySelector(".turn.hl");
    if (el) setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "center" }), 150);
  }

  // copy whole transcript
  document.getElementById("btn-copy-all").onclick = async () => {
    const text = turns.map(t => {
      const lab = t.speaker || "";
      const nm = lab ? ((speakers[lab] || "Speaker " + lab) + ` [${fmtClock(t.start_s)}]: `) : "";
      return nm + t.text;
    }).join("\n\n");
    try { await navigator.clipboard.writeText(text); toast("Transcript copied"); }
    catch (e) { toast("Copy failed — use the Markdown download instead", true); }
  };

  // delete
  document.getElementById("btn-del").onclick = () => {
    const m = showModal(`
      <h3>Delete “${esc(rec.title)}”?</h3>
      <p class="muted small">Choose whether to also delete the audio + transcript files in your transcripts folder.</p>
      <div class="foot">
        <button class="btn" id="d-cancel">Cancel</button>
        <button class="btn" id="d-index">Remove from app only</button>
        <button class="btn danger" id="d-all">${I.trash} Delete files too</button>
      </div>`);
    m.querySelector("#d-cancel").onclick = closeModal;
    m.querySelector("#d-index").onclick = async () => {
      await api("/api/recordings/" + recId + "?files=0", { method: "DELETE" });
      closeModal(); toast("Removed from library"); location.hash = "#/library";
    };
    m.querySelector("#d-all").onclick = async () => {
      await api("/api/recordings/" + recId + "?files=1", { method: "DELETE" });
      closeModal(); toast("Recording deleted"); location.hash = "#/library";
    };
  };

  // title editing
  const $title = document.getElementById("rec-title");
  const saveTitle = async () => {
    const t = $title.textContent.trim();
    if (!t || t === rec.title) { $title.textContent = rec.title; return; }
    try {
      const res = await api("/api/recordings/" + recId, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: t }),
      });
      rec.title = res.title; $title.textContent = res.title; toast("Title updated");
    } catch (e) { toast(e.message, true); }
  };
  $title.addEventListener("blur", saveTitle);
  $title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $title.blur(); } });

  // speaker renaming
  let renameTimer = null;
  document.querySelectorAll(".legend input").forEach(inp => {
    inp.addEventListener("change", () => {
      clearTimeout(renameTimer);
      renameTimer = setTimeout(async () => {
        const mapping = {};
        document.querySelectorAll(".legend input").forEach(i2 => { mapping[i2.dataset.label] = i2.value.trim(); });
        try {
          await api(`/api/recordings/${recId}/speakers`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ speakers: mapping }),
          });
          toast("Speakers updated");
          viewTranscript(recId, params);   // re-render with new names
        } catch (e) { toast(e.message, true); }
      }, 250);
    });
  });
}

function renderPostMeeting(rec, speakers, order) {
  const pm = rec.post_meeting;
  if (!pm || !pm.turns || !pm.turns.length) return "";
  const endTs = pm.meeting_end_s != null ? fmtClock(pm.meeting_end_s) : "";
  const inner = pm.turns.map(t => {
    const lab = t.speaker || "";
    const name = lab ? (speakers[lab] || "Speaker " + lab) : "";
    const color = lab ? speakerColor(lab, order) : "var(--line)";
    return `<div class="turn" data-start="${t.start_s || 0}">
      <div class="bar" style="background:${color};opacity:.45"></div>
      <div style="flex:1;min-width:0">
        ${lab ? `<div class="who" style="color:${color};opacity:.75">${esc(name)}
          <span class="ts">${esc(fmtClock(t.start_s))}</span></div>` : ""}
        <div class="txt" style="color:var(--ink-2)">${esc(t.text)}</div>
      </div>
    </div>`;
  }).join("");
  return `<details class="postmeet">
    <summary>🔇 After the meeting — ${pm.turns.length} segment${pm.turns.length > 1 ? "s" : ""} captured after the detected end${endTs ? ` (~${esc(endTs)})` : ""}. Excluded from summary &amp; search.</summary>
    <div class="turns" style="margin-top:8px">${inner}</div>
  </details>`;
}

/* ----- library / search ----- */
async function viewLibrary(params) {
  setNav("library");
  const q0 = params.get("q") || "";
  $view.innerHTML = `
    <h1>Library</h1>
    <p class="sub">Every transcript, searchable by keyword or by meaning.</p>
    <div class="searchbar" style="margin-top:0">${I.search}
      <input type="search" id="lib-search" placeholder="Search transcripts… (e.g. “pricing pushback from the agency”)" value="${esc(q0)}">
    </div>
    <div id="lib-out" class="list" style="margin-top:18px"></div>`;

  const $q = document.getElementById("lib-search");
  const $out = document.getElementById("lib-out");
  let timer = null, lastQuery = null;

  const renderAll = async () => {
    let recs = [];
    try { recs = await api("/api/recordings"); } catch (e) {}
    renderRecList($out, recs,
      `<div class="empty">${I.doc}<div>No transcripts yet.</div></div>`);
  };

  const renderSearch = async (q) => {
    $out.innerHTML = `<div class="empty">Searching…</div>`;
    let res = [];
    try { res = await api("/api/search?q=" + encodeURIComponent(q)); }
    catch (e) { $out.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (lastQuery !== q) return;  // stale response
    if (!res.length) { $out.innerHTML = `<div class="empty">No matches for “${esc(q)}”.</div>`; return; }
    $out.innerHTML = res.map(r => `
      <div class="card result" data-rec="${esc(r.rec_id)}" data-t="${r.start_s == null ? "" : r.start_s}">
        <div class="where">
          <b>${esc(r.title || "Untitled")}</b>
          <span>${esc(fmtDate(r.created_at))}</span>
          ${r.speaker ? `<span>· ${esc(r.speaker)}</span>` : ""}
          ${r.start_s != null && r.mode === "meeting" ? `<span>· ${esc(fmtClock(r.start_s))}</span>` : ""}
          ${(r.match || []).map(m => `<span class="tag ${esc(m)}">${esc(m)}</span>`).join("")}
        </div>
        <div class="snip">${r.snippet}</div>
      </div>`).join("");
    $out.querySelectorAll(".result").forEach(el => {
      el.onclick = () => {
        const t = el.dataset.t;
        location.hash = `#/transcript/${el.dataset.rec}` + (t !== "" ? `?t=${t}` : "");
      };
    });
  };

  const onInput = () => {
    clearTimeout(timer);
    const q = $q.value.trim();
    lastQuery = q;
    history.replaceState(null, "", q ? `#/library?q=${encodeURIComponent(q)}` : "#/library");
    timer = setTimeout(() => { q ? renderSearch(q) : renderAll(); }, 320);
  };
  $q.addEventListener("input", onInput);
  $q.focus();

  q0 ? renderSearch(q0) : renderAll();
}

/* ----- settings ----- */
async function viewSettings() {
  setNav("settings");
  const st = await refreshState() || {};
  $view.innerHTML = `
    <h1>Settings</h1>
    <p class="sub">Everything stays on your Mac except the audio sent to OpenAI for transcription.</p>
    <div class="card">
      <div class="set-row">
        <div class="lab">OpenAI API key</div>
        <div class="desc">Used for transcription, speaker detection, search and summaries.
          Stored only on this Mac. <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">Get a key ↗</a></div>
        <div class="inline">
          <input type="password" id="set-key" placeholder="sk-…" value="${esc(st.key_masked || "")}">
          <button class="btn primary" id="btn-save-key">Save &amp; test</button>
        </div>
        <div class="testmsg" id="key-msg"></div>
      </div>
      <div class="set-row">
        <div class="lab">Transcripts folder</div>
        <div class="desc">Every recording is saved here — audio, a readable transcript (.md) and data (.json).</div>
        <div class="inline">
          <input type="text" id="set-folder" value="${esc(st.transcripts_dir || "")}">
          <button class="btn" id="btn-browse">${I.folder} Browse…</button>
        </div>
        <div class="testmsg" id="folder-msg"></div>
      </div>
      <div class="set-row">
        <div class="inline" style="justify-content:space-between">
          <div>
            <div class="lab">AI titles &amp; meeting summaries</div>
            <div class="desc" style="margin:0">Auto-generate a title, overview, decisions and action items for each recording.</div>
          </div>
          <label class="switch"><input type="checkbox" id="set-summaries" ${st.generate_summaries ? "checked" : ""}><span></span></label>
        </div>
      </div>
      <div class="set-row">
        <div class="lab">Usage this month</div>
        <div class="desc" style="margin:0">${st.month_hours || 0} hours transcribed ≈ $${(st.month_cost_est || 0).toFixed(2)} of OpenAI credit
          <span class="muted">(transcription ≈ $0.36 per audio hour)</span></div>
      </div>
      <div class="set-row">
        <div class="lab">Maintenance</div>
        <div class="desc">If you moved or edited transcript files outside the app, rebuild the search index from the folder.</div>
        <button class="btn" id="btn-reindex">Rebuild search index</button>
        <span class="testmsg" id="reindex-msg"></span>
      </div>
      <div class="set-row">
        <div class="inline" style="justify-content:space-between">
          <div class="muted small">Meeting Scribe v${esc(st.version || "1.0")} · running locally at ${esc(location.host)}</div>
          <button class="btn" id="btn-quit">Quit Meeting Scribe</button>
        </div>
      </div>
    </div>`;

  document.getElementById("btn-quit").onclick = async () => {
    if (!confirm("Quit Meeting Scribe? Recording and processing will stop.")) return;
    try { await api("/api/quit", { method: "POST" }); } catch (e) {}
    $view.innerHTML = `<div class="empty">Meeting Scribe has quit. Double-click the app to start it again, then reload this page.</div>`;
  };

  document.getElementById("btn-save-key").onclick = async () => {
    const $msg = document.getElementById("key-msg");
    const key = document.getElementById("set-key").value.trim();
    $msg.className = "testmsg"; $msg.textContent = "Testing key…";
    try {
      if (key && !key.startsWith("•"))
        await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ openai_api_key: key }) });
      const res = await api("/api/settings/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      $msg.className = "testmsg ok";
      $msg.textContent = "✓ Key works. " + (res.diarize_available
        ? "Speaker-detection model available."
        : "Note: the diarization model wasn’t listed for this key — meeting mode may not work until your account has access to gpt-4o-transcribe-diarize.");
      refreshState();
    } catch (e) { $msg.className = "testmsg bad"; $msg.textContent = "✕ " + e.message; }
  };

  document.getElementById("set-folder").addEventListener("change", saveFolder);
  document.getElementById("btn-browse").onclick = () => browseFolder(saveFolder);

  async function saveFolder() {
    const $msg = document.getElementById("folder-msg");
    const val = document.getElementById("set-folder").value.trim();
    if (!val) return;
    try {
      const res = await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ transcripts_dir: val }) });
      document.getElementById("set-folder").value = res.transcripts_dir;
      $msg.className = "testmsg ok"; $msg.textContent = "✓ Transcripts will be saved here.";
    } catch (e) { $msg.className = "testmsg bad"; $msg.textContent = "✕ " + e.message; }
  }

  document.getElementById("set-summaries").addEventListener("change", async (e) => {
    try {
      await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ generate_summaries: e.target.checked }) });
      toast(e.target.checked ? "Summaries on" : "Summaries off");
    } catch (err) { toast(err.message, true); }
  });

  document.getElementById("btn-reindex").onclick = async () => {
    const $msg = document.getElementById("reindex-msg");
    $msg.className = "testmsg"; $msg.textContent = "Rebuilding…";
    try {
      const res = await api("/api/reindex", { method: "POST" });
      $msg.className = "testmsg ok";
      $msg.textContent = `✓ Indexed ${res.recordings} recordings (${res.embedded_chunks} chunks re-embedded).`;
    } catch (e) { $msg.className = "testmsg bad"; $msg.textContent = "✕ " + e.message; }
  };
}

function browseFolder(onPicked) {
  const render = async (path) => {
    let data;
    try { data = await api("/api/folders?path=" + encodeURIComponent(path || "~")); }
    catch (e) { toast(e.message, true); return; }
    const m = showModal(`
      <h3>Choose transcripts folder</h3>
      <div class="pathnow">${esc(data.path)}</div>
      <div class="dirlist">
        ${data.parent ? `<div class="d" data-p="${esc(data.parent)}">⬑ Up one level</div>` : ""}
        ${data.dirs.map(d => `<div class="d" data-p="${esc(data.path)}/${esc(d)}">${I.folder} ${esc(d)}</div>`).join("") || `<div class="d muted">No subfolders</div>`}
      </div>
      <div class="foot">
        <button class="btn" id="bf-cancel">Cancel</button>
        <button class="btn primary" id="bf-choose">Use this folder</button>
      </div>`);
    m.querySelectorAll(".d[data-p]").forEach(el => { el.onclick = () => render(el.dataset.p); });
    m.querySelector("#bf-cancel").onclick = closeModal;
    m.querySelector("#bf-choose").onclick = () => {
      document.getElementById("set-folder").value = data.path;
      closeModal(); onPicked();
    };
  };
  render(document.getElementById("set-folder").value || "~");
}

/* ---------------- router ---------------- */
async function route() {
  if (Rec.active && !location.hash.startsWith("#/record/")) {
    // navigating away while recording — keep recording, but warn
    toast("Still recording — go back to the recorder to stop it");
  }
  const hash = location.hash || "#/";
  const [pathPart, queryPart] = hash.slice(1).split("?");
  const params = new URLSearchParams(queryPart || "");
  const seg = pathPart.split("/").filter(Boolean);

  try {
    if (!seg.length) return await viewHome();
    if (seg[0] === "record") return await viewRecord(seg[1] === "quick" ? "quick" : "meeting");
    if (seg[0] === "processing" && seg[1]) return await viewProcessing(seg[1]);
    if (seg[0] === "transcript" && seg[1]) return await viewTranscript(seg[1], params);
    if (seg[0] === "library") return await viewLibrary(params);
    if (seg[0] === "settings") return await viewSettings();
    return await viewHome();
  } catch (e) {
    $view.innerHTML = `<div class="empty">Something went wrong: ${esc(e.message)}</div>`;
  }
}
window.addEventListener("hashchange", route);
route();
