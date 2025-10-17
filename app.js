/* =========================================================
 * IoT ダッシュボード（登録デバイス表示）
 * - サーバーから登録済みデバイス一覧を取得して表示
 * - デバイス登録ダイアログから任意のエッジデバイスを登録
 * - チャットはサーバー連携 + 簡易フォールバック応答
 * ======================================================= */

const FETCH_DEVICES_INTERVAL_MS = 5000;

/** ---------- ユーティリティ ---------- */
const $ = (sel, parent = document) => parent.querySelector(sel);
const nowTime = () => {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
};
const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (m) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]
  ));

/** ---------- デバイス描画 ---------- */
const gridEl = $("#deviceGrid");
const registerNoticeEl = $("#registerNotice");

let devices = [];
let isFetchingDevices = false;

function displayName(device){
  if(!device) return "";
  const meta = device.meta || {};
  if(typeof meta.display_name === "string" && meta.display_name.trim()){
    return meta.display_name.trim();
  }
  if(typeof meta.note === "string" && meta.note.trim()){
    return meta.note.trim();
  }
  if(typeof meta.label === "string" && meta.label.trim()){
    return meta.label.trim();
  }
  if(typeof meta.location === "string" && meta.location.trim()){
    return `${device.device_id} @ ${meta.location.trim()}`;
  }
  return device.device_id;
}

function formatTimestamp(ts){
  if(!ts && ts !== 0) return "-";
  const date = new Date(ts * 1000);
  if(Number.isNaN(date.getTime())){
    return String(ts);
  }
  return date.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatRelativeTime(ts){
  if(!ts && ts !== 0) return "未記録";
  const date = new Date(ts * 1000);
  if(Number.isNaN(date.getTime())){
    return String(ts);
  }
  const diff = Date.now() - date.getTime();
  if(diff < 0){
    return formatTimestamp(ts);
  }
  const sec = Math.floor(diff / 1000);
  if(sec < 5) return "たった今";
  if(sec < 60) return `${sec}秒前`;
  const min = Math.floor(sec / 60);
  if(min < 60) return `${min}分前`;
  const hours = Math.floor(min / 60);
  if(hours < 24) return `${hours}時間前`;
  const days = Math.floor(hours / 24);
  if(days < 7) return `${days}日前`;
  return formatTimestamp(ts);
}

function formatMetaValue(value){
  if(value === null) return "null";
  if(value === undefined) return "-";
  if(typeof value === "boolean") return value ? "true" : "false";
  if(typeof value === "number") return String(value);
  if(typeof value === "string") return value;
  try{
    return JSON.stringify(value);
  }catch(_err){
    return String(value);
  }
}

function createField(label, value){
  const wrapper = document.createElement("div");
  wrapper.className = "device-field";
  const labelEl = document.createElement("div");
  labelEl.className = "pill";
  labelEl.textContent = label;
  const valueEl = document.createElement("div");
  valueEl.className = "device-field__value";
  valueEl.textContent = value;
  wrapper.appendChild(labelEl);
  wrapper.appendChild(valueEl);
  return wrapper;
}

function renderCapabilities(capabilities){
  if(!Array.isArray(capabilities) || capabilities.length === 0){
    return null;
  }
  const section = document.createElement("div");
  section.className = "device-section";
  const label = document.createElement("div");
  label.className = "pill";
  label.textContent = "提供機能";
  section.appendChild(label);

  const list = document.createElement("ul");
  list.className = "capability-list";

  for(const cap of capabilities){
    const item = document.createElement("li");
    item.className = "capability-list__item";

    const name = document.createElement("div");
    name.className = "capability-list__name";
    name.textContent = typeof cap?.name === "string" && cap.name ? cap.name : "不明な機能";
    item.appendChild(name);

    if(typeof cap?.description === "string" && cap.description.trim()){
      const desc = document.createElement("div");
      desc.className = "capability-list__desc";
      desc.textContent = cap.description.trim();
      item.appendChild(desc);
    }

    if(Array.isArray(cap?.params) && cap.params.length){
      const param = document.createElement("div");
      param.className = "capability-list__params";
      const parts = [];
      for(const p of cap.params){
        if(!p || typeof p.name !== "string") continue;
        let text = p.name;
        if(p.type){
          text += ` (${p.type})`;
        }
        const extras = [];
        if(p.required){
          extras.push("必須");
        }
        if(Object.prototype.hasOwnProperty.call(p, "default")){
          extras.push(`既定=${p.default}`);
        }
        if(extras.length){
          text += ` [${extras.join(", ")}]`;
        }
        parts.push(text);
      }
      if(parts.length){
        param.textContent = parts.join(" / ");
        item.appendChild(param);
      }
    }

    list.appendChild(item);
  }

  section.appendChild(list);
  return section;
}

function renderMeta(meta){
  if(!meta || typeof meta !== "object"){
    return null;
  }
  const entries = Object.entries(meta).filter(([key, value]) =>
    key !== "display_name" && value !== "" && value !== null && value !== undefined
  );
  if(!entries.length){
    return null;
  }
  const section = document.createElement("div");
  section.className = "device-section";
  const label = document.createElement("div");
  label.className = "pill";
  label.textContent = "メタ情報";
  section.appendChild(label);

  const list = document.createElement("ul");
  list.className = "meta-list";
  for(const [key, value] of entries){
    const item = document.createElement("li");
    item.className = "meta-list__item";
    const keyEl = document.createElement("div");
    keyEl.className = "meta-list__key";
    keyEl.textContent = key;
    const valueEl = document.createElement("div");
    valueEl.className = "meta-list__value";
    valueEl.textContent = formatMetaValue(value);
    item.appendChild(keyEl);
    item.appendChild(valueEl);
    list.appendChild(item);
  }
  section.appendChild(list);
  return section;
}

function renderLastResult(result){
  if(!result || typeof result !== "object"){
    return null;
  }
  const section = document.createElement("div");
  section.className = "device-section";
  const label = document.createElement("div");
  label.className = "pill";
  label.textContent = "最後のジョブ";
  section.appendChild(label);

  const box = document.createElement("div");
  box.className = "device-result";
  const status = document.createElement("span");
  status.className = `device-result__status device-result__status--${result.ok ? "ok" : "error"}`;
  status.textContent = result.ok ? "成功" : "失敗";
  box.appendChild(status);

  const detail = document.createElement("div");
  detail.className = "device-result__detail";
  const detailLines = [];
  if(result.job_id){
    detailLines.push(`<div>ジョブID: ${escapeHtml(result.job_id)}</div>`);
  }
  if(Object.prototype.hasOwnProperty.call(result, "return_value")){
    const valueStr = escapeHtml(formatMetaValue(result.return_value));
    detailLines.push(`<div>戻り値: ${valueStr}</div>`);
  }
  if(detailLines.length){
    detail.innerHTML = detailLines.join("");
  }else{
    detail.textContent = "結果の詳細はありません。";
  }
  box.appendChild(detail);
  section.appendChild(box);
  return section;
}

function iconForDevice(){
  return `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="3" stroke="currentColor" stroke-width="2" />
      <path d="M7 9h10M7 13h6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
    </svg>`;
}

function renderDevices(){
  if(!gridEl) return;
  gridEl.innerHTML = "";

  if(!devices.length){
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p>登録されたデバイスがありません。</p>
      <p class="empty-state__hint">右上の「デバイス登録」から登録してください。</p>
    `;
    gridEl.appendChild(empty);
    return;
  }

  for(const device of devices){
    const card = document.createElement("article");
    card.className = "card";

    const head = document.createElement("div");
    head.className = "card__head";

    const title = document.createElement("div");
    title.className = "card__title";
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.innerHTML = iconForDevice();
    const titleText = document.createElement("div");
    const nameEl = document.createElement("div");
    nameEl.textContent = displayName(device);
    const metaEl = document.createElement("div");
    metaEl.className = "card__meta";
    metaEl.textContent = device.device_id;
    titleText.appendChild(nameEl);
    titleText.appendChild(metaEl);
    title.appendChild(badge);
    title.appendChild(titleText);

    head.appendChild(title);

    const tools = document.createElement("div");
    tools.className = "card__tools";
    const renameBtn = document.createElement("button");
    renameBtn.type = "button";
    renameBtn.className = "iconbtn";
    renameBtn.dataset.action = "rename";
    renameBtn.dataset.deviceId = device.device_id;
    renameBtn.title = "名前を変更";
    const ariaLabel = displayName(device) || device.device_id;
    renameBtn.setAttribute("aria-label", `${ariaLabel} の名前を変更`);
    renameBtn.textContent = "✏️";
    tools.appendChild(renameBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "iconbtn iconbtn--danger";
    deleteBtn.dataset.action = "delete";
    deleteBtn.dataset.deviceId = device.device_id;
    deleteBtn.title = "デバイスを削除";
    deleteBtn.setAttribute("aria-label", `${ariaLabel} を削除`);
    deleteBtn.textContent = "🗑️";
    tools.appendChild(deleteBtn);

    head.appendChild(tools);
    card.appendChild(head);

    const body = document.createElement("div");
    body.className = "card__body";
    body.appendChild(createField("最終アクセス", formatRelativeTime(device.last_seen)));
    body.appendChild(createField("登録日時", formatTimestamp(device.registered_at)));
    body.appendChild(createField("待機ジョブ", `${device.queue_depth || 0}件`));

    const capSection = renderCapabilities(device.capabilities);
    if(capSection){
      body.appendChild(capSection);
    }
    const metaSection = renderMeta(device.meta);
    if(metaSection){
      body.appendChild(metaSection);
    }
    const resultSection = renderLastResult(device.last_result);
    if(resultSection){
      body.appendChild(resultSection);
    }

    card.appendChild(body);
    gridEl.appendChild(card);
  }
}

async function fetchDevices({ silent = false } = {}){
  if(isFetchingDevices) return;
  isFetchingDevices = true;
  try{
    const res = await fetch("/api/devices", { cache: "no-store" });
    if(!res.ok){
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    if(Array.isArray(data.devices)){
      devices = data.devices;
    }else{
      devices = [];
    }
    renderDevices();
    if(registerNoticeEl?.dataset.kind === "error"){
      hideRegisterNotice();
    }
  }catch(err){
    console.error("Failed to fetch devices", err);
    if(!silent){
      showRegisterNotice(`デバイス一覧の取得に失敗しました: ${err.message}`, "error");
    }
  }finally{
    isFetchingDevices = false;
  }
}

async function updateDeviceDisplayName(deviceId, displayName){
  const payload = { display_name: displayName || null };
  const res = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/name`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const text = await res.text();
  let data = null;
  if(text){
    try{
      data = JSON.parse(text);
    }catch(_err){
      // ignore
    }
  }

  if(!res.ok){
    const message = (data && (data.error || data.message)) || text || `HTTP ${res.status}`;
    throw new Error(message);
  }

  return data?.device || null;
}

async function deleteDevice(deviceId){
  const res = await fetch(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: "DELETE",
  });

  const text = await res.text();
  let data = null;
  if(text){
    try{
      data = JSON.parse(text);
    }catch(_err){
      // ignore
    }
  }

  if(!res.ok){
    const message = (data && (data.error || data.message)) || text || `HTTP ${res.status}`;
    throw new Error(message);
  }

  return data;
}

/** ---------- デバイス登録モーダル ---------- */
const registerBtn = $("#registerDeviceBtn");
const registerDialog = $("#registerDialog");
const registerForm = $("#registerDeviceForm");
const registerDeviceIdInput = $("#registerDeviceId");
const registerNameInput = $("#registerDeviceName");
const registerNoteInput = $("#registerDeviceNote");
const registerDialogMessageEl = $("#registerDialogMessage");
const registerCancelBtn = $("#registerCancelBtn");
const registerSubmitBtn = $("#registerSubmitBtn");

const REGISTER_DIALOG_DEFAULT = registerDialogMessageEl
  ? registerDialogMessageEl.textContent.trim()
  : "エッジデバイスで使用する識別子を入力し、必要に応じて表示名やメモを設定します。";
let lastRegisteredDeviceId = null;
let lastRegisteredDeviceName = null;

function showRegisterNotice(message, kind = "info"){
  if(!registerNoticeEl) return;
  registerNoticeEl.hidden = false;
  registerNoticeEl.textContent = message;
  registerNoticeEl.className = "main__notice";
  registerNoticeEl.dataset.kind = kind;
  if(kind === "error"){
    registerNoticeEl.classList.add("main__notice--error");
  }else if(kind === "success"){
    registerNoticeEl.classList.add("main__notice--success");
  }
}

function hideRegisterNotice(){
  if(!registerNoticeEl) return;
  registerNoticeEl.hidden = true;
  registerNoticeEl.textContent = "";
  registerNoticeEl.className = "main__notice";
  delete registerNoticeEl.dataset.kind;
}

function setRegisterDialogMessage(message, kind = "info"){
  if(!registerDialogMessageEl) return;
  registerDialogMessageEl.textContent = message;
  registerDialogMessageEl.className = "form__hint";
  if(kind === "error"){
    registerDialogMessageEl.classList.add("form__hint--error");
  }else if(kind === "success"){
    registerDialogMessageEl.classList.add("form__hint--success");
  }
}

function clearRegisterDialog(){
  if(registerForm){
    registerForm.reset();
  }
  if(registerSubmitBtn){
    registerSubmitBtn.disabled = false;
    registerSubmitBtn.textContent = "登録";
  }
  setRegisterDialogMessage(REGISTER_DIALOG_DEFAULT);
}

async function handleRegisterSubmit(event){
  event.preventDefault();
  if(!registerSubmitBtn) return;

  const deviceId = registerDeviceIdInput ? registerDeviceIdInput.value.trim() : "";
  const displayNameInput = registerNameInput ? registerNameInput.value.trim() : "";
  const note = registerNoteInput ? registerNoteInput.value.trim() : "";

  if(!deviceId){
    setRegisterDialogMessage("デバイスIDを入力してください。", "error");
    if(registerDeviceIdInput){
      registerDeviceIdInput.focus();
    }
    return;
  }

  const capabilities = [];

  registerSubmitBtn.disabled = true;
  registerSubmitBtn.textContent = "登録中…";
  setRegisterDialogMessage("サーバーへ登録しています…");

  try{
    const payload = {
      device_id: deviceId,
      capabilities,
      meta: {
        registered_via: "dashboard",
      },
      approved: true,
    };
    if(displayNameInput){
      payload.meta.display_name = displayNameInput;
    }
    if(note){
      payload.meta.note = note;
    }

    const res = await fetch("/api/devices/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const text = await res.text();
    let data = null;
    if(text){
      try{
        data = JSON.parse(text);
      }catch(_err){
        // ignore JSON parse error and fall back to raw text
      }
    }

    if(!res.ok){
      const message = (data && (data.error || data.message)) || text || `HTTP ${res.status}`;
      throw new Error(message);
    }

    const registeredId = data && typeof data.device_id === "string" ? data.device_id : deviceId;
    const registeredDevice = data && data.device && typeof data.device === "object" ? data.device : null;
    lastRegisteredDeviceId = registeredId;
    if(registeredDevice){
      lastRegisteredDeviceName = displayName(registeredDevice);
    }else if(displayNameInput){
      lastRegisteredDeviceName = displayNameInput;
    }else{
      lastRegisteredDeviceName = null;
    }
    const successLabel = lastRegisteredDeviceName
      ? `${lastRegisteredDeviceName} (ID: ${registeredId})`
      : registeredId;
    setRegisterDialogMessage(`デバイス ${successLabel} を登録しました。`, "success");
    registerDialog?.close("success");
  }catch(err){
    const message = err instanceof Error ? err.message : String(err);
    setRegisterDialogMessage(`登録に失敗しました: ${message}`, "error");
  }finally{
    registerSubmitBtn.disabled = false;
    registerSubmitBtn.textContent = "登録";
  }
}

if(registerBtn && registerDialog){
  registerBtn.addEventListener("click", () => {
    clearRegisterDialog();
    registerDialog.showModal();
    if(registerDeviceIdInput){
      setTimeout(() => registerDeviceIdInput.focus(), 50);
    }
  });
}

if(registerCancelBtn && registerDialog){
  registerCancelBtn.addEventListener("click", () => {
    registerDialog.close("cancel");
  });
}

if(registerForm){
  registerForm.addEventListener("submit", handleRegisterSubmit);
}

if(registerDialog){
  registerDialog.addEventListener("close", () => {
    if(registerDialog.returnValue === "success" && lastRegisteredDeviceId){
      const label = lastRegisteredDeviceName || lastRegisteredDeviceId;
      const idSuffix = lastRegisteredDeviceName ? ` (ID: ${lastRegisteredDeviceId})` : "";
      showRegisterNotice(`デバイス「${label}」${idSuffix}を登録しました。エッジデバイスをオンラインにするとジョブの取得を開始できます。`, "success");
      fetchDevices();
    }
    lastRegisteredDeviceId = null;
    lastRegisteredDeviceName = null;
    clearRegisterDialog();
  });
}

if(gridEl){
  gridEl.addEventListener("click", async (event) => {
    const target = event.target instanceof Element ? event.target.closest("button[data-action]") : null;
    if(!target) return;
    const action = target.dataset.action;
    const deviceId = target.dataset.deviceId;
    if(!deviceId) return;
    event.preventDefault();

    if(action === "rename"){
      const device = devices.find((d) => d.device_id === deviceId);
      const currentName = device?.meta?.display_name && typeof device.meta.display_name === "string"
        ? device.meta.display_name
        : "";
      const promptLabel = currentName || displayName(device) || deviceId;
      const newName = window.prompt(`「${promptLabel}」の新しい名前を入力してください。`, currentName);
      if(newName === null) return;

      const trimmed = newName.trim();
      if(trimmed === (currentName || "").trim()){
        return;
      }
      try{
        const updatedDevice = await updateDeviceDisplayName(deviceId, trimmed);
        if(updatedDevice){
          const idx = devices.findIndex((d) => d.device_id === deviceId);
          if(idx !== -1){
            devices[idx] = updatedDevice;
          }
          const label = displayName(updatedDevice) || updatedDevice.device_id;
          renderDevices();
          showRegisterNotice(`デバイス名を「${label}」に更新しました。`, "success");
          fetchDevices({ silent: true });
        }else{
          throw new Error("サーバーから更新後のデバイス情報が取得できませんでした。");
        }
      }catch(err){
        const message = err instanceof Error ? err.message : String(err);
        showRegisterNotice(`名前の更新に失敗しました: ${message}`, "error");
      }
      return;
    }

    if(action === "delete"){
      const device = devices.find((d) => d.device_id === deviceId);
      const label = displayName(device) || deviceId;
      const confirmed = window.confirm(`デバイス「${label}」を削除しますか？\nジョブキューや履歴も失われます。`);
      if(!confirmed) return;
      try{
        await deleteDevice(deviceId);
        devices = devices.filter((d) => d.device_id !== deviceId);
        renderDevices();
        showRegisterNotice(`デバイス「${label}」を削除しました。`, "success");
        fetchDevices({ silent: true });
      }catch(err){
        const message = err instanceof Error ? err.message : String(err);
        showRegisterNotice(`デバイスの削除に失敗しました: ${message}`, "error");
      }
    }
  });
}

/** ---------- チャット：超軽量LLMもどき（デモ用） ---------- */
const logEl = $("#chatLog");
const formEl = $("#chatForm");
const inputEl = $("#chatInput");
const sendBtn = $("#sendBtn");
const pauseBtn = $("#pauseBtn");
const chatResetBtn = $("#chatResetBtn");
const INITIAL_GREETING = "こんにちは！登録済みデバイスの状況を確認したり、チャットで質問できます。";
let isPaused = false;
let isSending = false;
const chatHistory = [];

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
  chatHistory.push({ role, content: text });
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

function summarizeDevices(){
  if(!devices.length){
    return "登録済みのデバイスはありません。";
  }
  const summaries = devices.map((device) => {
    const caps = Array.isArray(device.capabilities)
      ? device.capabilities.map((cap) => cap?.name).filter(Boolean)
      : [];
    const capText = caps.length ? `（機能: ${caps.join(", ")})` : "";
    return `${displayName(device)}${capText}`;
  });
  return summaries.join(" / ");
}

function applyDeviceCommand(text){
  const t = text.trim();
  if(!t) return null;

  if(/状態|ステータス|確認|教えて/.test(t)){
    return summarizeDevices();
  }

  return null;
}

async function requestAssistantResponse(){
  const payload = {
    messages: chatHistory.map(({ role, content }) => ({ role, content })),
  };

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if(!res.ok){
    const errText = await res.text();
    throw new Error(errText || `HTTP ${res.status}`);
  }

  const data = await res.json();
  return typeof data.reply === "string" ? data.reply : "";
}

formEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  if(isPaused || isSending) return;
  const text = inputEl.value.trim();
  if(!text) return;
  pushMessage("user", text);
  inputEl.value = "";
  isSending = true;
  updateChatControls();

  const localFallback = applyDeviceCommand(text);

  try{
    const reply = await requestAssistantResponse();
    const cleanReply = reply && reply.trim();
    if(cleanReply){
      pushMessage("assistant", cleanReply);
    }else if(localFallback){
      pushMessage("assistant", localFallback);
    }else{
      pushMessage("assistant", "了解しました。");
    }
  }catch(err){
    if(localFallback){
      pushMessage("assistant", localFallback);
    }else{
      pushMessage("assistant", `エラーが発生しました: ${err.message}`);
    }
  }finally{
    isSending = false;
    updateChatControls();
  }
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
    chatHistory.length = 0;
    pushMessage("assistant", INITIAL_GREETING);
    isPaused = false;
    isSending = false;
    updateChatControls();
  });
}

/** ---------- 初期化 ---------- */
(async function init(){
  pushMessage("assistant", INITIAL_GREETING);
  updateChatControls();
  await fetchDevices();
  setInterval(() => {
    fetchDevices({ silent: true });
  }, FETCH_DEVICES_INTERVAL_MS);
})();
