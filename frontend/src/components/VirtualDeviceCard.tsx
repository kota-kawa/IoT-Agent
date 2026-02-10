import { useEffect, useRef, useState } from 'react';
import type { Device } from '../types';
import { displayName, formatRelativeTime } from '../utils';

type VirtualDeviceState = {
  power?: string;
  color?: unknown;
  display_text?: string;
  motor_status?: string;
};

type VirtualReturnValue = {
  state?: VirtualDeviceState;
  message?: string;
};

type VirtualDeviceCardProps = {
  device: Device;
  onRename: (device: Device) => void;
  onClearJobs: (device: Device) => void;
  onDelete: (device: Device) => void;
};

const CAPABILITY_LABELS: Record<string, string> = {
  turn_on: '電源をオンにする',
  turn_off: '電源をオフにする',
  set_color: 'LEDの色を変更する',
  set_display: 'ディスプレイにテキストを表示する',
  control_motor: 'モーターを回転/停止する',
  get_status: '状態を取得する'
};

const FALLBACK_CAPABILITIES = [
  '電源のオン/オフ',
  'LEDの色を変更',
  'ディスプレイ表示の更新',
  'モーターの回転/停止',
  '状態の取得'
];

const PROMPT_EXAMPLES = [
  '仮想デモデバイスのLEDを青にして',
  'ディスプレイに「ようこそ」と表示して',
  'モーターを回して',
  'モーターを止めて',
  'いまの状態を教えて'
];

const DEFAULT_STATE: Required<VirtualDeviceState> = {
  power: 'off',
  color: '#cccccc',
  display_text: '',
  motor_status: 'stopped'
};

const normalizeColor = (value: unknown): string => {
  if (value === null || value === undefined) return '';
  return String(value).trim();
};

const normalizeText = (value: unknown): string => {
  if (typeof value !== 'string') return '';
  return value.trim();
};

const sanitizeId = (value: string): string => value.replace(/[^a-zA-Z0-9_-]/g, '-');

const extractCapabilityLabel = (raw: unknown): string => {
  if (!raw || typeof raw !== 'object') return '';
  const record = raw as Record<string, unknown>;
  const description = normalizeText(record.description);
  if (description) return description;
  const name = normalizeText(record.name);
  if (name && CAPABILITY_LABELS[name]) return CAPABILITY_LABELS[name];
  return name;
};

/**
 * A specialized card for the Virtual Device.
 * It renders simulated hardware components (LED, Display, Motor)
 * based on the device's state from the last job result.
 */
export default function VirtualDeviceCard({ device, onRename, onClearJobs, onDelete }: VirtualDeviceCardProps): JSX.Element {
  const label = displayName(device) || device.device_id;
  const [showInfo, setShowInfo] = useState(false);

  const rawReturnValue = device.last_result?.return_value;
  const returnValue: VirtualReturnValue = rawReturnValue && typeof rawReturnValue === 'object'
    ? (rawReturnValue as VirtualReturnValue)
    : {};
  const state: VirtualDeviceState = {
    ...DEFAULT_STATE,
    ...(returnValue.state && typeof returnValue.state === 'object' ? returnValue.state : {})
  };

  const colorCandidate = normalizeColor(state.color);
  const ledColor = colorCandidate && colorCandidate !== 'black' && colorCandidate !== '#000'
    ? colorCandidate
    : '#444';
  const isLedLit = ledColor !== '#444';
  const displayText = typeof state.display_text === 'string' && state.display_text
    ? state.display_text
    : 'Ready';
  const motorStatus = typeof state.motor_status === 'string' ? state.motor_status : DEFAULT_STATE.motor_status;
  const isMotorRunning = motorStatus === 'running';

  const cardRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    card.style.setProperty('--led-color', ledColor);
    card.style.setProperty('--led-shadow', isLedLit ? `0 0 10px ${ledColor}` : 'none');
  }, [ledColor, isLedLit]);

  const motorClass = isMotorRunning ? 'v-motor v-motor--running' : 'v-motor';
  const powerStatus = state.power === 'on' ? 'ON' : 'OFF';
  const powerStatusClass = state.power === 'on' ? 'virtual-status--on' : 'virtual-status--off';
  const motorStatusClass = isMotorRunning ? 'virtual-status--on' : 'virtual-status--off';
  const ledStatusClass = isLedLit ? 'virtual-status--on' : 'virtual-status--off';
  const ledLabel = isLedLit ? ledColor : 'OFF';
  const infoPanelId = `virtual-info-${sanitizeId(device.device_id)}`;
  const capabilityList = (() => {
    const list = Array.isArray(device.capabilities)
      ? device.capabilities.map(extractCapabilityLabel).filter(Boolean)
      : [];
    if (!list.length) return FALLBACK_CAPABILITIES;
    const unique = new Set<string>();
    const deduped: string[] = [];
    list.forEach((item) => {
      if (!unique.has(item)) {
        unique.add(item);
        deduped.push(item);
      }
    });
    return deduped;
  })();

  return (
    <article className="card virtual-device-card" ref={cardRef}>
      <div className="card__head">
        <div className="card__title">
          <div className="badge virtual-badge">Virtual</div>
          <div>
            <div>{label}</div>
            <div className="card__meta">{device.device_id}</div>
          </div>
        </div>
        <div className="card__tools">
          <button
            type="button"
            className={`iconbtn iconbtn--info${showInfo ? ' is-active' : ''}`}
            title="使い方を見る"
            aria-expanded={showInfo}
            aria-controls={infoPanelId}
            onClick={() => setShowInfo((prev) => !prev)}
          >
            <span className="iconbtn__text" aria-hidden="true">ⓘ</span>
          </button>
          <button
            type="button"
            className="iconbtn"
            title="名前を変更"
            onClick={() => onRename(device)}
          >
            ✏️
          </button>
          <button
            type="button"
            className="iconbtn"
            title="待機ジョブをクリア"
            onClick={() => onClearJobs(device)}
          >
            🧹
          </button>
          <button
            type="button"
            className="iconbtn iconbtn--danger"
            title="デバイスを削除"
            onClick={() => onDelete(device)}
          >
            🗑️
          </button>
        </div>
      </div>

      <div className="card__body">
        <div className="device-stats">
          <div className="device-stat">
            <div className="device-stat__label">最終更新</div>
            <div className="device-stat__value">{formatRelativeTime(device.last_seen)}</div>
          </div>
          <div className="device-stat">
            <div className="device-stat__label">電源</div>
            <div className={`device-stat__value ${powerStatusClass}`}>{powerStatus}</div>
          </div>
          <div className="device-stat">
            <div className="device-stat__label">LED</div>
            <div className={`device-stat__value ${ledStatusClass}`}>
              <span className="virtual-color-chip" aria-hidden="true"></span>
              {ledLabel}
            </div>
          </div>
          <div className="device-stat">
            <div className="device-stat__label">モーター</div>
            <div className={`device-stat__value ${motorStatusClass}`}>{isMotorRunning ? 'RUN' : 'STOP'}</div>
          </div>
          <div className="device-stat">
            <div className="device-stat__label">表示</div>
            <div className="device-stat__value" title={displayText}>{displayText}</div>
          </div>
        </div>

        <div className="virtual-board">
          <div className="v-component">
            <div className={`v-led${isLedLit ? ' v-led--lit' : ''}`}></div>
            <span className="v-label">STATUS LED</span>
          </div>

          <div className="v-component">
            <div className={motorClass}>⚙️</div>
            <span className="v-label">MOTOR ({isMotorRunning ? 'RUN' : 'STOP'})</span>
          </div>

          <div className="v-component v-component--display">
            <div className="v-display">{displayText}</div>
            <span className="v-label">OLED DISPLAY</span>
          </div>
        </div>

        <section
          className="virtual-info"
          id={infoPanelId}
          aria-label="仮想デモデバイスの使い方"
          hidden={!showInfo}
        >
            <div className="virtual-info__header">
              <div className="virtual-info__title">仮想デモデバイスでできること</div>
              <div className="virtual-info__hint">チャットに入力すると、上のボードに結果が反映されます。</div>
            </div>
            <div className="virtual-info__grid">
              <div className="virtual-info__block">
                <div className="virtual-info__label">できること</div>
                <ul className="virtual-info__list">
                  {capabilityList.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="virtual-info__block">
                <div className="virtual-info__label">プロンプト例</div>
                <ul className="virtual-info__list">
                  {PROMPT_EXAMPLES.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="virtual-info__note">※実機ではなく画面上のシミュレーションです。</div>
          </section>

        {typeof returnValue.message === 'string' && returnValue.message && (
          <div className="virtual-result">
            Last: {returnValue.message}
          </div>
        )}
      </div>
    </article>
  );
}
