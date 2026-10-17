/* Sheep MP3 — dashboard logic */
"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  format: "mp3",
  jobs: new Map(),     // id -> job
  baa: false,
};

const els = {
  links: $("#links"),
  convert: $("#convertBtn"),
  formatSeg: $("#formatSeg"),
  flock: $("#flock"),
  flockEmpty: $("#flockEmpty"),
  flockHead: $("#flockHead"),
  clearBtn: $("#clearBtn"),
  hint: $("#hint"),
  reveal: $("#revealBtn"),
  outDir: $("#outDir"),
  // settings
  settingsBtn: $("#settingsBtn"),
  drawer: $("#drawer"),
  scrim: $("#scrim"),
  closeSettings: $("#closeSettings"),
  outDirInput: $("#outDirInput"),
  baaToggle: $("#baaToggle"),
  cookiesToggle: $("#cookiesToggle"),
  cookiesBrowser: $("#cookiesBrowser"),
  saveSettings: $("#saveSettings"),
  engine: $("#engine"),
};

const ACTIVE = new Set(["queued", "resolving", "downloading", "converting"]);
const SOURCE_LABEL = { youtube: "YT", soundcloud: "SC", spotify: "SP", unknown: "?" };

/* ---- WebSocket ---------------------------------------------------------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "snapshot") {
      msg.jobs.forEach(upsertJob);
    } else if (msg.type === "job") {
      upsertJob(msg);
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onerror = () => ws.close();
}

/* ---- Job rendering ------------------------------------------------------ */
function upsertJob(job) {
  const prev = state.jobs.get(job.id);
  state.jobs.set(job.id, job);

  let card = document.getElementById(`job-${job.id}`);
  if (!card) {
    card = renderCard(job);
    els.flock.appendChild(card);
  }
  paintCard(card, job);

  // soft baa on a fresh completion
  if (state.baa && job.status === "done" && prev && prev.status !== "done") {
    baa();
  }
  refreshChrome();
}

function renderCard(job) {
  const card = document.createElement("div");
  card.className = "job";
  card.id = `job-${job.id}`;
  card.innerHTML = `
    <div class="job-badge ${job.source}">${SOURCE_LABEL[job.source] || "?"}</div>
    <div class="job-main">
      <div class="job-title"></div>
      <div class="job-detail"></div>
      <div class="bar"><div class="bar-fill"></div></div>
    </div>
    <div class="job-status"></div>`;
  return card;
}

function paintCard(card, job) {
  card.classList.toggle("done", job.status === "done");
  card.classList.toggle("error", job.status === "error");
  card.querySelector(".job-title").textContent = job.title || job.url;
  card.querySelector(".job-detail").textContent = job.error || job.detail || "";
  card.querySelector(".bar-fill").style.width = `${job.percent || 0}%`;

  const status = card.querySelector(".job-status");
  if (job.status === "done") status.innerHTML = `<span class="tick">✓</span>`;
  else if (job.status === "error") status.innerHTML = `<span class="tick">!</span>`;
  else if (job.status === "downloading" || job.status === "converting")
    status.textContent = `${job.percent || 0}%`;
  else status.innerHTML = `<span class="tick">🐑</span>`;
}

function refreshChrome() {
  const has = state.jobs.size > 0;
  els.flockEmpty.hidden = has;
  els.flockHead.hidden = !has;
  const active = [...state.jobs.values()].some((j) => ACTIVE.has(j.status));
  els.convert.classList.toggle("busy", active);
  els.convert.querySelector(".convert-label").textContent = active ? "Rounding up…" : "Round ’em up";
}

/* ---- Submit ------------------------------------------------------------- */
async function submit() {
  const urls = els.links.value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) {
    toast("Paste at least one link 🐑");
    els.links.focus();
    return;
  }
  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls, format: state.format }),
    });
    if (!res.ok) throw new Error(await res.text());
    els.links.value = "";
    toast(`Herding ${urls.length} track${urls.length > 1 ? "s" : ""}…`);
  } catch (e) {
    toast("Something went wrong starting the download");
  }
}

/* ---- Settings ----------------------------------------------------------- */
async function loadSettings() {
  try {
    const s = await (await fetch("/api/settings")).json();
    state.format = s.format === "wav" ? "wav" : "mp3";
    [...els.formatSeg.children].forEach((b) =>
      b.classList.toggle("active", b.dataset.fmt === state.format));
    els.outDir.textContent = s.output_dir.replace(/^\/Users\/[^/]+/, "~");
    els.outDirInput.value = s.output_dir;
    els.cookiesToggle.checked = !!s.use_cookies;
    if (s.cookies_browser) els.cookiesBrowser.value = s.cookies_browser;
    state.baa = localStorage.getItem("baa") === "1";
    els.baaToggle.checked = state.baa;
  } catch (e) { /* server may still be warming up */ }
}

async function saveSettings() {
  state.baa = els.baaToggle.checked;
  localStorage.setItem("baa", state.baa ? "1" : "0");
  try {
    const s = await (await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        output_dir: els.outDirInput.value.trim() || undefined,
        use_cookies: els.cookiesToggle.checked,
        cookies_browser: els.cookiesBrowser.value,
      }),
    })).json();
    els.outDir.textContent = s.output_dir.replace(/^\/Users\/[^/]+/, "~");
    toast("Settings saved");
  } catch (e) { toast("Couldn’t save settings"); }
  closeDrawer();
}

async function loadVersion() {
  try {
    const v = await (await fetch("/api/version")).json();
    const bits = [`Sheep MP3 ${v.app}`, `yt-dlp ${v.yt_dlp}`];
    if (!v.ffmpeg) bits.push("⚠️ ffmpeg missing");
    if (!v.deno) bits.push("⚠️ deno missing");
    els.engine.textContent = bits.join(" · ");
    els.engine.classList.toggle("warn", !v.ffmpeg || !v.deno);
    if (!v.ffmpeg || !v.deno) toast("Some tools are missing — run `sheep-mp3 setup` in Terminal");
  } catch (e) { /* ignore */ }
}

function openDrawer() { els.scrim.hidden = false; els.drawer.hidden = false; }
function closeDrawer() { els.scrim.hidden = true; els.drawer.hidden = true; }

/* ---- Toast + baa -------------------------------------------------------- */
let toastTimer;
function toast(text) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 2600);
}

/* a gentle synthesized bleat, no audio asset required */
function baa() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    const vib = ctx.createOscillator();
    const vibG = ctx.createGain();
    o.type = "sawtooth";
    o.frequency.setValueAtTime(420, ctx.currentTime);
    o.frequency.linearRampToValueAtTime(360, ctx.currentTime + 0.5);
    vib.frequency.value = 14; vibG.gain.value = 16;       // bleat wobble
    vib.connect(vibG); vibG.connect(o.frequency);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.55);
    o.connect(g); g.connect(ctx.destination);
    o.start(); vib.start();
    o.stop(ctx.currentTime + 0.6); vib.stop(ctx.currentTime + 0.6);
    o.onended = () => ctx.close();
  } catch (e) { /* audio blocked, ignore */ }
}

/* ---- Wiring ------------------------------------------------------------- */
els.formatSeg.addEventListener("click", (e) => {
  const btn = e.target.closest(".seg-opt");
  if (!btn) return;
  state.format = btn.dataset.fmt;
  [...els.formatSeg.children].forEach((b) => b.classList.toggle("active", b === btn));
});
els.convert.addEventListener("click", submit);
els.links.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
});
els.clearBtn.addEventListener("click", () => {
  for (const [id, job] of state.jobs) {
    if (!ACTIVE.has(job.status)) {
      document.getElementById(`job-${id}`)?.remove();
      state.jobs.delete(id);
    }
  }
  refreshChrome();
});
els.reveal.addEventListener("click", () => fetch("/api/reveal", { method: "POST" }));
els.settingsBtn.addEventListener("click", openDrawer);
els.closeSettings.addEventListener("click", closeDrawer);
els.scrim.addEventListener("click", closeDrawer);
els.saveSettings.addEventListener("click", saveSettings);

$("#quitBtn").addEventListener("click", async () => {
  try { await fetch("/api/quit", { method: "POST" }); } catch (e) { /* expected */ }
  document.body.innerHTML =
    '<div style="display:grid;place-items:center;height:100vh;font-family:Fredoka,sans-serif;' +
    'color:#6B5E52;text-align:center;gap:6px"><div><div style="font-size:46px">🐑💤</div>' +
    '<p style="font-size:20px;color:#3E3A36">Sheep MP3 has gone to sleep.</p>' +
    '<p>You can close this tab. Next time, open Terminal and run <code>sheep-mp3</code>.</p></div></div>';
});

loadSettings();
loadVersion();
connect();
