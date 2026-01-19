import React from 'react';
import { displayName, formatRelativeTime } from '../utils.js';

/**
 * A specialized card for the Virtual Device.
 * It renders simulated hardware components (LED, Display, Motor)
 * based on the device's state from the last job result.
 */
export default function VirtualDeviceCard({ device, onRename, onClearJobs, onDelete }) {
  const label = displayName(device) || device.device_id;
  
  // Extract state from the last result
  const lastResult = device.last_result || {};
  const returnValue = lastResult.return_value || {};
  const state = returnValue.state || {
    power: 'off',
    color: '#cccccc',
    display_text: '',
    motor_status: 'stopped'
  };

  const isPowerOn = state.power === 'on';
  // LED lights up if color is set and not 'black'/'#000', ignoring power state as requested
  const ledColor = (state.color && state.color !== 'black' && state.color !== '#000') ? state.color : '#444';
  const isLedLit = ledColor !== '#444';
  const displayText = state.display_text || "Ready";
  const isMotorRunning = state.motor_status === 'running';

  // CSS for the spinning motor
  const motorStyle = {
    display: 'inline-block',
    fontSize: '2rem',
    transition: 'transform 0.5s ease',
    animation: isMotorRunning ? 'spin 1s linear infinite' : 'none'
  };

  return (
    <article className="card virtual-device-card" style={{ border: '2px solid #3b82f6' }}>
      <style>{`
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .virtual-board {
          background: #2a2a2a;
          padding: 1rem;
          border-radius: 8px;
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 1rem;
          color: #fff;
          font-family: monospace;
          margin-top: 1rem;
        }
        .v-component {
          background: #1a1a1a;
          padding: 0.5rem;
          border-radius: 4px;
          text-align: center;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          min-height: 80px;
        }
        .v-led {
          width: 24px;
          height: 24px;
          border-radius: 50%;
          background-color: ${ledColor};
          box-shadow: ${isLedLit ? `0 0 10px ${ledColor}` : 'none'};
          margin-bottom: 0.5rem;
          border: 2px solid #555;
          transition: background-color 0.3s, box-shadow 0.3s;
        }
        .v-display {
          background: #000;
          color: #33ff33;
          font-family: 'Courier New', Courier, monospace;
          padding: 4px 8px;
          border: 1px solid #555;
          width: 100%;
          min-height: 24px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 0.8rem;
          overflow: hidden;
          white-space: nowrap;
          text-overflow: ellipsis;
        }
        .v-label {
          font-size: 0.7rem;
          color: #888;
          margin-top: 4px;
        }
      `}</style>

      <div className="card__head">
        <div className="card__title">
          <div className="badge" style={{background: '#3b82f6', width: 'auto', padding: '0 8px', color: '#fff'}}>Virtual</div>
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
          {/* LED Component */}
          <div className="v-component">
            <div className="v-led"></div>
            <span className="v-label">STATUS LED</span>
          </div>

          {/* Motor Component */}
          <div className="v-component">
            <div style={motorStyle}>⚙️</div>
            <span className="v-label">MOTOR ({isMotorRunning ? 'RUN' : 'STOP'})</span>
          </div>

          {/* Display Component */}
          <div className="v-component" style={{gridColumn: '1 / -1'}}>
            <div className="v-display">{displayText}</div>
            <span className="v-label">OLED DISPLAY</span>
          </div>
        </div>
        
        {returnValue.message && (
          <div style={{marginTop: '0.5rem', fontSize: '0.8rem', color: '#666'}}>
             Last: {returnValue.message}
          </div>
        )}
      </div>
    </article>
  );
}
