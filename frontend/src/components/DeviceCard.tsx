import { useState } from 'react';
import type { Device, DeviceCapability, DeviceResult } from '../types';
import CollapsibleText from './CollapsibleText';
import { displayName, formatMetaValue, formatRelativeTime, formatTimestamp } from '../utils';

function DeviceIcon(): JSX.Element {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="3" stroke="currentColor" strokeWidth="2" />
      <path d="M7 9h10M7 13h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function InfoIcon(): JSX.Element {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4" />
      <path d="M12 8h.01" />
    </svg>
  );
}

type StatProps = {
  label: string;
  value: string | number | null | undefined;
};

function Stat({ label, value }: StatProps): JSX.Element {
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

type CapabilitiesProps = {
  capabilities?: DeviceCapability[];
};

function Capabilities({ capabilities }: CapabilitiesProps): JSX.Element | null {
  if (!Array.isArray(capabilities) || capabilities.length === 0) {
    return null;
  }
  const names = capabilities
    .filter((cap) => cap && typeof cap.name === 'string' && cap.name.trim())
    .map((cap) => (cap.name || '').trim());
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

type LastResultProps = {
  result?: DeviceResult | null;
};

function LastResult({ result }: LastResultProps): JSX.Element | null {
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

type DeviceCardProps = {
  device: Device;
  onRename: (device: Device) => void;
  onClearJobs: (device: Device) => void;
  onDelete: (device: Device) => void;
};

export default function DeviceCard({ device, onRename, onClearJobs, onDelete }: DeviceCardProps): JSX.Element {
  const [showInfo, setShowInfo] = useState(false);
  const label = displayName(device) || device.device_id;
  const queueRaw = Number(device.queue_depth);
  const queueCount = Number.isFinite(queueRaw) ? queueRaw : 0;
  const infoPanelId = `info-${device.device_id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

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
            className={`iconbtn iconbtn--info${showInfo ? ' is-active' : ''}`}
            title="デバイス情報"
            aria-expanded={showInfo}
            aria-controls={infoPanelId}
            onClick={() => setShowInfo((prev) => !prev)}
          >
            <InfoIcon />
          </button>
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

        {showInfo && (
          <section
            className="virtual-info"
            id={infoPanelId}
            aria-label="デバイスの詳細情報"
            style={{ borderStyle: 'dashed', borderColor: 'rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.03)' }}
          >
            <div className="virtual-info__header">
              <div className="virtual-info__title" style={{ color: 'var(--text-dim)' }}>デバイス仕様</div>
              <div className="virtual-info__hint">このデバイスに命令できる内容の詳細は以下の通りです。</div>
            </div>
            <div className="virtual-info__block">
              <div className="virtual-info__label">利用可能な能力</div>
              <ul className="virtual-info__list" style={{ color: 'var(--text-dim)' }}>
                {device.capabilities && device.capabilities.length > 0 ? (
                  device.capabilities.map((cap, i) => (
                    <li key={i}>
                      <strong>{typeof cap.name === 'string' ? cap.name : 'unknown'}</strong>
                      {typeof cap.description === 'string' && cap.description ? (
                        <span>: {cap.description}</span>
                      ) : null}
                    </li>
                  ))
                ) : (
                  <li>登録された機能はありません</li>
                )}
              </ul>
            </div>
          </section>
        )}

        <Capabilities capabilities={device.capabilities} />
        <LastResult result={device.last_result || undefined} />
      </div>
    </article>
  );
}
