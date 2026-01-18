import React from 'react';
import CollapsibleText from './CollapsibleText.jsx';
import { displayName, formatMetaValue, formatRelativeTime, formatTimestamp } from '../utils.js';

function DeviceIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="3" stroke="currentColor" strokeWidth="2" />
      <path d="M7 9h10M7 13h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Stat({ label, value }) {
  const textValue = value == null ? '-' : String(value);
  return (
    <div className="device-stat">
      <div className="device-stat__label">{label}</div>
      <div className="device-stat__value" title={textValue}>
        {textValue}
      </div>
    </div>
  );
}

function Capabilities({ capabilities }) {
  if (!Array.isArray(capabilities) || capabilities.length === 0) {
    return null;
  }
  const names = capabilities
    .filter((cap) => cap && typeof cap.name === 'string' && cap.name.trim())
    .map((cap) => cap.name.trim());
  if (!names.length) return null;

  const maxChips = 6;
  const visible = names.slice(0, maxChips);
  const rest = names.slice(maxChips);

  return (
    <div className="device-section">
      <div className="device-section__label">提供機能</div>
      <div className="chip-list">
        {visible.map((name) => (
          <span className="chip" key={name}>{name}</span>
        ))}
        {rest.length > 0 && (
          <span className="chip chip--muted" title={rest.join(', ')}>
            +{rest.length}
          </span>
        )}
      </div>
    </div>
  );
}

function LastResult({ result }) {
  if (!result || typeof result !== 'object') {
    return null;
  }

  return (
    <div className="device-section">
      <div className="device-section__label">最後のジョブ</div>
      <div className="device-result">
        <span
          className={`device-result__status device-result__status--${result.ok ? 'ok' : 'error'}`}
        >
          {result.ok ? '成功' : '失敗'}
        </span>
        <div className="device-result__detail">
          {result.job_id && (
            <div className="device-result__line">
              <span className="device-result__label">ジョブID</span>
              <span className="device-result__value" title={result.job_id}>{result.job_id}</span>
            </div>
          )}
          {Object.prototype.hasOwnProperty.call(result, 'return_value') && (
            <div className="device-result__line">
              <span className="device-result__label">戻り値</span>
              <span className="device-result__value">
                <CollapsibleText text={formatMetaValue(result.return_value)} />
              </span>
            </div>
          )}
          {!result.job_id && !Object.prototype.hasOwnProperty.call(result, 'return_value') && (
            <div className="device-result__line">結果の詳細はありません</div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function DeviceCard({ device, onRename, onClearJobs, onDelete }) {
  const label = displayName(device) || device.device_id;
  const queueRaw = Number(device.queue_depth);
  const queueCount = Number.isFinite(queueRaw) ? queueRaw : 0;

  return (
    <article className="card">
      <div className="card__head">
        <div className="card__title">
          <div className="badge"><DeviceIcon /></div>
          <div>
            <div>{label}</div>
            <div className="card__meta">{device.device_id}</div>
          </div>
        </div>
        <div className="card__tools">
          <button
            type="button"
            className="iconbtn"
            title="名前を変更"
            aria-label={`${label} の名前を変更`}
            onClick={() => onRename(device)}
          >
            ✏️
          </button>
          <button
            type="button"
            className="iconbtn"
            title="待機ジョブをクリア"
            aria-label={`${label} の待機ジョブをクリア`}
            onClick={() => onClearJobs(device)}
          >
            🧹
          </button>
          <button
            type="button"
            className="iconbtn iconbtn--danger"
            title="デバイスを削除"
            aria-label={`${label} を削除`}
            onClick={() => onDelete(device)}
          >
            🗑️
          </button>
        </div>
      </div>
      <div className="card__body">
        <div className="device-stats">
          <Stat label="最終アクセス" value={formatRelativeTime(device.last_seen)} />
          <Stat label="登録日時" value={formatTimestamp(device.registered_at)} />
          <Stat label="待機ジョブ" value={`${queueCount}件`} />
        </div>
        <Capabilities capabilities={device.capabilities} />
        <LastResult result={device.last_result} />
      </div>
    </article>
  );
}
