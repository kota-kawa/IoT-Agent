import { useEffect, useRef } from 'react';
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

/**
 * A specialized card for the Virtual Device.
 * It renders simulated hardware components (LED, Display, Motor)
 * based on the device's state from the last job result.
 */
export default function VirtualDeviceCard({ device, onRename, onClearJobs, onDelete }: VirtualDeviceCardProps): JSX.Element {
  const label = displayName(device) || device.device_id;

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
          <div>更新: {formatRelativeTime(device.last_seen)}</div>
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

        {typeof returnValue.message === 'string' && returnValue.message && (
          <div className="virtual-result">
            Last: {returnValue.message}
          </div>
        )}
      </div>
    </article>
  );
}
