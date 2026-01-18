import React, { useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api.js';
import { displayName } from '../utils.js';

const DEFAULT_MESSAGE =
  'エッジデバイスで使用する識別子を入力し、必要に応じて表示名やメモを設定します。';

export default function RegisterDialog({ open, onClose, onSuccess }) {
  const dialogRef = useRef(null);
  const deviceIdRef = useRef(null);
  const [deviceId, setDeviceId] = useState('');
  const [displayNameInput, setDisplayNameInput] = useState('');
  const [note, setNote] = useState('');
  const [message, setMessage] = useState(DEFAULT_MESSAGE);
  const [messageKind, setMessageKind] = useState('info');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDeviceId('');
    setDisplayNameInput('');
    setNote('');
    setMessage(DEFAULT_MESSAGE);
    setMessageKind('info');
    setSubmitting(false);
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      setTimeout(() => deviceIdRef.current?.focus(), 50);
    }
    if (!open && dialog.open) {
      dialog.close('cancel');
    }
  }, [open]);

  const handleClose = () => {
    onClose?.(dialogRef.current?.returnValue || '');
  };

  const setDialogMessage = (text, kind = 'info') => {
    setMessage(text);
    setMessageKind(kind);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (submitting) return;
    const trimmedId = deviceId.trim();
    if (!trimmedId) {
      setDialogMessage('デバイスIDを入力してください。', 'error');
      deviceIdRef.current?.focus();
      return;
    }

    setSubmitting(true);
    setDialogMessage('サーバーへ登録しています…');

    const payload = {
      device_id: trimmedId,
      capabilities: [],
      meta: {
        registered_via: 'dashboard'
      },
      approved: true
    };

    if (displayNameInput.trim()) {
      payload.meta.display_name = displayNameInput.trim();
    }
    if (note.trim()) {
      payload.meta.note = note.trim();
    }

    try {
      const { response, data, text } = await fetchJson('/api/devices/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const messageText = data?.error || data?.message || text || `HTTP ${response.status}`;
        throw new Error(messageText);
      }

      const registeredId = typeof data?.device_id === 'string' ? data.device_id : trimmedId;
      const registeredDevice = data?.device && typeof data.device === 'object' ? data.device : null;
      const label = registeredDevice
        ? displayName(registeredDevice)
        : displayNameInput.trim() || registeredId;
      const successLabel = label ? `${label} (ID: ${registeredId})` : registeredId;

      setDialogMessage(`デバイス ${successLabel} を登録しました。`, 'success');
      onSuccess?.({ deviceId: registeredId, label, displayName: label });
      dialogRef.current?.close('success');
    } catch (err) {
      setDialogMessage(`登録に失敗しました: ${err.message}`, 'error');
    } finally {
      setSubmitting(false);
    }
  };

  const messageClasses = ['form__hint'];
  if (messageKind === 'error') messageClasses.push('form__hint--error');
  if (messageKind === 'success') messageClasses.push('form__hint--success');

  return (
    <dialog
      id="registerDialog"
      className="dialog"
      aria-labelledby="registerDialogTitle"
      ref={dialogRef}
      onClose={handleClose}
    >
      <form
        className="dialog__panel"
        id="registerDeviceForm"
        noValidate
        autoComplete="off"
        onSubmit={handleSubmit}
      >
        <header className="dialog__header">
          <h3 id="registerDialogTitle">デバイスを登録</h3>
        </header>
        <div className="dialog__body">
          <p className={messageClasses.join(' ')} id="registerDialogMessage">
            {message}
          </p>
          <div className="form__row">
            <label className="form__label" htmlFor="registerDeviceId">デバイスID</label>
            <input
              id="registerDeviceId"
              className="form__control"
              type="text"
              name="device_id"
              placeholder="例: device-abc123"
              autoComplete="off"
              required
              value={deviceId}
              onChange={(event) => setDeviceId(event.target.value)}
              ref={deviceIdRef}
            />
          </div>
          <div className="form__row">
            <label className="form__label" htmlFor="registerDeviceName">表示名（任意）</label>
            <input
              id="registerDeviceName"
              className="form__control"
              type="text"
              name="display_name"
              placeholder="例: キッチンのセンサー"
              autoComplete="off"
              value={displayNameInput}
              onChange={(event) => setDisplayNameInput(event.target.value)}
            />
          </div>
          <div className="form__row">
            <label className="form__label" htmlFor="registerDeviceNote">メモ（任意）</label>
            <input
              id="registerDeviceNote"
              className="form__control"
              type="text"
              name="note"
              placeholder="設置場所など"
              autoComplete="off"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
        </div>
        <footer className="dialog__footer">
          <button
            type="button"
            className="btn btn--ghost"
            id="registerCancelBtn"
            onClick={() => dialogRef.current?.close('cancel')}
          >
            キャンセル
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            id="registerSubmitBtn"
            disabled={submitting}
          >
            {submitting ? '登録中…' : '登録'}
          </button>
        </footer>
      </form>
    </dialog>
  );
}
