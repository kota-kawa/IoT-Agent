# Current Task Context

## 今回やること・目的 (Goal/Objective)
<!-- 何のために何をするのか簡潔に記述 -->
- [ ] フロントエンドを React + TypeScript に完全移行し、既存 UI/機能を維持しながら型安全性と保守性を大幅に向上させる。

## やること (Must)
<!-- 具体的なタスクリスト -->
- [ ] `frontend/src` 配下の **すべての .js/.jsx を .ts/.tsx へ移行**し、JSX/ロジックを TypeScript 化する。
- [ ] 型定義の追加・整備（例: `Device`, `DeviceMeta`, `Capability`, `DeviceResult`, `ChatMessage`, `ModelOption`, `AppConfig` など）。
- [ ] API レスポンスの型定義と **`fetchJson<T>()` のジェネリクス化**で型安全な通信にする。
- [ ] `window.__APP_CONFIG__` の型宣言を追加し、`config.js` 連携を型安全化する。
- [ ] `tsconfig.json` / `tsconfig.node.json` を追加し **strict 有効**でビルド可能にする。
- [ ] `package.json` に TypeScript 関連依存（`typescript`, `@types/react`, `@types/react-dom`, `@types/react-router-dom`, `@types/node` など）を追加。
- [ ] `frontend/index.html` のエントリを `main.tsx` へ切替（既存機能・表示の維持）。
- [ ] **React 側でのインライン style を撤去**し、既存 CSS に寄せる（`VirtualDeviceCard` など）。
- [ ] 既存の **CSS クラス・id（例: `#deviceGrid`, `#registerNotice`）を保持**して互換性を維持。
- [ ] Vite 設定・ビルド出力（`frontend/dist_v2`）を維持し、`npm run build` で成果物が生成されることを保証。
- [ ] 画面・挙動の回帰を防ぐため、**主要フローの手動確認手順**を明記する。

## やらないこと (Non-goals)
<!-- 今回のスコープ外のこと -->
- [ ] UI/UX の大幅な刷新、デザイン変更、機能追加。
- [ ] FastAPI バックエンド API の仕様変更・エンドポイント追加。
- [ ] DB/ストレージ、認証方式、デバイスプロトコルの改修。
- [ ] 既存の API レスポンス構造の変更。

## 受け入れ基準 (Acceptance Criteria)
<!-- 完了とみなす条件 -->
- [ ] `frontend/` で `npm run build` が成功し、`frontend/dist_v2` が生成される。
- [ ] `frontend/` で `npm run dev` 起動後、`/`, `/login`, `/agent-result`, `/agent_result.html` で画面が表示される。
- [ ] TypeScript コンパイルが **strict** で通る（`tsc --noEmit` がエラーゼロ）。
- [ ] `frontend/src` 配下に `.js/.jsx` が残っていない（TypeScript へ完全移行）。
- [ ] 既存 UI と主要機能が変わらない（ログイン、デバイス一覧、チャット、登録、モデル切替、通知、削除/リネーム/ジョブ削除）。
- [ ] `window.__APP_CONFIG__` 由来の API Base が従来通り動作する。
- [ ] 既存の CSS クラス・id が維持され、表示/レイアウトに回帰がない。

## 影響範囲 (Impact/Scope)
<!-- 変更するファイルや注意すべき既存機能 -->
- **触るファイル**:
  - `frontend/src/**`（全コンポーネント/ページ/ユーティリティ/フック/スタイル参照）
  - `frontend/index.html`
  - `frontend/package.json`
  - `frontend/vite.config.js`（必要に応じて）
  - `frontend/tsconfig.json`, `frontend/tsconfig.node.json`
  - `frontend/src/vite-env.d.ts`
- **壊しちゃいけない挙動**:
  - 認証状態に応じた `/` → `/login` リダイレクト。
  - `config.js` を経由した API Base 参照。
  - `/api/devices`, `/api/chat`, `/api/models`, `/model_settings`, `/api/session` など既存 API 連携。
  - `body` クラス付け替え（`login-view`, `standalone-view`）。
  - チャットログの自動スクロール、送信制御、リセット。
  - デバイスのリネーム・削除・ジョブ削除、登録ダイアログの挙動。
