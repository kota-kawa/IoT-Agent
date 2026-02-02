# Project Specifications & Guidelines

## 全体ルール (General Rules)
<!-- プロジェクト全体で遵守すべき原則 -->
- フロントエンドは **React + TypeScript へ完全移行**し、`frontend/src` 配下に `.js/.jsx` を残さない。
- TypeScript は **strict モード**を有効化し、`any` の使用を禁止（やむを得ない場合は理由をコメントで明示）。
- API からの値は **不信頼データ**として扱い、型ガード/検証で安全に取り扱う。
- `window.__APP_CONFIG__` を **グローバル型宣言**し、API Base 取得経路を固定する。
- 既存の **URL ルーティング**（`/`, `/login`, `/agent-result`, `/agent_result.html`）と **UI/文言**を維持。
- **CSS クラス/id を維持**し、既存セレクタとの互換性を守る（例: `#deviceGrid`, `#registerNotice`）。
- React コンポーネント内での **インライン style を禁止**（必要なら CSS 変数/クラスで代替）。
- ビルド成果物の出力先は `frontend/dist_v2` を維持し、`app.py` の配信仕様と整合させる。

## コーディング規約 (Coding Conventions)
<!-- 言語ごとのスタイルガイド、フォーマッター設定など -->
- **Python**:
  - PEP 8 準拠、4スペースインデント、dataclasses を優先。
  - モジュール定数は `SCREAMING_SNAKE_CASE`。
- **TypeScript/React**:
  - 2スペースインデント、関数型コンポーネントのみ使用。
  - `type Props = { ... }` で props 定義し、`const Component = (props: Props): JSX.Element => { ... }` を基本形とする。
  - `useState<Type>`, `useRef<Type | null>` など **明示的な型指定**を徹底。
  - `fetchJson<T>()` のジェネリクスで **API 型安全化**。
  - `any` / 過剰な `as` キャストは禁止（必要なら `unknown` + 型ガード）。
  - 既存の UI ロジック（チャット履歴、デバイス表示、通知等）を保持。

## 命名規則 (Naming Conventions)
<!-- 変数、関数、クラス、ファイル名の命名ルール -->
- **Variables/Functions**: `camelCase`（例: `fetchDevices`, `handleSubmit`）
- **Classes/Components/Types**: `PascalCase`（例: `DeviceCard`, `ChatMessage`）
- **Files**: コンポーネントは `PascalCase.tsx`、ユーティリティは `camelCase.ts`
- **Constants**: `SCREAMING_SNAKE_CASE`（例: `FETCH_DEVICES_INTERVAL_MS`）
- **Hooks**: `useXxx` 形式（例: `useRequireAuth`）

## ディレクトリ構成方針 (Directory Structure Policy)
<!-- ファイルの配置ルール、モジュール分割の方針 -->
- `frontend/src/components/`：UI コンポーネント
- `frontend/src/pages/`：ルーティング単位のページ
- `frontend/src/hooks/`：カスタム hooks
- `frontend/src/utils/`：汎用関数
- `frontend/src/api/`：API クライアント（`fetchJson` など）
- `frontend/src/types/`：API/アプリ共通の型定義
- `frontend/src/styles.css`：全体スタイル（既存を維持）

## 型定義・API 契約 (Type Definitions & API Contracts)
- **Device**: `device_id`, `capabilities`, `queue_depth`, `last_seen`, `registered_at`, `last_result`, `meta`。
- **DeviceMeta**: `display_name`, `note`, `label`, `location`, `virtual` など（任意）。
- **DeviceResult**: `ok`, `job_id`, `return_value`, `message` など（任意）。
- **ChatMessage**: `role`, `content`, `time`, `images`。
- **ModelOption**: `provider`, `model`, `label`, `base_url`。
- **AppConfig**: `apiBase`。
- API レスポンスは `response.ok` を必ず判定し、`data` の型は **型ガード**で検証。

## ルーティング/画面仕様 (Routing & UI Behavior)
- `/` と `/index.html` は **ダッシュボード**を表示。
- `/login` と `/login.html` は **ログイン**を表示。
- `/agent-result` と `/agent_result.html` は **結果専用ビュー**を表示。
- ログイン済みで `/login` に来た場合は `/` へ遷移。
- 未ログインで `/` `/agent-result` に来た場合は `/login` に遷移。
- `body` クラス切替（`login-view`, `standalone-view`）を継続。

## エラーハンドリング方針 (Error Handling Policy)
<!-- 例外処理、ログ出力、ユーザーへのフィードバック方法 -->
- API 通信は常に `try/catch` で保護し、失敗時は日本語メッセージで通知。
- 例外発生時も UI が固まらないよう状態を復帰（`submitting`, `isSending` など）。
- `useEffect` 内の非同期処理は `active` フラグで **アンマウント後更新を防止**。
- `fetchJson` で JSON パース失敗時は `data = null` を維持し、`text` を優先表示。

## テスト方針 (Testing Policy)
<!-- テストの種類、カバレッジ目標、使用ツール -->
- **Unit Tests**:
  - 追加する場合は `Vitest + React Testing Library` を想定。
  - `utils`（`formatTimestamp`, `displayName` 等）は最低限のユニットテスト対象。
- **E2E Tests**:
  - 自動化は必須ではないが、以下の **手動スモークテスト**を実施。
    - ログイン成功/失敗、ログアウト。
    - ダッシュボードでのデバイス取得/リネーム/削除/ジョブ削除。
    - チャット送信/履歴表示/画像表示/リセット。
    - モデル選択の更新と `/model_settings` 反映。
    - `npm run build` の成功と `dist_v2` 生成確認。
