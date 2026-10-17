// ShadowCoach front end — plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");

const api = async (path, opts = {}) => {
  const o = { ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers = { "Content-Type": "application/json" }; o.method = o.method || "POST"; delete o.json; }
  const r = await fetch(path, o);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
};
const toast = (msg, ms = 3500) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms); };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tone = (v) => (v == null ? "" : v >= 80 ? "good" : v >= 60 ? "mid" : "bad");
const mount = (id) => { app.innerHTML = ""; app.appendChild($(id).content.cloneNode(true)); };
const ls = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
             set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };

let view = null;
let speakerPoll = null;

$$("nav button").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
function go(v, arg) {
  view = v; clearInterval(speakerPoll); stopAll();
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === (v === "session" ? "scripts" : v)));
  ({ scripts: showScripts, session: showSession, speakers: showSpeakers, settings: showSettings }[v])(arg);
  location.hash = v === "session" ? `s/${arg}` : v;
}

// ================= Scripts =================
async function showScripts() {
  mount("#tpl-scripts");
  $("#np-create").onclick = async () => {
    try {
      const p = await api("/api/projects", { json: { title: $("#np-title").value, script: $("#np-script").value } });
      go("session", p.id);
    } catch (e) { toast(e.message); }
  };
  const list = await api("/api/projects");
  $("#project-list").innerHTML = list.length ? "" : `<p class="muted">No sessions yet. Paste a script on the left to start.</p>`;
  for (const p of list) {
    const el = document.createElement("div");
    el.className = "item";
    el.innerHTML = `<div><div class="t">${esc(p.title)}</div><div class="muted small">${p.kind === "speaker" ? "Shadowing clips · " : ""}${p.done}/${p.n} sentences recorded</div></div>
      <button class="ghost small del">Delete</button>`;
    el.onclick = () => go("session", p.id);
    $(".del", el).onclick = async (ev) => { ev.stopPropagation(); if (confirm(`Delete “${p.title}” and all its takes?`)) { await api(`/api/projects/${p.id}`, { method: "DELETE" }); showScripts(); } };
    $("#project-list").appendChild(el);
  }
}

// ================= Session =================
const S = { p: null, i: 0, rate: 1, refAudio: new Audio(), youAudio: new Audio(), rec: null, chunks: [], stream: null, t0: 0, timer: null, lastAnalysis: null, selWord: null };

function stopAll() {
  S.refAudio.pause(); S.youAudio.pause();
  if (S.rec && S.rec.state === "recording") S.rec.stop();
  document.onkeydown = null;
}

async function showSession(pid) {
  mount("#tpl-session");
  S.p = await api(`/api/projects/${pid}`);
  S.i = Math.min(ls.get(`pos_${pid}`, 0), S.p.sentences.length - 1);
  const settings = await api("/api/settings");
  $("#provider").value = settings.tts_provider;
  $("#sess-title").textContent = S.p.title;
  $("#back").onclick = () => go("scripts");
  $("#regen-group").hidden = S.p.kind === "speaker";
  $("#auto-ab").checked = ls.get("autoAB", true);
  $("#auto-an").checked = ls.get("autoAn", true);
  $("#auto-ab").onchange = (e) => ls.set("autoAB", e.target.checked);
  $("#auto-an").onchange = (e) => ls.set("autoAn", e.target.checked);
  S.rate = ls.get("rate", 1);
  $$("#speeds button").forEach((b) => {
    b.classList.toggle("on", +b.dataset.r === S.rate);
    b.onclick = () => { S.rate = +b.dataset.r; ls.set("rate", S.rate); $$("#speeds button").forEach((x) => x.classList.toggle("on", x === b)); S.refAudio.playbackRate = S.rate; };
  });
  $("#loop").onchange = (e) => (S.refAudio.loop = e.target.checked);
  $("#play-ref").onclick = playRef;
  $("#regen").onclick = () => makeRef(true);
  $("#rec").onclick = toggleRec;
  $("#ab").onclick = abCompare;
  $("#prev").onclick = () => select(S.i - 1);
  $("#next").onclick = () => select(S.i + 1);
  $("#export").onclick = exportVO;
  document.onkeydown = keys;
  renderList();
  select(S.i);
}

function keys(e) {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); playRef(); }
  else if (k === "r") toggleRec();
  else if (k === "a") abCompare();
  else if (k === "arrowright") select(S.i + 1);
  else if (k === "arrowleft") select(S.i - 1);
  else if (k === "k") { const t = cur().takes.at(-1); if (t) keepTake(t.id); }
}

const cur = () => S.p.sentences[S.i];

function renderList() {
  const ol = $("#sent-list");
  ol.innerHTML = "";
  let done = 0;
  S.p.sentences.forEach((s, i) => {
    const kept = s.takes.find((t) => t.kept), best = kept || s.takes.at(-1);
    if (s.takes.length) done++;
    const sc = best?.scores?.overall;
    const li = document.createElement("li");
    li.className = i === S.i ? "cur" : "";
    li.innerHTML = `<span class="n">${i + 1}</span><span>${esc(s.text.length > 80 ? s.text.slice(0, 78) + "…" : s.text)}</span>
      <span class="sc" style="color:var(--${sc == null ? "mute" : tone(sc) === "good" ? "good" : tone(sc) === "mid" ? "mid" : "bad"})">${kept ? "★" : ""}${sc != null ? Math.round(sc) : s.takes.length ? "•" : ""}</span>`;
    li.onclick = () => select(i);
    ol.appendChild(li);
  });
  $("#prog-bar").style.width = `${(100 * done) / S.p.sentences.length}%`;
  $("#prog-text").textContent = `${done} of ${S.p.sentences.length} sentences recorded`;
}

async function select(i) {
  if (i < 0 || i >= S.p.sentences.length) return;
  if (S.rec && S.rec.state === "recording") return toast("Stop recording first.");
  S.i = i; ls.set(`pos_${S.p.id}`, i);
  S.refAudio.pause(); S.youAudio.pause();
  S.lastAnalysis = null; S.selWord = null;
  $$("#sent-list li").forEach((li, k) => li.classList.toggle("cur", k === i));
  $("#sent-list li.cur")?.scrollIntoView({ block: "nearest" });
  $("#line-num").textContent = `Sentence ${i + 1} of ${S.p.sentences.length}`;
  $("#results").hidden = true; $("#word-detail").hidden = true;
  renderLine();
  renderTakes();
  const s = cur();
  if (!s.ref_url && S.p.kind !== "speaker") await makeRef(false);
  else setRefSrc();
  // show analysis of the kept/latest take, if any
  const t = s.takes.find((x) => x.kept) || s.takes.at(-1);
  if (t?.scores) { try { showAnalysis(await api(`/api/projects/${S.p.id}/takes/${t.id}`)); } catch {} }
  prefetch(i + 1);
}

function setRefSrc() {
  const s = cur();
  const label = s.ref_provider === "speaker" ? `Real voice · ${s.ref_note || "speaker"}` : s.ref_provider ? `${s.ref_provider} voice` : "No reference yet";
  $("#ref-src").textContent = label;
  if (s.ref_url) { S.refAudio.src = s.ref_url; S.refAudio.playbackRate = S.rate; S.refAudio.loop = $("#loop").checked; }
}

async function makeRef(force) {
  const i = S.i;
  $("#ref-src").textContent = "Generating reference…";
  try {
    const s = await api(`/api/projects/${S.p.id}/sentences/${i}/reference?provider=${$("#provider").value}`, { method: "POST" });
    S.p.sentences[i].ref_url = s.ref_url; S.p.sentences[i].ref_provider = s.ref_provider;
    if (i === S.i) { setRefSrc(); if (force) playRef(); }
  } catch (e) { $("#ref-src").textContent = "No reference"; toast(e.message, 6000); }
}

async function prefetch(i) {
  const s = S.p.sentences[i];
  if (!s || s.ref_url || S.p.kind === "speaker") return;
  try {
    const r = await api(`/api/projects/${S.p.id}/sentences/${i}/reference?provider=${$("#provider").value}`, { method: "POST" });
    s.ref_url = r.ref_url; s.ref_provider = r.ref_provider;
  } catch {}
}

function playRef() {
  if (!cur().ref_url) return toast("No reference audio yet.");
  S.youAudio.pause();
  S.refAudio.playbackRate = S.rate;
  S.refAudio.currentTime = 0;
  S.refAudio.play();
}

function playUntilEnd(a) {
  return new Promise((res) => { a.onended = () => { a.onended = null; res(); }; a.currentTime = 0; a.play().catch(res); });
}

async function abCompare(takeUrl) {
  const t = typeof takeUrl === "string" ? takeUrl : (cur().takes.find((x) => x.kept) || cur().takes.at(-1))?.url;
  if (!t) return toast("Record a take first.");
  const loop = S.refAudio.loop; S.refAudio.loop = false;
  S.refAudio.playbackRate = 1;
  if (cur().ref_url) { await playUntilEnd(S.refAudio); await new Promise((r) => setTimeout(r, 350)); }
  S.youAudio.src = t;
  await playUntilEnd(S.youAudio);
  S.refAudio.loop = loop; S.refAudio.playbackRate = S.rate;
}

// ---- recording ----
async function toggleRec() {
  if (S.rec && S.rec.state === "recording") { S.rec.stop(); return; }
  try {
    S.stream = S.stream || await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
  } catch (e) { return toast("Microphone permission is needed to record."); }
  S.refAudio.pause(); S.youAudio.pause();
  const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
  S.rec = new MediaRecorder(S.stream, mime ? { mimeType: mime, audioBitsPerSecond: 128000 } : undefined);
  S.chunks = [];
  S.rec.ondataavailable = (e) => e.data.size && S.chunks.push(e.data);
  S.rec.onstop = onRecorded;
  S.rec.start();
  S.t0 = Date.now();
  $("#rec").classList.add("on");
  S.timer = setInterval(() => { const s = Math.floor((Date.now() - S.t0) / 1000); $("#rec-time").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }, 250);
}

async function onRecorded() {
  clearInterval(S.timer);
  $("#rec").classList.remove("on");
  const type = S.rec.mimeType || "audio/webm";
  const blob = new Blob(S.chunks, { type });
  if (blob.size < 2000) return toast("That recording was empty — try again.");
  const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
  const i = S.i;
  const analyse = $("#auto-an").checked;
  const localUrl = URL.createObjectURL(blob);
  const ab = $("#auto-ab").checked ? abCompare(localUrl) : Promise.resolve();
  const fd = new FormData();
  fd.append("file", blob, `take.${ext}`);
  fd.append("analyse", analyse ? "true" : "false");
  if (analyse) { $("#analysing").hidden = false; $("#results").hidden = true; }
  try {
    const res = await api(`/api/projects/${S.p.id}/sentences/${i}/takes`, { method: "POST", body: fd });
    S.p.sentences[i].takes.push(res.take);
    await ab;
    if (i === S.i) { renderTakes(); if (res.analysis) showAnalysis(res.analysis); }
    renderList();
  } catch (e) { toast(e.message, 6000); }
  finally { $("#analysing").hidden = true; }
}

// ---- takes ----
function renderTakes() {
  const box = $("#takes");
  const takes = cur().takes;
  box.innerHTML = takes.length ? "" : `<p class="muted small">No takes yet. Listen to the reference, then press R and say the line.</p>`;
  takes.slice().reverse().forEach((t, k) => {
    const el = document.createElement("div");
    el.className = "take" + (t.kept ? " kept" : "");
    const sc = t.scores?.overall;
    el.innerHTML = `<button class="play">▶</button><span class="small muted">Take ${takes.length - k}</span>
      <span class="sc" style="color:var(--${tone(sc) || "mute"})">${sc != null ? `${Math.round(sc)}/100` : "—"}</span>
      <button class="show">Feedback</button><button class="cmp">A/B</button>
      <button class="star ${t.kept ? "on" : ""}" title="Use this take in the final voiceover (K)">★ Keep</button><button class="del">✕</button>`;
    $(".play", el).onclick = () => { S.refAudio.pause(); S.youAudio.src = t.url; S.youAudio.play(); };
    $(".cmp", el).onclick = () => abCompare(t.url);
    $(".show", el).onclick = async () => {
      try { showAnalysis(await api(`/api/projects/${S.p.id}/takes/${t.id}`)); }
      catch { $("#analysing").hidden = false; try { showAnalysis(await api(`/api/projects/${S.p.id}/sentences/${S.i}/takes/${t.id}/analyse`, { method: "POST" })); } catch (e) { toast(e.message); } finally { $("#analysing").hidden = true; } }
    };
    $(".star", el).onclick = () => keepTake(t.id);
    $(".del", el).onclick = async () => {
      const s = await api(`/api/projects/${S.p.id}/sentences/${S.i}/takes/${t.id}`, { method: "DELETE" });
      cur().takes = s.takes; renderTakes(); renderList();
    };
    box.appendChild(el);
  });
}

async function keepTake(tid) {
  const s = await api(`/api/projects/${S.p.id}/sentences/${S.i}/takes/${tid}/keep`, { method: "POST" });
  cur().takes = s.takes; renderTakes(); renderList();
}

// ---- analysis display ----
function renderLine() {
  const a = S.lastAnalysis;
  const el = $("#line-text");
  if (!a?.words) { el.textContent = cur().text; return; }
  const flagged = new Set((a.notes?.top || []).filter((n) => n.word != null).map((n) => n.word));
  el.innerHTML = a.words.map((w, i) => {
    const cls = w.error === "Omission" ? "miss" : tone(w.acc);
    return `<span class="w ${cls} ${flagged.has(i) ? "flag" : ""} ${S.selWord === i ? "sel" : ""}" data-i="${i}">${esc(w.word)}</span>`;
  }).join(" ");
  $$(".w", el).forEach((s) => (s.onclick = () => wordDetail(+s.dataset.i)));
}

function wordDetail(i) {
  const a = S.lastAnalysis;
  const w = a?.words?.[i];
  if (!w) return;
  S.selWord = i; renderLine();
  const box = $("#word-detail");
  box.hidden = false;
  if (w.error === "Omission") { box.innerHTML = `<b>${esc(w.word)}</b> — not heard in your take.`; return; }
  box.innerHTML = `<b>${esc(w.word)}</b> <span class="muted">· ${Math.round(w.acc ?? 0)}/100${w.heard ? ` · heard as “${esc(w.heard)}”` : ""}${w.syllables?.length > 1 ? ` · syllables: ${w.syllables.map(esc).join(" · ")}` : ""}</span>
    <div>${w.phonemes.map((p) => `<span class="ph ${tone(p.acc)}">/${esc(p.ph)}/<small>${p.acc != null ? Math.round(p.acc) : "–"}${p.acc < 60 && p.alt ? ` → /${esc(p.alt)}/` : ""}</small></span>`).join("")}</div>
    <div class="muted small" style="margin-top:6px">Each box is one sound. The number is how American it sounded; “→” shows what it sounded like instead.</div>`;
}

function showAnalysis(a) {
  S.lastAnalysis = a;
  $("#results").hidden = false;
  const sc = a.scores;
  $("#scores").innerHTML = sc
    ? [["overall", "Overall"], ["accuracy", "Sounds"], ["prosody", "Melody & rhythm"], ["fluency", "Fluency"], ["completeness", "Completeness"]]
        .map(([k, l]) => `<div class="score"><b style="color:var(--${tone(sc[k]) || "mute"})">${sc[k] != null ? Math.round(sc[k]) : "–"}</b><span>${l}</span></div>`).join("")
    : "";
  $("#warnings").innerHTML = (a.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("");
  $("#notes").innerHTML = (a.notes?.top || []).map((n) =>
    `<li class="k-${n.kind}" ${n.word != null ? `data-word="${n.word}"` : ""}><span class="lab">${esc(n.label)}</span>${esc(n.text)}</li>`).join("");
  $$("#notes li[data-word]").forEach((li) => (li.onclick = () => wordDetail(+li.dataset.word)));
  const st = a.stats || {};
  const bits = [];
  if (st.rate) bits.push(`Pace: you ${st.rate.you} vs reference ${st.rate.ref} words/sec`);
  if (st.range) bits.push(`Pitch movement: you ${st.range.you} vs reference ${st.range.ref} semitones`);
  if (st.target) bits.push(`Target ${esc(st.target.name)}: ${st.target.rate_wps ?? "–"} words/sec, ${st.target.range_st ?? "–"} semitones`);
  $("#stats").innerHTML = bits.join(" · ");
  renderLine();
  requestAnimationFrame(() => drawChart(a.chart));
}

function drawChart(ch) {
  const cv = $("#chart");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = 220;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const col = (n) => css.getPropertyValue(n).trim();
  if (!ch || (!ch.ref?.length && !ch.you?.length)) {
    g.fillStyle = col("--mute"); g.font = "13px Inter"; g.fillText("No melody comparison for this take.", 16, H / 2); return;
  }
  const all = [...ch.ref, ...ch.you];
  const ts = all.map((p) => p[0]);
  let x0 = Math.min(...ts), x1 = Math.max(...ts);
  if (x1 - x0 < 1e-3) x1 = x0 + 1;
  const padL = 34, padR = 10, padT = 12, padB = ch.words?.length ? 34 : 14;
  const Y = 9; // ±9 semitones around each speaker's own median
  const X = (t) => padL + ((t - x0) / (x1 - x0)) * (W - padL - padR);
  const Yp = (v) => padT + ((Y - Math.max(-Y, Math.min(Y, v))) / (2 * Y)) * (H - padT - padB);
  g.strokeStyle = col("--line"); g.lineWidth = 1; g.fillStyle = col("--mute"); g.font = "11px JetBrains Mono";
  for (const v of [-6, 0, 6]) { g.beginPath(); g.moveTo(padL, Yp(v)); g.lineTo(W - padR, Yp(v)); g.stroke(); g.fillText((v > 0 ? "+" : "") + v, 4, Yp(v) + 4); }
  (ch.words || []).forEach(([s, e, w], k) => {
    g.strokeStyle = col("--line"); g.beginPath(); g.moveTo(X(s), padT); g.lineTo(X(s), H - padB + 4); g.stroke();
    g.fillStyle = col("--mute"); g.font = "11px Inter";
    const lbl = w.length > 12 ? w.slice(0, 11) + "…" : w;
    g.fillText(lbl, X(s) + 2, H - (k % 2 ? 6 : 19));
  });
  const line = (pts, color, width) => {
    g.strokeStyle = color; g.lineWidth = width; g.lineJoin = "round"; g.beginPath();
    let pen = false, lastT = null;
    for (const [t, v] of pts) {
      if (v == null || (lastT != null && t - lastT > 0.08 && ch.aligned)) { pen = false; }
      if (v == null) { lastT = null; continue; }
      pen ? g.lineTo(X(t), Yp(v)) : g.moveTo(X(t), Yp(v));
      pen = true; lastT = t;
    }
    g.stroke();
  };
  line(ch.ref, col("--ref"), 3);
  line(ch.you, col("--you"), 2.2);
}
window.addEventListener("resize", () => S.lastAnalysis && view === "session" && drawChart(S.lastAnalysis.chart));

async function exportVO() {
  const b = $("#export"); b.disabled = true; b.textContent = "Building…";
  try {
    const r = await api(`/api/projects/${S.p.id}/export`, { method: "POST" });
    $("#export-out").innerHTML = `<p>${r.seconds}s voiceover ready: <a href="${r.mp3}" download>MP3</a> · <a href="${r.wav}" download>WAV</a></p>
      ${r.missing.length ? `<p class="muted">Not recorded yet: ${r.missing.join(", ")}</p>` : ""}<audio controls src="${r.mp3}" style="width:100%"></audio>`;
  } catch (e) { toast(e.message); }
  finally { b.disabled = false; b.textContent = "Build final voiceover"; }
}

// ================= Speakers =================
async function showSpeakers() {
  mount("#tpl-speakers");
  $("#sp-add").onclick = async () => {
    try { await api("/api/speakers/youtube", { json: { url: $("#sp-url").value, name: $("#sp-name").value } }); $("#sp-url").value = ""; refreshSpeakers(); }
    catch (e) { toast(e.message, 6000); }
  };
  $("#sp-upload").onclick = async () => {
    const f = $("#sp-file").files[0];
    if (!f) return toast("Choose an audio or video file first.");
    const fd = new FormData(); fd.append("file", f); fd.append("name", $("#sp-name").value || f.name);
    try { await api("/api/speakers/upload", { method: "POST", body: fd }); $("#sp-file").value = ""; refreshSpeakers(); }
    catch (e) { toast(e.message, 6000); }
  };
  await refreshSpeakers();
  speakerPoll = setInterval(refreshSpeakers, 4000);
}

const clipAudio = new Audio();
async function refreshSpeakers() {
  if (view !== "speakers") return;
  const [list, settings] = await Promise.all([api("/api/speakers"), api("/api/settings")]);
  const box = $("#speaker-list");
  if (!box) return;
  const open = new Set($$(".sp-card.open", box).map((x) => x.dataset.id));
  box.innerHTML = list.length ? "" : `<p class="muted">No speakers yet.</p>`;
  for (const sp of list) {
    const pr = sp.profile || {};
    const isTarget = settings.target_speaker === sp.id;
    const el = document.createElement("div");
    el.className = "sp-card" + (open.has(sp.id) ? " open" : "");
    el.dataset.id = sp.id;
    el.innerHTML = `<div class="hd"><div><b>${esc(sp.name || "Speaker")}</b> <span class="muted small">${esc(sp.title || "")}</span></div>
        <span class="status ${sp.status}">${sp.status === "ready" ? (isTarget ? "★ target" : "ready") : esc(sp.status)}</span></div>
      ${sp.status_text ? `<p class="small ${sp.status === "error" ? "" : "muted"}">${esc(sp.status_text)}</p>` : ""}
      ${sp.status === "ready" ? `<div class="metrics">
          <div><b>${pr.rate_wps ?? "–"}</b><span>words / sec</span></div>
          <div><b>${pr.range_st ?? "–"}</b><span>pitch movement (st)</span></div>
          <div><b>${pr.pauses_per_100w ?? "–"}</b><span>pauses / 100 words</span></div>
          <div><b>${pr.median_hz ?? "–"} Hz</b><span>voice pitch</span></div></div>
        <div class="sp-actions">
          <button class="primary prac">Shadow their sentences (${sp.clips?.length || 0})</button>
          <button class="ghost tgt">${isTarget ? "Stop matching" : "Match my style to them"}</button>
          <button class="ghost tog">${open.has(sp.id) ? "Hide clips" : "Show clips"}</button>
          <button class="ghost del">Delete</button></div>
        <div class="clips" ${open.has(sp.id) ? "" : "hidden"}>${(sp.clips || []).map((c) => `<div data-u="${c.url}">▶ ${esc(c.text)}</div>`).join("")}</div>`
      : `<div class="sp-actions"><button class="ghost del">Delete</button></div>`}`;
    $(".del", el).onclick = async () => { if (confirm("Delete this speaker?")) { await api(`/api/speakers/${sp.id}`, { method: "DELETE" }); refreshSpeakers(); } };
    if (sp.status === "ready") {
      $(".prac", el).onclick = async () => { const p = await api(`/api/speakers/${sp.id}/practice`, { method: "POST" }); go("session", p.id); };
      $(".tgt", el).onclick = async () => { await api("/api/settings", { json: { target_speaker: isTarget ? "" : sp.id } }); toast(isTarget ? "Style matching off." : `Your takes will now be compared with ${sp.name}'s style too.`); refreshSpeakers(); };
      $(".tog", el).onclick = () => { el.classList.toggle("open"); $(".clips", el).hidden = !el.classList.contains("open"); $(".tog", el).textContent = el.classList.contains("open") ? "Hide clips" : "Show clips"; };
      $$(".clips div", el).forEach((d) => (d.onclick = () => { clipAudio.src = d.dataset.u; clipAudio.play(); }));
    }
    box.appendChild(el);
  }
}

// ================= Settings =================
async function showSettings() {
  mount("#tpl-settings");
  const [s, sps] = await Promise.all([api("/api/settings"), api("/api/speakers")]);
  $("#target-select").innerHTML = `<option value="">— nobody, just the reference —</option>` +
    sps.filter((x) => x.status === "ready").map((x) => `<option value="${x.id}">${esc(x.name || x.title)}</option>`).join("");
  $$(`input[name=tts_provider]`).forEach((r) => (r.checked = r.value === s.tts_provider));
  $$("[data-k]").forEach((el) => {
    const k = el.dataset.k;
    if (el.type === "password") el.placeholder = s[k + "_set"] ? `Saved ${s[k + "_hint"]}` : "Not set";
    else el.value = s[k] ?? "";
  });
  $("#gemini-find").onclick = async () => {
    const box = $("#gemini-found");
    box.textContent = "Looking…";
    try {
      const vs = await api("/api/gemini/voices");
      box.innerHTML = vs.length ? vs.map((v) => `<span class="found-voice" data-id="${esc(v.id)}"><b>${esc(v.name)}</b> · ${esc(v.gender || "")} ${esc(v.accent || "")}<br><span class="muted">${esc(v.description || "")}</span></span>`).join("")
        : "No American voices returned. The built-in voices in the list still work.";
      $$(".found-voice", box).forEach((el) => (el.onclick = () => { $('[data-k="gemini_voice"]').value = el.dataset.id; toast("Voice picked. Save settings to use it."); }));
    } catch (e) { box.textContent = e.message; }
  };
  $("#save-settings").onclick = async () => {
    const patch = { tts_provider: $("input[name=tts_provider]:checked")?.value || "azure" };
    $$("[data-k]").forEach((el) => {
      let v = el.value;
      if (el.type === "number") v = v === "" ? null : +v;
      if (v === null) return;
      patch[el.dataset.k] = v;
    });
    try { await api("/api/settings", { json: patch }); toast("Settings saved."); showSettings(); }
    catch (e) { toast(e.message); }
  };
}

// ================= boot =================
const h = location.hash.slice(1);
if (h.startsWith("s/")) go("session", h.slice(2));
else go(["speakers", "settings"].includes(h) ? h : "scripts");
