import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api';
import { displayName } from '../utils';
import DeviceGrid from '../components/DeviceGrid';
import Notice from '../components/Notice';
import type {
  Device,
  DeviceListResponse,
  DeviceUpdateResponse,
  GenericResponse,
  NoticeKind,
  NoticeState
} from '../types';

const FETCH_DEVICES_INTERVAL_MS = 5000;

export default function AgentResult(): JSX.Element | null {
  const [devices, setDevices] = useState<Device[]>([]);
  const [notice, setNotice] = useState<NoticeState>({ message: '', kind: 'info', visible: false });
  const isFetchingRef = useRef(false);
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (noticeTimerRef.current) {
        clearTimeout(noticeTimerRef.current);
      }
    };
  }, []);

  const showNotice = (message: string, kind: NoticeKind = 'info') => {
    setNotice({ message, kind, visible: true });
    if (noticeTimerRef.current) {
      clearTimeout(noticeTimerRef.current);
    }
    noticeTimerRef.current = setTimeout(() => {
      setNotice((prev) => ({ ...prev, visible: false }));
    }, 5000);
  };

  const fetchDevices = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    try {
      const { response, data, text } = await fetchJson<DeviceListResponse>('/api/devices');
      if (!response.ok) {
        throw new Error(text || `HTTP ${response.status}`);
      }
      const nextDevices = Array.isArray(data?.devices) ? data.devices : [];
      setDevices(nextDevices);
    } catch (err) {
      if (!silent) {
        if (err instanceof Error) {
          showNotice(`デバイス一覧の取得に失敗しました: ${err.message}`, 'error');
        } else {
          showNotice('デバイス一覧の取得に失敗しました。', 'error');
        }
      }
    } finally {
      isFetchingRef.current = false;
    }
  }, []);

  useEffect(() => {
    fetchDevices();
    const timer = setInterval(() => {
      fetchDevices({ silent: true });
    }, FETCH_DEVICES_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [fetchDevices]);

  const updateDeviceDisplayName = async (deviceId: string, displayNameInput: string) => {
    const payload = { display_name: displayNameInput || null };
    const { response, data, text } = await fetchJson<DeviceUpdateResponse>(
      `/api/devices/${encodeURIComponent(deviceId)}/name`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }
    );

    if (!response.ok) {
      throw new Error(data?.error || data?.message || text || `HTTP ${response.status}`);
    }

    return data?.device || null;
  };

  const clearDeviceJobs = async (deviceId: string) => {
    const { response, data, text } = await fetchJson<GenericResponse>(
      `/api/devices/${encodeURIComponent(deviceId)}/jobs`,
      {
        method: 'DELETE'
      }
    );
    if (!response.ok) {
      throw new Error(data?.error || data?.message || text || `HTTP ${response.status}`);
    }
    return data;
  };

  const deleteDevice = async (deviceId: string) => {
    const { response, data, text } = await fetchJson<GenericResponse>(
      `/api/devices/${encodeURIComponent(deviceId)}`,
      {
        method: 'DELETE'
      }
    );
    if (!response.ok) {
      throw new Error(data?.error || data?.message || text || `HTTP ${response.status}`);
    }
    return data;
  };

  const handleRename = async (device: Device) => {
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
      if (err instanceof Error) {
        showNotice(`名前の更新に失敗しました: ${err.message}`, 'error');
      } else {
        showNotice('名前の更新に失敗しました。', 'error');
      }
    }
  };

  const handleClearJobs = async (device: Device) => {
    const label = displayName(device) || device.device_id;
    const confirmed = window.confirm(`デバイス「${label}」の待機ジョブをすべて削除しますか？\n未実行のコマンドはキャンセルされます。`);
    if (!confirmed) return;

    try {
      await clearDeviceJobs(device.device_id);
      showNotice(`デバイス「${label}」の待機ジョブをクリアしました。`, 'success');
      fetchDevices({ silent: true });
    } catch (err) {
      if (err instanceof Error) {
        showNotice(`ジョブのクリアに失敗しました: ${err.message}`, 'error');
      } else {
        showNotice('ジョブのクリアに失敗しました。', 'error');
      }
    }
  };

  const handleDelete = async (device: Device) => {
    const label = displayName(device) || device.device_id;
    const confirmed = window.confirm(`デバイス「${label}」を削除しますか？\nジョブキューや履歴も失われます。`);
    if (!confirmed) return;

    try {
      await deleteDevice(device.device_id);
      setDevices((prev) => prev.filter((item) => item.device_id !== device.device_id));
      showNotice(`デバイス「${label}」を削除しました。`, 'success');
      fetchDevices({ silent: true });
    } catch (err) {
      if (err instanceof Error) {
        showNotice(`デバイスの削除に失敗しました: ${err.message}`, 'error');
      } else {
        showNotice('デバイスの削除に失敗しました。', 'error');
      }
    }
  };

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
