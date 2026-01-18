export const DEFAULT_MODEL = { provider: 'groq', model: 'openai/gpt-oss-20b', base_url: '' };

export const nowTime = () => {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
};

export function displayName(device) {
  if (!device) return '';
  const meta = device.meta || {};
  if (typeof meta.display_name === 'string' && meta.display_name.trim()) {
    return meta.display_name.trim();
  }
  if (typeof meta.note === 'string' && meta.note.trim()) {
    return meta.note.trim();
  }
  if (typeof meta.label === 'string' && meta.label.trim()) {
    return meta.label.trim();
  }
  if (typeof meta.location === 'string' && meta.location.trim()) {
    return `${device.device_id} @ ${meta.location.trim()}`;
  }
  return device.device_id;
}

export function formatTimestamp(ts) {
  if (!ts && ts !== 0) return '-';
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) {
    return String(ts);
  }
  return date.toLocaleString('ja-JP', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  });
}

export function formatRelativeTime(ts) {
  if (!ts && ts !== 0) return '未記録';
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) {
    return String(ts);
  }
  const diff = Date.now() - date.getTime();
  if (diff < 0) {
    return formatTimestamp(ts);
  }
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return 'たった今';
  if (sec < 60) return `${sec}秒前`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}分前`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours}時間前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}日前`;
  return formatTimestamp(ts);
}

export function formatMetaValue(value) {
  if (value === null) return 'null';
  if (value === undefined) return '-';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch (_err) {
    return String(value);
  }
}

export function summarizeDevices(devices) {
  if (!devices.length) {
    return '登録済みのデバイスはありません。';
  }
  const summaries = devices.map((device) => {
    const caps = Array.isArray(device.capabilities)
      ? device.capabilities.map((cap) => cap?.name).filter(Boolean)
      : [];
    const capText = caps.length ? `（機能: ${caps.join(', ')}）` : '';
    return `${displayName(device)}${capText}`;
  });
  return summaries.join(' / ');
}

export function applyDeviceCommand(text, devices) {
  const t = text.trim();
  if (!t) return null;
  if (/状態|ステータス|確認|教えて/.test(t)) {
    return summarizeDevices(devices);
  }
  return null;
}
