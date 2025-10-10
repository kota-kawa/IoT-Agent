/* =========================================================
 * IoT ダッシュボード（グリーンテーマ）
 * - 初期デバイス4種（温度/湿度/ランプ/ファン）
 * - 追加・削除、校正（センサー値ランダム化）、トグル操作
 * - チャットから自然言語で操作（例: ランプをオン）
 * - 状態を localStorage に保存
 * ======================================================= */

const LS_KEY = "iot_green_dashboard.devices.v1";

/** ---------- ユーティリティ ---------- */
const $ = (sel, parent = document) => parent.querySelector(sel);
const $$ = (sel, parent = document) => Array.from(parent.querySelectorAll(sel));
const nowTime = () => {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
};
const uid = () => Math.random().toString(36).slice(2, 10);

/** ---------- デバイスモデル ---------- */
const initialDevices = () => ([
  {
    id: uid(),
    kind: "sensor-temp",
    name: "温度センサー",
    meta: "センサー",
    value: 24.3,
    unit: "℃"
  },
  {
    id: uid(),
    kind: "sensor-humid",
    name: "湿度センサー",
    meta: "センサー",
    value: 55.4,
    unit: "%"
  },
  {
    id: uid(),
    kind: "actuator-lamp",
    name: "ランプ",
    meta: "アクチュエータ",
    on: false
  },
  {
    id: uid(),
    kind: "actuator-fan",
    name: "ファン",
    meta: "アクチュエータ",
    on: false
  }
]);

function loadDevices(){
  try{
    const raw = localStorage.getItem(LS_KEY);
    if(!raw) return initialDevices();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : initialDevices();
  }catch(e){
    console.warn("Failed to load devices:", e);
    return initialDevices();
  }
}
function saveDevices(devs){
  localStorage.setItem(LS_KEY, JSON.stringify(devs));
}

/** ---------- アイコン ---------- */
function iconFor(kind){
  // シンプルなSVG（埋め込み）
  switch(kind){
    case "sensor-temp":
      return `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M10 13.5V5a2 2 0 1 1 4 0v8.5a4.5 4.5 0 1 1-4 0Z" stroke="currentColor" stroke-width="2"/>
          <path d="M12 6h4" stroke="currentColor" stroke-width="2"/>
        </svg>`;
    case "sensor-humid":
      return `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 3s6 6.4 6 10a6 6 0 1 1-12 0c0-3.6 6-10 6-10Z" stroke="currentColor" stroke-width="2"/>
        </svg>`;
    case "actuator-lamp":
      return `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 3c4.4 0 8 3.6 8 8 0 3.3-2 6.1-5 7.3V21h-6v-2.7C6 17.1 4 14.3 4 11c0-4.4 3.6-8 8-8Z" stroke="currentColor" stroke-width="2"/>
        </svg>`;
    case "actuator-fan":
      return `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="2" stroke="currentColor" stroke-width="2"/>
          <path d="M10 5c0 2 1.5 4 6 4-1 3-3.5 3-5 1m-3 9c0-2 1.5-4 6-4-1-3-3.5-3-5-1M5 10c2 0 4 1.5 4 6 3-1 3-3.5 1-5" stroke="currentColor" stroke-width="2"/>
        </svg>`;
    default: return "";
  }
}

/** ---------- ビュー描画 ---------- */
const gridEl = $("#deviceGrid");

function render(){
  const devices = loadDevices();
  gridEl.innerHTML = "";
  for(const d of devices){
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.id = d.id;

    const head = document.createElement("div");
    head.className = "card__head";

    // Title + badge
    const title = document.createElement("div");
    title.className = "card__title";
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.innerHTML = iconFor(d.kind);
    const ttl = document.createElement("div");
    ttl.innerHTML = `<div>${d.name}</div><div class="card__meta">${d.meta}</div>`;
    title.appendChild(badge); title.appendChild(ttl);

    // Tools
    const tools = document.createElement("div");
    tools.className = "card__tools";
    const edit = document.createElement("button");
    edit.className = "iconbtn";
    edit.title = "名前の変更";
    edit.innerHTML = "✎";
    edit.addEventListener("click", () => renameDevice(d.id));
    const del = document.createElement("button");
    del.className = "iconbtn";
    del.title = "削除";
    del.innerHTML = "🗑";
    del.addEventListener("click", () => deleteDevice(d.id));
    tools.appendChild(edit); tools.appendChild(del);

    head.appendChild(title); head.appendChild(tools);

    // Body
    const body = document.createElement("div");
    body.className = "card__body";

    if(d.kind.startsWith("sensor")){
      // センサー UI
      const label = document.createElement("div");
      label.className = "pill";
      label.textContent = "現在値";

      const value = document.createElement("div");
      value.className = "sensor__value";
      value.textContent = `${Number(d.value).toFixed(1)}${d.unit || ""}`;

      const row = document.createElement("div");
      row.className = "row";
      const calib = document.createElement("button");
      calib.className = "btn btn--tiny btn--ghost";
      calib.textContent = "校正";
      calib.addEventListener("click", () => calibrateSensor(d.id));

      row.appendChild(calib);
      body.appendChild(label);
      body.appendChild(value);
      body.appendChild(row);
    }else{
      // アクチュエータ UI
      const status = document.createElement("div");
      status.className = "pill";
      status.innerHTML = `<span>現在の状態</span>`;

      const row = document.createElement("div");
      row.className = "row";

      const sw = document.createElement("label");
      sw.className = "switch";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!d.on;
      input.setAttribute("aria-label", `${d.name} の電源`);
      const dot = document.createElement("span");
      dot.className = "state-dot";
      sw.appendChild(input); sw.appendChild(dot);

      const stateText = document.createElement("div");
      stateText.className = "pill";
      stateText.textContent = d.on ? "ON" : "OFF";
      stateText.style.borderColor = "rgba(255,255,255,.10)";

      input.addEventListener("change", () => {
        toggleActuator(d.id, input.checked);
      });

      row.appendChild(sw);
      row.appendChild(stateText);

      body.appendChild(status);
      body.appendChild(row);
    }

    card.appendChild(head);
    card.appendChild(body);
    gridEl.appendChild(card);
  }
}

/** ---------- 操作ハンドラ ---------- */
function renameDevice(id){
  const devices = loadDevices();
  const t = devices.find(x => x.id === id);
  if(!t) return;
  const name = prompt("新しい名前を入力:", t.name);
  if(!name) return;
  t.name = name.trim();
  saveDevices(devices);
  render();
}

function deleteDevice(id){
  const devices = loadDevices().filter(x => x.id !== id);
  saveDevices(devices);
  render();
}

function toggleActuator(id, on){
  const devices = loadDevices();
  const t = devices.find(x => x.id === id);
  if(!t) return;
  t.on = !!on;
  saveDevices(devices);
  // 表示の更新（テキストだけ即時更新）
  const card = gridEl.querySelector(`.card[data-id="${id}"]`);
  if(card){
    const pill = card.querySelector(".card__body .row .pill");
    if(pill) pill.textContent = on ? "ON" : "OFF";
  }
}

function calibrateSensor(id){
  const devices = loadDevices();
  const t = devices.find(x => x.id === id);
  if(!t) return;
  if(t.kind === "sensor-temp"){
    t.value = Math.round((18 + Math.random() * 12) * 10) / 10; // 18〜30℃
    t.unit = "℃";
  }else if(t.kind === "sensor-humid"){
    t.value = Math.round((35 + Math.random() * 45) * 10) / 10; // 35〜80%
    t.unit = "%";
  }
  saveDevices(devices);
  render();
}

function addDevice(kind, name){
  const devices = loadDevices();
  const id = uid();
  const d = { id, kind, name: name || defaultName(kind) };
  if(kind.startsWith("sensor")){
    if(kind === "sensor-temp"){
      d.meta = "センサー"; d.value = 24.0; d.unit = "℃";
    }else{
      d.meta = "センサー"; d.value = 50.0; d.unit = "%";
    }
  }else{
    d.meta = "アクチュエータ"; d.on = false;
  }
  devices.push(d);
  saveDevices(devices);
  render();
}
function defaultName(kind){
  switch(kind){
    case "sensor-temp": return "温度センサー";
    case "sensor-humid": return "湿度センサー";
    case "actuator-lamp": return "ランプ";
    case "actuator-fan": return "ファン";
    default: return "デバイス";
  }
}

/** ---------- 追加/リセット UI ---------- */
const addBtn = $("#addDeviceBtn");
const resetBtn = $("#resetBtn");
const dlg = $("#addDialog");
const addForm = $("#addDeviceForm");
const deviceKindSel = $("#deviceKind");
const deviceNameInp = $("#deviceName");

addBtn.addEventListener("click", () => {
  deviceKindSel.value = "sensor-temp";
  deviceNameInp.value = "";
  dlg.showModal();
});

addForm.addEventListener("close", (e) => {
  // nothing
});
addForm.addEventListener("submit", (e) => e.preventDefault());
dlg.addEventListener("close", () => {
  if(dlg.returnValue === "confirm"){
    const kind = deviceKindSel.value;
    const name = deviceNameInp.value.trim();
    addDevice(kind, name);
  }
});

resetBtn.addEventListener("click", () => {
  if(confirm("ダッシュボードを初期化しますか？（保存データは削除されます）")){
    saveDevices(initialDevices());
    render();
  }
});

/** ---------- チャット：超軽量LLMもどき（デモ用） ---------- */
const logEl = $("#chatLog");
const formEl = $("#chatForm");
const inputEl = $("#chatInput");
const sendBtn = $("#sendBtn");
const pauseBtn = $("#pauseBtn");
const chatResetBtn = $("#chatResetBtn");
const INITIAL_GREETING = "こんにちは！右側のカードを直接操作するか、チャットで指示してください。例:「ランプをオン」「温度を25にして」";
let isPaused = false;
let isSending = false;

function updateChatControls(){
  if(!sendBtn || !inputEl) return;
  const disableSend = isPaused || isSending;
  sendBtn.disabled = disableSend;
  inputEl.disabled = isPaused;
  if(pauseBtn){
    pauseBtn.classList.toggle("is-active", isPaused);
    pauseBtn.setAttribute("aria-pressed", String(isPaused));
  }
}

function pushMessage(role, text){
  const item = document.createElement("div");
  item.className = `message message--${role}`;
  item.innerHTML = `
    <div class="message__avatar">${role === "user" ? "👤" : "🤖"}</div>
    <div>
      <div class="message__bubble">${escapeHtml(text)}</div>
      <div class="message__meta">${role === "user" ? "あなた" : "LLM"} ・ ${nowTime()}</div>
    </div>
  `;
  logEl.appendChild(item);
  logEl.scrollTop = logEl.scrollHeight;
}

function escapeHtml(s){
  return s.replace(/[&<>"']/g, m => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"
  }[m]));
}

// 簡易NLU：日本語/かな混じり指示を解析
function handleChatCommand(text){
  const t = text.replace(/\s+/g, "");

  // ランプ
  if(/ランプ.*(オン|つけ|点け|起動)/.test(t)) {
    setActuatorByName(/ランプ/, true);
    return "ランプをオンにしました。";
  }
  if(/ランプ.*(オフ|消|停止)/.test(t)) {
    setActuatorByName(/ランプ/, false);
    return "ランプをオフにしました。";
  }

  // ファン
  if(/ファン.*(オン|つけ|回|起動)/.test(t)) {
    setActuatorByName(/ファン/, true);
    return "ファンをオンにしました。";
  }
  if(/ファン.*(オフ|止|停止|消)/.test(t)) {
    setActuatorByName(/ファン/, false);
    return "ファンをオフにしました。";
  }

  // 温度値の設定（例: 温度を25にして, 温度25.4）
  const tempMatch = t.match(/温度(を)?([0-9]+(?:\.[0-9]+)?)?/);
  if(tempMatch && tempMatch[2]){
    const v = parseFloat(tempMatch[2]);
    setSensorValue("sensor-temp", v, "℃");
    return `温度を ${v.toFixed(1)}℃ に設定しました。`;
  }

  // 湿度
  const humMatch = t.match(/湿度(を)?([0-9]+(?:\.[0-9]+)?)?/);
  if(humMatch && humMatch[2]){
    const v = parseFloat(humMatch[2]);
    setSensorValue("sensor-humid", v, "%");
    return `湿度を ${v.toFixed(1)}% に設定しました。`;
  }

  // 状態の読み上げ
  if(/状態|ステータス|確認|教えて/.test(t)){
    const s = summarizeState();
    return s;
  }

  return "了解しました。チャットからも操作できます。例: 「ランプをオン」「ファンをオフ」「温度を25.5にして」「湿度60%にして」「状態を教えて」。";
}

function setActuatorByName(regex, on){
  const ds = loadDevices();
  const target = ds.find(d => d.kind.startsWith("actuator") && regex.test(d.name));
  if(target){ target.on = !!on; saveDevices(ds); render(); }
}

function setSensorValue(kind, value, unit){
  const ds = loadDevices();
  const target = ds.find(d => d.kind === kind);
  if(target){
    target.value = Number(value); target.unit = unit;
    saveDevices(ds); render();
  }
}

function summarizeState(){
  const ds = loadDevices();
  const t = ds.find(d => d.kind === "sensor-temp");
  const h = ds.find(d => d.kind === "sensor-humid");
  const lamp = ds.find(d => d.kind === "actuator-lamp");
  const fan = ds.find(d => d.kind === "actuator-fan");
  const parts = [];
  if(t) parts.push(`温度 ${t.value.toFixed(1)}℃`);
  if(h) parts.push(`湿度 ${h.value.toFixed(1)}%`);
  if(lamp) parts.push(`ランプ ${lamp.on ? "ON" : "OFF"}`);
  if(fan) parts.push(`ファン ${fan.on ? "ON" : "OFF"}`);
  return parts.join(" ・ ");
}

// フォーム送信
formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if(isPaused || isSending) return;
  const text = inputEl.value.trim();
  if(!text) return;
  pushMessage("user", text);
  inputEl.value = "";
  isSending = true;
  updateChatControls();

  // 疑似レスポンス
  setTimeout(() => {
    const reply = handleChatCommand(text);
    pushMessage("assistant", reply);
    isSending = false;
    updateChatControls();
  }, 450);
});

if(pauseBtn){
  pauseBtn.addEventListener("click", () => {
    isPaused = !isPaused;
    updateChatControls();
    if(!isPaused){
      inputEl.focus();
    }
  });
}

if(chatResetBtn){
  chatResetBtn.addEventListener("click", () => {
    logEl.innerHTML = "";
    pushMessage("assistant", INITIAL_GREETING);
    isPaused = false;
    isSending = false;
    updateChatControls();
  });
}

/** ---------- 初期化 ---------- */
(function init(){
  if(!localStorage.getItem(LS_KEY)){
    saveDevices(initialDevices());
  }
  render();

  // 初期メッセージ
  pushMessage("assistant", INITIAL_GREETING);
  updateChatControls();
})();
