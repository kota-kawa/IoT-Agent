/* =========================================================
 * IoT Agent Result (Standalone Device Dashboard)
 * - Fetches and displays registered devices.
 * - Supports renaming, clearing jobs, and deleting devices.
 * - No chat or new device registration features.
 * ======================================================= */

const FETCH_DEVICES_INTERVAL_MS = 5000;

const API_BASE = (window.location.protocol === 'file:')
  ? 'http://localhost:5006'
  : '';

if (window.location.protocol === 'file:') {
  console.warn("Notice: Opened from file system. Attempting to connect to API server at http://localhost:5006.");
}

/** ---------- Utilities ---------- */
const $ = (sel, parent = document) => parent.querySelector(sel);

const formatTimestamp = (ts) => {
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
};

const formatRelativeTime = (ts) => {
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
};

const formatMetaValue = (value) => {
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
};

/** ---------- Device Rendering ---------- */
const gridEl = $("#deviceGrid");
const registerNoticeEl = $("#registerNotice"); // Reusing this ID for general notifications

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

function createStat(label, value){
  const wrapper = document.createElement("div");
  wrapper.className = "device-stat";
  const labelEl = document.createElement("div");
  labelEl.className = "device-stat__label";
  labelEl.textContent = label;
  const valueEl = document.createElement("div");
  valueEl.className = "device-stat__value";
  const textValue = value == null ? "-" : String(value);
  valueEl.textContent = textValue;
  valueEl.title = textValue;
  wrapper.appendChild(labelEl);
  wrapper.appendChild(valueEl);
  return wrapper;
}

function createCollapsibleText(text, { maxLength = 180 } = {}){
  const str = text == null ? "" : String(text);
  const wrapper = document.createElement("div");
  wrapper.className = "collapsible-text";
  const content = document.createElement("div");
  content.className = "collapsible-text__content";
  content.textContent = str;
  content.title = str;
  wrapper.appendChild(content);

  if(str.length <= maxLength){
    wrapper.dataset.state = "expanded";
    return wrapper;
  }

  const fullText = str;
  const truncated = fullText.slice(0, maxLength).trimEnd() + "…";
  let collapsed = true;

  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "collapsible-text__toggle";
  toggleBtn.textContent = "もっと見る";
  toggleBtn.setAttribute("aria-expanded", "false");

  const applyState = () => {
    if(collapsed){
      content.textContent = truncated;
      wrapper.dataset.state = "collapsed";
      toggleBtn.textContent = "もっと見る";
      toggleBtn.setAttribute("aria-expanded", "false");
      toggleBtn.setAttribute("aria-label", "全文を表示");
    }else{
      content.textContent = fullText;
      wrapper.dataset.state = "expanded";
      toggleBtn.textContent = "閉じる";
      toggleBtn.setAttribute("aria-expanded", "true");
      toggleBtn.setAttribute("aria-label", "折りたたむ");
    }
  };

  toggleBtn.addEventListener("click", () => {
    collapsed = !collapsed;
    applyState();
  });

  wrapper.appendChild(toggleBtn);
  applyState();
  return wrapper;
}

function renderCapabilities(capabilities){
  if(!Array.isArray(capabilities) || capabilities.length === 0){
    return null;
  }
  const names = [];
  for(const cap of capabilities){
    if(cap && typeof cap.name === "string" && cap.name.trim()){
      names.push(cap.name.trim());
    }
  }
  if(!names.length){
    return null;
  }
  const section = document.createElement("div");
  section.className = "device-section";
  const label = document.createElement("div");
  label.className = "device-section__label";
  label.textContent = "提供機能";
  section.appendChild(label);

  const list = document.createElement("div");
  list.className = "chip-list";
  const maxChips = 6;
  names.slice(0, maxChips).forEach((name) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name;
    list.appendChild(chip);
  });
  if(names.length > maxChips){
    const restChip = document.createElement("span");
    restChip.className = "chip chip--muted";
    restChip.textContent = `+${names.length - maxChips}`;
    restChip.title = names.slice(maxChips).join(", ");
    list.appendChild(restChip);
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
  label.className = "device-section__label";
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
  if(result.job_id){
    const jobLine = document.createElement("div");
    jobLine.className = "device-result__line";
    const jobLabel = document.createElement("span");
    jobLabel.className = "device-result__label";
    jobLabel.textContent = "ジョブID";
    jobLine.appendChild(jobLabel);
    const jobValue = document.createElement("span");
    jobValue.className = "device-result__value";
    jobValue.textContent = result.job_id;
    jobValue.title = result.job_id;
    jobLine.appendChild(jobValue);
    detail.appendChild(jobLine);
  }
  if(Object.prototype.hasOwnProperty.call(result, "return_value")){
    const valueLine = document.createElement("div");
    valueLine.className = "device-result__line";
    const valueLabel = document.createElement("span");
    valueLabel.className = "device-result__label";
    valueLabel.textContent = "戻り値";
    valueLine.appendChild(valueLabel);
    const valueEl = document.createElement("span");
    valueEl.className = "device-result__value";
    const valueStr = formatMetaValue(result.return_value);
    valueEl.appendChild(createCollapsibleText(valueStr));
    valueLine.appendChild(valueEl);
    detail.appendChild(valueLine);
  }
  if(!detail.children.length){
    const emptyLine = document.createElement("div");
    emptyLine.className = "device-result__line";
    emptyLine.textContent = "結果の詳細はありません";
    detail.appendChild(emptyLine);
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

  const hasDevices = devices.length > 0;
  gridEl.classList.toggle("grid--empty", !hasDevices);

  if(!hasDevices){
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p>登録されたデバイスがありません。</p>
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

    const clearJobsBtn = document.createElement("button");
    clearJobsBtn.type = "button";
    clearJobsBtn.className = "iconbtn";
    clearJobsBtn.dataset.action = "clear-jobs";
    clearJobsBtn.dataset.deviceId = device.device_id;
    clearJobsBtn.title = "待機ジョブをクリア";
    clearJobsBtn.setAttribute("aria-label", `${ariaLabel} の待機ジョブをクリア`);
    clearJobsBtn.textContent = "🧹";
    tools.appendChild(clearJobsBtn);

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

    const stats = document.createElement("div");
    stats.className = "device-stats";
    stats.appendChild(createStat("最終アクセス", formatRelativeTime(device.last_seen)));
    stats.appendChild(createStat("登録日時", formatTimestamp(device.registered_at)));
    const queueRaw = Number(device.queue_depth);
    const queueCount = Number.isFinite(queueRaw) ? queueRaw : 0;
    stats.appendChild(createStat("待機ジョブ", `${queueCount}件`));
    body.appendChild(stats);

    const capSection = renderCapabilities(device.capabilities);
    if(capSection){
      body.appendChild(capSection);
    }
    const resultSection = renderLastResult(device.last_result);
    if(resultSection){
      body.appendChild(resultSection);
    }

    card.appendChild(body);
    gridEl.appendChild(card);
  }
}

function showNotice(message, kind = "info"){
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
  // Auto hide after 5 seconds
  setTimeout(() => {
    registerNoticeEl.hidden = true;
  }, 5000);
}

async function fetchDevices({ silent = false } = {}){
  if(isFetchingDevices) return;
  isFetchingDevices = true;
  try{
    const res = await fetch(`${API_BASE}/api/devices`, { cache: "no-store" });
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
  }catch(err){
    console.error("Failed to fetch devices", err);
    if(!silent){
      showNotice(`デバイス一覧の取得に失敗しました: ${err.message}`, "error");
    }
  }finally{
    isFetchingDevices = false;
  }
}

async function updateDeviceDisplayName(deviceId, displayName){
  const payload = { display_name: displayName || null };
  const res = await fetch(`${API_BASE}/api/devices/${encodeURIComponent(deviceId)}/name`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if(!res.ok){
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
  }
  const data = await res.json();
  return data?.device || null;
}

async function clearDeviceJobs(deviceId){
  const res = await fetch(`${API_BASE}/api/devices/${encodeURIComponent(deviceId)}/jobs`, {
    method: "DELETE",
  });
  if(!res.ok){
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
  }
}

async function deleteDevice(deviceId){
  const res = await fetch(`${API_BASE}/api/devices/${encodeURIComponent(deviceId)}`, {
    method: "DELETE",
  });
  if(!res.ok){
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
  }
}

// Event Listeners for device actions
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
          showNotice(`デバイス名を「${label}」に更新しました。`, "success");
          fetchDevices({ silent: true });
        }
      }catch(err){
        showNotice(`名前の更新に失敗しました: ${err.message}`, "error");
      }
      return;
    }

    if(action === "clear-jobs"){
      const device = devices.find((d) => d.device_id === deviceId);
      const label = displayName(device) || deviceId;
      const confirmed = window.confirm(`デバイス「${label}」の待機ジョブをすべて削除しますか？\n未実行のコマンドはキャンセルされます。`);
      if(!confirmed) return;
      try{
        await clearDeviceJobs(deviceId);
        showNotice(`デバイス「${label}」の待機ジョブをクリアしました。`, "success");
        fetchDevices({ silent: true });
      }catch(err){
        showNotice(`ジョブのクリアに失敗しました: ${err.message}`, "error");
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
        showNotice(`デバイス「${label}」を削除しました。`, "success");
        fetchDevices({ silent: true });
      }catch(err){
        showNotice(`デバイスの削除に失敗しました: ${err.message}`, "error");
      }
    }
  });
}

/** ---------- Init ---------- */
(async function init(){
  await fetchDevices();
  setInterval(() => {
    fetchDevices({ silent: true });
  }, FETCH_DEVICES_INTERVAL_MS);
})();
