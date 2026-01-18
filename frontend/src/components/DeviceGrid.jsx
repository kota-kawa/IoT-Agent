import React from 'react';
import DeviceCard from './DeviceCard.jsx';

export default function DeviceGrid({ devices, onRename, onClearJobs, onDelete, emptyHint }) {
  const hasDevices = devices.length > 0;

  return (
    <section className={`grid${hasDevices ? '' : ' grid--empty'}`} id="deviceGrid" aria-label="デバイス一覧">
      {!hasDevices && (
        <div className="empty-state">
          <p>登録されたデバイスがありません。</p>
          {emptyHint && <p className="empty-state__hint">{emptyHint}</p>}
        </div>
      )}
      {hasDevices && devices.map((device) => (
        <DeviceCard
          key={device.device_id}
          device={device}
          onRename={onRename}
          onClearJobs={onClearJobs}
          onDelete={onDelete}
        />
      ))}
    </section>
  );
}
