/* Cozmo Companion — WebSocket client */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------- i18n ---------------- */
const I18N = {
  es: {
    offline: "Desconectado", connected: "Conectado", connecting: "Conectando...",
    connect: "Conectar", disconnect: "Desconectar",
    camera: "Cámara", cam_on: "Activar", cam_off: "Apagar",
    movement: "Movimiento", drive_hint: "Mantén pulsado o usa WASD / flechas",
    lift: "Brazo", head: "Cabeza", dance: "Bailar", look: "Explorar", lights: "Luces",
    chat: "Conversación", chat_ph: "Escríbele algo a Cozmo...",
    cmd_ph: "Comando: «cozmo avanza 2», «baila»...",
    emotions: "Emociones", listening: "Escuchando...", pet: "Mascota",
  },
  en: {
    offline: "Offline", connected: "Connected", connecting: "Connecting...",
    connect: "Connect", disconnect: "Disconnect",
    camera: "Camera", cam_on: "Enable", cam_off: "Disable",
    movement: "Movement", drive_hint: "Hold or use WASD / arrow keys",
    lift: "Lift", head: "Head", dance: "Dance", look: "Look around", lights: "Lights",
    chat: "Conversation", chat_ph: "Say something to Cozmo...",
    cmd_ph: "Command: \"cozmo forward 2\", \"dance\"...",
    emotions: "Emotions", listening: "Listening...", pet: "Pet",
  },
};
let lang = "es";
const t = (k) => (I18N[lang] && I18N[lang][k]) || k;

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  $("btn-lang").textContent = lang === "es" ? "EN" : "ES";
  document.documentElement.lang = lang;
  updateConnUI();
}

/* ---------------- State ---------------- */
let ws = null;
let reconnectTimer = null;
let state = { connected: false, camera: false, stt_listening: false, stt_available: false };
let cameraOn = false;

const EMOTION_EMOJIS = {
  happy: "😊", sad: "😢", curious: "🤔", excited: "🤩",
  tired: "😴", bored: "🙄", scared: "😨",
};
const SWATCH_COLORS = ["red", "green", "blue", "yellow", "purple", "orange", "white", "off"];
const SWATCH_RGB = {
  red: "#ff0000", green: "#00ff00", blue: "#0000ff", yellow: "#ffff00",
  purple: "#800080", orange: "#ffa500", white: "#ffffff", off: "#202020",
};

/* ---------------- WebSocket ---------------- */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => { log("WebSocket conectado", "ok"); send({ action: "get_status" }); };
  ws.onclose = () => {
    log("WebSocket cerrado, reintentando...", "warn");
    setConnState(false);
    reconnectTimer = setTimeout(connectWS, 2000);
  };
  ws.onerror = () => {};
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleMessage(msg);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function handleMessage(msg) {
  switch (msg.type) {
    case "status": onStatus(msg); break;
    case "camera": onCameraFrame(msg); break;
    case "log": log(msg.message, msg.level); break;
    case "error": log(msg.message, "error"); break;
    case "chat": addChat(msg.role, msg.text, msg.via); break;
    case "stt": onSTT(msg); break;
    case "photo": onPhoto(msg); break;
    case "pong": break;
  }
}

/* ---------------- Status ---------------- */
function onStatus(s) {
  const wasConnected = state.connected;
  state = { ...state, ...s };
  if (s.lang && s.lang !== lang) { lang = s.lang; applyI18n(); }

  $("version").textContent = "v" + (s.version || "?");
  setConnState(s.connected);
  $("battery").textContent = s.battery ? s.battery.toFixed(2) + "V" : "—";

  // Emotion
  if (s.emotion) {
    $("emotion-emoji").textContent = s.emotion.emoji || "🤔";
    $("emotion-name").textContent = s.emotion.name || "?";
    $("boredom-fill").style.width = Math.min(100, (s.emotion.boredom / 30) * 100) + "%";
    document.querySelectorAll(".emotion-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.emotion === s.emotion.name));
  }

  // Personalities
  if (s.personalities) {
    const sel = $("personality");
    if (sel.options.length !== Object.keys(s.personalities).length) {
      sel.innerHTML = "";
      for (const [key, label] of Object.entries(s.personalities)) {
        const opt = document.createElement("option");
        opt.value = key; opt.textContent = label;
        sel.appendChild(opt);
      }
    }
    sel.value = s.personality;
  }

  // STT button
  $("btn-mic").style.display = s.stt_available ? "" : "none";
  $("btn-mic").classList.toggle("listening", !!s.stt_listening);

  // Pet mode + memory
  $("btn-pet").classList.toggle("active", !!s.pet_mode);
  if (s.memory_count !== undefined) $("mem-count").textContent = s.memory_count;

  // Camera button
  cameraOn = !!s.camera;
  $("btn-camera").textContent = cameraOn ? t("cam_off") : t("cam_on");

  enableControls(s.connected);
  if (s.connected !== wasConnected) updateConnUI();
}

function setConnState(connected) {
  state.connected = connected;
  updateConnUI();
  enableControls(connected);
}

function updateConnUI() {
  const dot = $("conn-dot");
  dot.className = "dot" + (state.connected ? " on" : "");
  $("conn-text").textContent = state.connected ? t("connected") : t("offline");
  $("btn-connect").textContent = state.connected ? t("disconnect") : t("connect");
}

function enableControls(on) {
  ["btn-camera", "btn-photo", "btn-dance", "btn-look", "btn-stop",
   "slider-lift", "slider-head", "color-picker"].forEach((id) => { $(id).disabled = !on; });
  document.querySelectorAll(".dpad-btn").forEach((b) => { b.disabled = !on; });
  document.querySelectorAll(".swatch").forEach((s) => s.classList.toggle("disabled", !on));
}

/* ---------------- Camera ---------------- */
function onCameraFrame(msg) {
  const img = $("cam");
  img.src = "data:image/jpeg;base64," + msg.image;
  img.style.display = "block";
  $("cam-placeholder").style.display = "none";
}

function onPhoto(msg) {
  log("📸 " + (msg.saved || "photo"), "ok");
  if (msg.image) {
    const link = document.createElement("a");
    link.href = "data:image/jpeg;base64," + msg.image;
    link.download = msg.saved || "cozmo.jpg";
    link.click();
  }
}

/* ---------------- STT ---------------- */
function onSTT(msg) {
  const bar = $("stt-bar");
  if (msg.state === "listening") {
    bar.style.display = "flex";
    $("stt-partial").textContent = msg.partial || t("listening");
    $("btn-mic").classList.add("listening");
  } else {
    bar.style.display = "none";
    $("btn-mic").classList.remove("listening");
  }
}

/* ---------------- Chat ---------------- */
function addChat(role, text, via) {
  const box = $("chat-messages");
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = (via === "voice" ? "🎤 " : "") + text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

/* ---------------- Log ---------------- */
function log(message, level = "info") {
  const box = $("log");
  const div = document.createElement("div");
  div.className = "log-line " + level;
  const time = new Date().toLocaleTimeString();
  div.innerHTML = `<span class="log-time">${time}</span>`;
  div.appendChild(document.createTextNode(message));
  box.appendChild(div);
  while (box.children.length > 150) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

/* ---------------- Drive (hold-to-move) ---------------- */
const DRIVE_KEYS = {
  ArrowUp: [100, 100], w: [100, 100], W: [100, 100],
  ArrowDown: [-100, -100], s: [-100, -100], S: [-100, -100],
  ArrowLeft: [-100, 100], a: [-100, 100], A: [-100, 100],
  ArrowRight: [100, -100], d: [100, -100], D: [100, -100],
};
let driveInterval = null;

function startDrive(left, right, el) {
  stopDrive();
  if (el) el.classList.add("active");
  const pulse = () => send({ action: "drive", left, right, duration: 0.35 });
  pulse();
  driveInterval = setInterval(pulse, 300);
  driveInterval._el = el;
}

function stopDrive() {
  if (driveInterval) {
    clearInterval(driveInterval);
    if (driveInterval._el) driveInterval._el.classList.remove("active");
    driveInterval = null;
    send({ action: "stop" });
  }
}

document.querySelectorAll(".dpad-btn[data-drive]").forEach((btn) => {
  const [_, left, right] = btn.dataset.drive.split(",").map(Number);
  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); startDrive(left, right, btn); });
  btn.addEventListener("pointerup", stopDrive);
  btn.addEventListener("pointerleave", stopDrive);
});

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const speeds = DRIVE_KEYS[e.key];
  if (speeds && !driveInterval) {
    e.preventDefault();
    startDrive(speeds[0], speeds[1], null);
  } else if (e.key === " ") {
    e.preventDefault();
    send({ action: "dance" });
  }
});
document.addEventListener("keyup", (e) => {
  if (DRIVE_KEYS[e.key]) stopDrive();
});

/* ---------------- UI events ---------------- */
$("btn-connect").addEventListener("click", () => {
  if (state.connected) {
    $("conn-dot").className = "dot busy";
    $("conn-text").textContent = t("offline");
    send({ action: "disconnect" });
  } else {
    $("conn-dot").className = "dot busy";
    $("conn-text").textContent = t("connecting");
    send({ action: "connect" });
  }
});

$("btn-lang").addEventListener("click", () => {
  lang = lang === "es" ? "en" : "es";
  applyI18n();
  send({ action: "lang", lang });
});

$("btn-camera").addEventListener("click", () => {
  send({ action: "camera", enabled: !cameraOn });
  if (cameraOn) { $("cam").style.display = "none"; $("cam-placeholder").style.display = ""; }
});

$("btn-photo").addEventListener("click", () => send({ action: "photo" }));
$("btn-dance").addEventListener("click", () => send({ action: "dance" }));
$("btn-look").addEventListener("click", () => send({ action: "look" }));
$("btn-stop").addEventListener("click", () => send({ action: "stop" }));

function throttle(fn, ms) {
  let last = 0;
  return (...args) => {
    const now = Date.now();
    if (now - last >= ms) { last = now; fn(...args); }
  };
}
$("slider-lift").addEventListener("input", throttle((e) =>
  send({ action: "lift", value: e.target.value / 100 }), 100));
$("slider-head").addEventListener("input", throttle((e) =>
  send({ action: "head", value: e.target.value / 100 }), 100));

/* Lights */
const swatchesBox = $("swatches");
SWATCH_COLORS.forEach((name) => {
  const s = document.createElement("div");
  s.className = "swatch disabled";
  s.style.background = SWATCH_RGB[name];
  s.title = name;
  s.addEventListener("click", () => send({ action: "lights", color: name }));
  swatchesBox.appendChild(s);
});
$("color-picker").addEventListener("change", (e) =>
  send({ action: "lights", color: e.target.value }));

/* Emotions */
const emoBox = $("emotion-buttons");
Object.entries(EMOTION_EMOJIS).forEach(([name, emoji]) => {
  const b = document.createElement("button");
  b.className = "btn emotion-btn";
  b.dataset.emotion = name;
  b.textContent = emoji;
  b.title = name;
  b.addEventListener("click", () => send({ action: "emotion", name }));
  emoBox.appendChild(b);
});

/* Personality */
$("personality").addEventListener("change", (e) =>
  send({ action: "personality", name: e.target.value }));

/* Chat */
function sendChat() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  send({ action: "chat", text });
}
$("btn-chat-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

/* Typed command */
function sendCommand() {
  const input = $("cmd-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  send({ action: "command", text });
}
$("btn-cmd-send").addEventListener("click", sendCommand);
$("cmd-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendCommand(); });

/* Mic */
let listening = false;
$("btn-mic").addEventListener("click", () => {
  listening = !listening;
  send({ action: "stt", enabled: listening });
});

/* Pet mode */
$("btn-pet").addEventListener("click", () =>
  send({ action: "pet", enabled: !state.pet_mode }));

/* Memory clear */
$("btn-mem-clear").addEventListener("click", () => {
  if (confirm(lang === "es" ? "¿Borrar toda la memoria de Cozmo?" : "Clear all of Cozmo's memory?"))
    send({ action: "memory_clear" });
});

/* Log clear */
$("btn-clear-log").addEventListener("click", () => { $("log").innerHTML = ""; });

/* ---------------- Init ---------------- */
applyI18n();
connectWS();
setInterval(() => send({ action: "ping" }), 25000);
