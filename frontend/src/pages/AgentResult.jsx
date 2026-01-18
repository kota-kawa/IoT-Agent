import React, { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api.js';
import { displayName } from '../utils.js';
import { useRequireAuth } from '../hooks.js';
import DeviceGrid from '../components/DeviceGrid.jsx';
import Notice from '../components/Notice.jsx';

const FETCH_DEVICES_INTERVAL_MS = 5000;

export default function AgentResult() {
  const ready = useRequireAuth();
  const [devices, setDevices] = useState([]);
  const [notice, setNotice] = useState({ message: '', kind: 'info', visible: false });
  const isFetchingRef = useRef(false);
  const noticeTimerRef = useRef(null);

  useEffect(() => {
    return () => {
      if (noticeTimerRef.current) {
        clearTimeout(noticeTimerRef.current);
      }
    };
  }, []);

  const showNotice = (message, kind = 'info') => {
    setNotice({ message, kind, visible: true });
    if (noticeTimerRef.current) {
      clearTimeout(noticeTimerRef.current);
    }
    noticeTimerRef.current = setTimeout(() => {
      setNotice((prev) => ({ ...prev, visible: false }));
    }, 5000);
  };

  const fetchDevices = useCallback(async ({ silent = false } = {}) => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    try {
      const { response, data, text } = await fetchJson('/api/devices');
      if (!response.ok) {
        throw new Error(text || `HTTP ${response.status}`);
      }
      const nextDevices = Array.isArray(data?.devices) ? data.devices : [];
      setDevices(nextDevices);
    } catch (err) {
      if (!silent) {
        showNotice(`デバイス一覧の取得に失敗しました: ${err.message}`, 'error');
      }
    } finally {
      isFetchingRef.current = false;
    }
  }, []);

  useEffect(() => {
    if (!ready) return;
    fetchDevices();
    const timer = setInterval(() => {
      fetchDevices({ silent: true });
    }, FETCH_DEVICES_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [fetchDevices, ready]);

  const updateDeviceDisplayName = async (deviceId, displayNameInput) => {
    const payload = { display_name: displayNameInput || null };
    const { response, data, text } = await fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/name`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(data?.error || data?.message || text || `HTTP ${response.status}`);
    }

    return data?.device || null;
  };

  const clearDeviceJobs = async (deviceId) => {
    const { response, data, text } = await fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/jobs`, {
      method: 'DELETE'
    });
    if (!response.ok) {
      throw new Error(data?.error || data?.message || text || `HTTP ${response.status}`);
    }
    return data;
  };

  const deleteDevice = async (deviceId) => {
    const { response, data, text } = await fetchJson(`/api/devices/${encodeURIComponent(deviceId)}`, {
      method: 'DELETE'
    });
    if (!response.ok) {
      throw new Error(data?.error || data?.message || text || `HTTP ${response.status}`);
    }
    return data;
  };

  const handleRename = async (device) => {
    const currentName = typeof device?.meta?.display_name === 'string'
      ? device.meta.display_name
      : '';
    const promptLabel = currentName || displayName(device) || device.device_id;
    const newName = window.prompt(`「${promptLabel}」の新しい名前を入力してください。`, currentName);
    if (newName === null) return;
    const trimmed = newName.trim();
    if (trimmed === (currentName || '').trim()) return;

    try {
      const updatedDevice = await updateDeviceDisplayName(device.device_id, trimmed);
      if (updatedDevice) {
        setDevices((prev) => prev.map((item) => (
          item.device_id === device.device_id ? updatedDevice : item
        )));
        const label = displayName(updatedDevice) || updatedDevice.device_id;
        showNotice(`デバイス名を「${label}」に更新しました。`, 'success');
        fetchDevices({ silent: true });
      }
    } catch (err) {
      showNotice(`名前の更新に失敗しました: ${err.message}`, 'error');
    }
  };

  const handleClearJobs = async (device) => {
    const label = displayName(device) || device.device_id;
    const confirmed = window.confirm(`デバイス「${label}」の待機ジョブをすべて削除しますか？\n未実行のコマンドはキャンセルされます。`);
    if (!confirmed) return;

    try {
      await clearDeviceJobs(device.device_id);
      showNotice(`デバイス「${label}」の待機ジョブをクリアしました。`, 'success');
      fetchDevices({ silent: true });
    } catch (err) {
      showNotice(`ジョブのクリアに失敗しました: ${err.message}`, 'error');
    }
  };

  const handleDelete = async (device) => {
    const label = displayName(device) || device.device_id;
    const confirmed = window.confirm(`デバイス「${label}」を削除しますか？\nジョブキューや履歴も失われます。`);
    if (!confirmed) return;

    try {
      await deleteDevice(device.device_id);
      setDevices((prev) => prev.filter((item) => item.device_id !== device.device_id));
      showNotice(`デバイス「${label}」を削除しました。`, 'success');
      fetchDevices({ silent: true });
    } catch (err) {
      showNotice(`デバイスの削除に失敗しました: ${err.message}`, 'error');
    }
  };

  if (!ready) {
    return null;
  }

  return (
    <div className="app-standalone">
      <main className="main">
        <header className="main__header">
          <div>
            <h2 className="main__title">IoT デバイス状態</h2>
            <p className="main__subtitle">登録されたデバイスの現在の状態と実行結果を表示しています。</p>
          </div>
          <Notice message={notice.message} kind={notice.kind} hidden={!notice.visible} />
        </header>
        <div className="grid-wrapper">
          <DeviceGrid
            devices={devices}
            onRename={handleRename}
            onClearJobs={handleClearJobs}
            onDelete={handleDelete}
          />
        </div>
      </main>
    </div>
  );
}
