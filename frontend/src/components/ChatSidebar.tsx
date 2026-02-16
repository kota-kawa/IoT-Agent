import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { fetchJson } from '../api';
import { DEFAULT_MODEL, applyDeviceCommand, nowTime } from '../utils';
import type {
  ChatImage,
  ChatMessage,
  ChatRole,
  ChatResponse,
  Device,
  ModelOption,
  ModelSettingsPayload,
  ModelsResponse
} from '../types';

const INITIAL_GREETING = 'こんにちは！登録済みデバイスの状況を確認したり、チャットで質問できます。';

type ChatSidebarProps = {
  devices: Device[];
};

const isModelOption = (value: unknown): value is ModelOption => {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return typeof record.provider === 'string' && typeof record.model === 'string';
};

export default function ChatSidebar({ devices }: ChatSidebarProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    { role: 'assistant', content: INITIAL_GREETING, time: nowTime(), images: [] }
  ]);
  const [input, setInput] = useState('');
  const [isPaused, setIsPaused] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState(`${DEFAULT_MODEL.provider}:${DEFAULT_MODEL.model}`);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const logEl = logRef.current;
    if (!logEl) return;
    logEl.scrollTop = logEl.scrollHeight;
  }, [messages, isSending]);

  useEffect(() => {
    let active = true;
    const loadModelOptions = async () => {
      try {
        const { response, data } = await fetchJson<ModelsResponse>('/api/models');
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        if (Array.isArray(data?.models)) {
          const sanitized = data.models.filter(isModelOption);
          if (active) {
            setAvailableModels(sanitized);
          }
        }
        const current = data?.current;
        if (isModelOption(current)) {
          const nextModel: ModelOption = {
            provider: current.provider,
            model: current.model,
            base_url: typeof current.base_url === 'string' ? current.base_url : ''
          };
          if (active) {
            setSelectedModel(`${nextModel.provider}:${nextModel.model}`);
          }
        }
      } catch {
        if (active) {
          setAvailableModels([]);
          setSelectedModel(`${DEFAULT_MODEL.provider}:${DEFAULT_MODEL.model}`);
        }
      } finally {
        if (active) {
          setModelsLoaded(true);
        }
      }
    };
    loadModelOptions();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!modelsLoaded) return;
    const [providerRaw, modelRaw] = (selectedModel || `${DEFAULT_MODEL.provider}:${DEFAULT_MODEL.model}`).split(':');
    const payload: ModelSettingsPayload = {
      provider: providerRaw || DEFAULT_MODEL.provider,
      model: modelRaw || DEFAULT_MODEL.model,
      base_url: ''
    };

    const update = async () => {
      try {
        const { response } = await fetchJson('/model_settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
      } catch (err) {
        if (err instanceof Error) {
          window.alert(`モデルの更新に失敗しました: ${err.message}`);
        } else {
          window.alert('モデルの更新に失敗しました。');
        }
      }
    };

    update();
  }, [modelsLoaded, selectedModel]);

  const options = useMemo<ModelOption[]>(() => {
    if (availableModels.length) return availableModels;
    return [{ ...DEFAULT_MODEL, label: 'Default (Groq GPT-OSS)' }];
  }, [availableModels]);

  const disableSend = isPaused || isSending;
  const disableInput = isPaused;

  const pushMessage = (role: ChatRole, text: string, images: ChatImage[] = []) => {
    setMessages((prev) => [
      ...prev,
      { role, content: text, time: nowTime(), images }
    ]);
  };

  const requestAssistantResponse = async (history: ChatMessage[]) => {
    const payload = {
      messages: history.map(({ role, content }) => ({ role, content }))
    };

    const { response, data, text } = await fetchJson<ChatResponse>('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(text || `HTTP ${response.status}`);
    }
    return data;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isPaused || isSending) return;
    const text = input.trim();
    if (!text) return;

    const nextHistory: ChatMessage[] = [
      ...messages,
      { role: 'user', content: text, time: nowTime(), images: [] }
    ];
    pushMessage('user', text);
    setInput('');
    setIsSending(true);

    const localFallback = applyDeviceCommand(text, devices);

    try {
      const data = await requestAssistantResponse(nextHistory);
      const reply = typeof data?.reply === 'string' ? data.reply : '';
      const images = Array.isArray(data?.images) ? data.images : [];
      const cleanReply = reply.trim();

      if (cleanReply || images.length > 0) {
        pushMessage('assistant', cleanReply, images);
      } else if (localFallback) {
        pushMessage('assistant', localFallback);
      } else {
        pushMessage('assistant', '了解しました。');
      }
    } catch (err) {
      if (localFallback) {
        pushMessage('assistant', localFallback);
      } else if (err instanceof Error) {
        pushMessage('assistant', `エラーが発生しました: ${err.message}`);
      } else {
        pushMessage('assistant', 'エラーが発生しました。');
      }
    } finally {
      setIsSending(false);
    }
  };

  const handleReset = () => {
    setMessages([{ role: 'assistant', content: INITIAL_GREETING, time: nowTime(), images: [] }]);
    setIsPaused(false);
    setIsSending(false);
  };

  return (
    <aside className="sidebar" aria-label="IoT Agent">
      <header className="sidebar__header">
        <div className="sidebar__title">
          <span className="sidebar__bubble">💬</span>
          <h1>IoT Agent</h1>
          <div className="model-selection">
            <label htmlFor="modelSelect" className="sr-only">モデルを選択</label>
            <select
              id="modelSelect"
              className="model-selection__select"
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
            >
              {options.map((model) => (
                <option key={`${model.provider}:${model.model}`} value={`${model.provider}:${model.model}`}>
                  {model.label || `${model.provider}:${model.model}`}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>

      <section className="chat" id="chat">
        <div
          className="chat__log"
          id="chatLog"
          role="log"
          aria-live="polite"
          aria-relevant="additions"
          aria-busy={isSending}
          ref={logRef}
        >
          {messages.map((msg, index) => (
            <div className={`message message--${msg.role}`} key={`${msg.role}-${index}-${msg.time}`}>
              <div className="message__avatar">{msg.role === 'user' ? '👤' : '🤖'}</div>
              <div>
                <div className="message__bubble">
                  {msg.content}
                  {Array.isArray(msg.images) && msg.images.length > 0 && (
                    <div className="message__images">
                      {msg.images.map((img, imgIndex) => (
                        img?.data_url ? (
                          <img
                            key={`${imgIndex}-${img.data_url}`}
                            src={img.data_url}
                            alt={img.label || 'Captured Image'}
                            className="message__image"
                          />
                        ) : null
                      ))}
                    </div>
                  )}
                </div>
                <div className="message__meta">{msg.role === 'user' ? 'あなた' : 'LLM'} ・ {msg.time}</div>
              </div>
            </div>
          ))}
          {isSending && (
            <div className="message message--assistant message--thinking" key="assistant-thinking">
              <div className="message__avatar" aria-hidden="true">🤖</div>
              <div>
                <div className="message__bubble message__bubble--thinking">
                  <span
                    className="thinking-indicator"
                    role="status"
                    aria-live="polite"
                    aria-label="LLM が応答を生成中です"
                  >
                    <span className="thinking-indicator__dot" />
                    <span className="thinking-indicator__dot" />
                    <span className="thinking-indicator__dot" />
                    <span className="thinking-indicator__label">Thinking...</span>
                  </span>
                </div>
                <div className="message__meta">LLM ・ 応答を生成中</div>
              </div>
            </div>
          )}
        </div>
      </section>

      <form className="chat-controller" id="chatForm" autoComplete="off" onSubmit={handleSubmit}>
        <label htmlFor="chatInput" className="sr-only">メッセージを入力</label>
        <div className="chat-controller__inner">
          <textarea
            id="chatInput"
            className="chat-controller__input"
            rows={2}
            placeholder="ブラウザに指示したい内容を入力してください。"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={disableInput}
          />
          <div className="chat-controller__side">
            <button
              type="submit"
              className="control-btn control-btn--send"
              id="sendBtn"
              aria-label="送信"
              disabled={disableSend}
            >
              <span aria-hidden="true">➜</span>
            </button>
            <div className="control-btn__row">
              <button
                type="button"
                className={`control-btn${isPaused ? ' is-active' : ''}`}
                id="pauseBtn"
                aria-label="一時停止"
                aria-pressed={isPaused}
                onClick={() => {
                  const next = !isPaused;
                  setIsPaused(next);
                }}
              >
                <span aria-hidden="true">⏸</span>
              </button>
              <button
                type="button"
                className="control-btn"
                id="chatResetBtn"
                aria-label="リセット"
                onClick={handleReset}
              >
                <span aria-hidden="true">⟲</span>
              </button>
            </div>
          </div>
        </div>
      </form>
    </aside>
  );
}
