import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { apiUrl, fetchJson } from '../api';
import { DEFAULT_MODEL, applyDeviceCommand, nowTime } from '../utils';
import type {
  ChatImage,
  ExecutionLogStatus,
  ChatMessage,
  ChatRole,
  ChatResponse,
  Device,
  MessageExecutionLog,
  ModelOption,
  ModelSettingsPayload,
  ModelsResponse
} from '../types';

const INITIAL_GREETING = 'こんにちは！登録済みデバイスの状況を確認したり、チャットで質問できます。';

type ChatSidebarProps = {
  devices: Device[];
};

type ChatStreamEvent = {
  type?: string;
  stage?: string;
  message?: string;
  timestamp?: number;
  payload?: ChatResponse;
  status?: number;
  [key: string]: unknown;
};

type ChatStatusUpdate = {
  stage: string;
  message: string;
  timestamp?: number;
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
  const [currentStatus, setCurrentStatus] = useState('');
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState(`${DEFAULT_MODEL.provider}:${DEFAULT_MODEL.model}`);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const logEl = logRef.current;
    if (!logEl) return;
    logEl.scrollTop = logEl.scrollHeight;
  }, [messages, isSending, currentStatus]);

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

  const pushMessage = (
    role: ChatRole,
    text: string,
    images: ChatImage[] = [],
    executionLog: MessageExecutionLog | null = null
  ) => {
    setMessages((prev) => [
      ...prev,
      { role, content: text, time: nowTime(), images, executionLog }
    ]);
  };

  const appendStep = (steps: string[], step: string) => {
    const text = step.trim();
    if (!text) return steps;
    if (steps[steps.length - 1] === text) return steps;
    return [...steps, text];
  };

  const toMessageExecutionLog = (
    steps: string[],
    status: ExecutionLogStatus
  ): MessageExecutionLog | null => {
    if (!steps.length) return null;
    return {
      status,
      steps
    };
  };

  const messageLogStatusLabel = (status: ExecutionLogStatus) => (
    status === 'success' ? '完了' : 'エラー'
  );

  const requestAssistantResponseLegacy = async (history: ChatMessage[]) => {
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

  const requestAssistantResponseStream = async (
    history: ChatMessage[],
    onStatus: (event: ChatStatusUpdate) => void
  ) => {
    const payload = {
      messages: history.map(({ role, content }) => ({ role, content }))
    };

    const response = await fetch(apiUrl('/api/chat/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      cache: 'no-store',
      body: JSON.stringify(payload)
    });

    if (response.status === 404 || !response.body) {
      return requestAssistantResponseLegacy(history);
    }

    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload: ChatResponse | null = null;
    let streamErrorMessage = '';

    const consumeLine = (rawLine: string) => {
      const line = rawLine.trim();
      if (!line) return;

      let event: ChatStreamEvent | null = null;
      try {
        const parsed = JSON.parse(line) as unknown;
        if (parsed && typeof parsed === 'object') {
          event = parsed as ChatStreamEvent;
        }
      } catch {
        return;
      }
      if (!event) return;

      const type = typeof event.type === 'string' ? event.type : '';
      const stage = typeof event.stage === 'string' ? event.stage : '';
      const message = typeof event.message === 'string' ? event.message.trim() : '';
      const timestamp = typeof event.timestamp === 'number' ? event.timestamp : undefined;

      if (type === 'status' && message) {
        onStatus({ stage, message, timestamp });
        return;
      }

      if (type === 'result') {
        if (event.payload && typeof event.payload === 'object') {
          finalPayload = event.payload;
        } else {
          finalPayload = {};
        }
        const statusCode = typeof event.status === 'number' ? event.status : 200;
        if (statusCode >= 400) {
          streamErrorMessage = message || `HTTP ${statusCode}`;
        }
        return;
      }

      if (type === 'error') {
        streamErrorMessage = message || 'ストリーム応答でエラーが発生しました。';
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let lineBreakIndex = buffer.indexOf('\n');
      while (lineBreakIndex >= 0) {
        const line = buffer.slice(0, lineBreakIndex);
        buffer = buffer.slice(lineBreakIndex + 1);
        consumeLine(line);
        lineBreakIndex = buffer.indexOf('\n');
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      consumeLine(buffer);
    }

    if (streamErrorMessage) {
      throw new Error(streamErrorMessage);
    }

    if (finalPayload) {
      return finalPayload;
    }

    throw new Error('ストリーム応答が不完全でした。');
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
    setCurrentStatus('リクエストを送信しています');
    let executionSteps = ['リクエストを送信しています'];

    const localFallback = applyDeviceCommand(text, devices);
    const addExecutionStep = (step: string) => {
      executionSteps = appendStep(executionSteps, step);
    };

    try {
      const data = await requestAssistantResponseStream(nextHistory, ({ message }) => {
        setCurrentStatus(message);
        addExecutionStep(message);
      });
      const reply = typeof data?.reply === 'string' ? data.reply : '';
      const images = Array.isArray(data?.images) ? data.images : [];
      const cleanReply = reply.trim();
      addExecutionStep('応答を返しました');
      const messageExecutionLog = toMessageExecutionLog(executionSteps, 'success');

      if (cleanReply || images.length > 0) {
        pushMessage('assistant', cleanReply, images, messageExecutionLog);
      } else if (localFallback) {
        pushMessage('assistant', localFallback, [], messageExecutionLog);
      } else {
        pushMessage('assistant', '了解しました。', [], messageExecutionLog);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'エラーが発生しました。';
      addExecutionStep(`エラー: ${errorMessage}`);
      const messageExecutionLog = toMessageExecutionLog(executionSteps, 'error');
      if (localFallback) {
        pushMessage('assistant', localFallback, [], messageExecutionLog);
      } else if (err instanceof Error) {
        pushMessage('assistant', `エラーが発生しました: ${err.message}`, [], messageExecutionLog);
      } else {
        pushMessage('assistant', 'エラーが発生しました。', [], messageExecutionLog);
      }
    } finally {
      setIsSending(false);
      setCurrentStatus('');
    }
  };

  const handleReset = () => {
    setMessages([{ role: 'assistant', content: INITIAL_GREETING, time: nowTime(), images: [] }]);
    setIsPaused(false);
    setIsSending(false);
    setCurrentStatus('');
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
                  {msg.role === 'assistant' && msg.executionLog?.steps?.length ? (
                    <details className="message-execution-log">
                      <summary className="message-execution-log__summary">
                        実行ログ ({messageLogStatusLabel(msg.executionLog.status)})
                      </summary>
                      <ol className="message-execution-log__steps">
                        {msg.executionLog.steps.map((step, stepIndex) => (
                          <li key={`${msg.time}-exec-${stepIndex}`}>{step}</li>
                        ))}
                      </ol>
                    </details>
                  ) : null}
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
                    aria-label={currentStatus ? `LLM が処理中です: ${currentStatus}` : 'LLM が応答を生成中です'}
                  >
                    <span className="thinking-indicator__dots" aria-hidden="true">
                      <span className="thinking-indicator__dot" />
                      <span className="thinking-indicator__dot" />
                      <span className="thinking-indicator__dot" />
                    </span>
                    <span className="thinking-indicator__label">{currentStatus || 'Thinking...'}</span>
                  </span>
                </div>
                <div className="message__meta">LLM ・ {currentStatus || '応答を生成中'}</div>
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
