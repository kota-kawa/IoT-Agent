import React, { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api.js';
import { displayName } from '../utils.js';
import { useRequireAuth } from '../hooks.js';
import ChatSidebar from '../components/ChatSidebar.jsx';
import DeviceGrid from '../components/DeviceGrid.jsx';
import Notice from '../components/Notice.jsx';
import RegisterDialog from '../components/RegisterDialog.jsx';

const FETCH_DEVICES_INTERVAL_MS = 5000;

export default function Dashboard() {
  const ready = useRequireAuth();
  const [devices, setDevices] = useState([]);
  const [notice, setNotice] = useState({ message: '', kind: 'info', visible: false });
  const [dialogOpen, setDialogOpen] = useState(false);
  const isFetchingRef = useRef(false);

  const showNotice = (message, kind = 'info') => {
    setNotice({ message, kind, visible: true });
  };

  const hideNotice = () => {
    setNotice({ message: '', kind: 'info', visible: false });
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
      if (notice.visible && notice.kind === 'error') {
        hideNotice();
      }
    } catch (err) {
      if (!silent) {
        showNotice(`デバイス一覧の取得に失敗しました: ${err.message}`, 'error');
      }
    } finally {
      isFetchingRef.current = false;
    }
  }, [notice.visible, notice.kind]);

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
      const message = data?.error || data?.message || text || `HTTP ${response.status}`;
      throw new Error(message);
    }

    return data?.device || null;
  };

  const clearDeviceJobs = async (deviceId) => {
    const { response, data, text } = await fetchJson(`/api/devices/${encodeURIComponent(deviceId)}/jobs`, {
      method: 'DELETE'
    });

    if (!response.ok) {
      const message = data?.error || data?.message || text || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return data;
  };

  const deleteDevice = async (deviceId) => {
    const { response, data, text } = await fetchJson(`/api/devices/${encodeURIComponent(deviceId)}`, {
      method: 'DELETE'
    });

    if (!response.ok) {
      const message = data?.error || data?.message || text || `HTTP ${response.status}`;
      throw new Error(message);
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
      } else {
        throw new Error('サーバーから更新後のデバイス情報が取得できませんでした。');
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

  const handleRegisterSuccess = ({ deviceId, label }) => {
    const displayLabel = label || deviceId;
    const idSuffix = label ? ` (ID: ${deviceId})` : '';
    showNotice(
      `デバイス「${displayLabel}」${idSuffix}を登録しました。エッジデバイスをオンラインにするとジョブの取得を開始できます。`,
      'success'
    );
    fetchDevices();
  };

  if (!ready) {
    return null;
  }

  return (
    <div className="app">
      <ChatSidebar devices={devices} />
      <main className="main">
        <header className="main__header">
          <div>
            <h2 className="main__title">IoT ダッシュボード</h2>
            <p className="main__subtitle">登録された Pico W デバイスの状態と提供機能を確認できます。</p>
          </div>
          <div className="main__actions">
            <button className="btn btn--primary" id="registerDeviceBtn" onClick={() => setDialogOpen(true)}>
              デバイス登録
            </button>
          </div>
        </header>

        <Notice message={notice.message} kind={notice.kind} hidden={!notice.visible} />

        <div className="grid-wrapper">
          <DeviceGrid
            devices={devices}
            onRename={handleRename}
            onClearJobs={handleClearJobs}
            onDelete={handleDelete}
            emptyHint="右上の「デバイス登録」から登録してください。"
          />
        </div>
      </main>

      <RegisterDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSuccess={handleRegisterSuccess}
      />
    </div>
  );
}
