# IoT Agent

<img src="static/README-Logo/IoT-Agent-Logo.png" width="800" alt="IoT Agent Logo">


## 概要
このプロジェクトは、FastAPI ベースの IoT 管理サーバー、シングルページ風ダッシュボード、Jetson / Raspberry Pi / Pico W 向けの参照クライアントをまとめ、自然言語チャットからデバイスジョブを配信できるようにします。バックエンドは `app.py` が担い、API・セッション認証・LLM 連携・静的ファイル配信を単一プロセスで実装しています。
This project bundles a FastAPI IoT management server, a single-page style dashboard, and reference clients for Jetson, Raspberry Pi, and Pico W so that natural language chat can dispatch device jobs. The backend lives in `app.py`, which exposes the APIs, session auth, LLM integration, and static asset delivery from one process.

## 主な特長
- チャット駆動のデバイス制御 — OpenAI Responses API（既定は `GPT-OSS`）を中心に複数モデルへ切り替え可能で、自然言語からエージェントジョブを組み立てます。  
  - Chat-driven device control — Uses the OpenAI Responses API (default `GPT-OSS`) with optional model switching to map natural language to agent jobs.
- ブラウザダッシュボード — 左ペインでチャット、右ペインでデバイスカードや登録モーダルを表示し、数秒毎のポーリングで状態を同期します。  
  - Browser dashboard — Chat on the left, device cards and registration modal on the right, refreshed via multi-second polling.
- メモリ内ジョブ管理 — `DeviceState` と FIFO キューでジョブ投入、`wait_for_result` や結果要約をサポートし、最大 `MAX_COMPLETED_JOBS` 件までリングバッファに保持します。  
  - In-memory job tracking — `DeviceState` objects keep FIFO queues, support `wait_for_result`, and rotate completed jobs up to `MAX_COMPLETED_JOBS`.
- カメラ／マルチモーダル対応 — 画像撮影ジョブ (`capture_camera_photo`) やビジョンモデル連携、LLM 応答の多言語サポートを備えています。  
  - Camera and multimodal support — Provides `capture_camera_photo`, forwards captures to vision models, and returns multilingual responses.

## リポジトリ構成
- `app.py` — FastAPI アプリ本体。認証、チャット、ジョブ、デバイス API がここに集中しています。  
  - `app.py` — Core FastAPI app hosting auth, chat, job, and device APIs.
- `index.html` / `app.js` / `styles.css` — UI 全体、チャット、デバイスグリッド、モーダルの描画を担当。  
  - `index.html` / `app.js` / `styles.css` — Render the UI, chat workflow, device grid, and modal dialogs.
- `login.html` — パスワード入力画面を提供し、成功時はダッシュボードへ遷移させます。  
  - `login.html` — Password prompt that routes to the dashboard after a successful login.
- `static/` — 画像や追加の JS/CSS アセット置き場（`app.py` から静的配信）。  
  - `static/` — Extra JS/CSS/static assets served by `app.py`.
- `edge_device_code/` — ハードウェア別クライアントと `device_test/` ユーティリティ（`jetson/`, `raspberrypi4/`, `raspberrypi-pico/`）。  
  - `edge_device_code/` — Platform-specific clients plus `device_test/` utilities for Jetson, Raspberry Pi 4, and Raspberry Pi Pico W.
- `Dockerfile` / `docker-compose.yml` — Gunicorn(Uvicorn worker) イメージとホットリロード開発環境のビルド定義。  
  - `Dockerfile` / `docker-compose.yml` — Build the Gunicorn (Uvicorn worker) image and the hot-reload dev environment.
- `requirements.txt` — FastAPI、dotenv、OpenAI SDK など Python 依存関係を固定。  
  - `requirements.txt` — Pins Python dependencies like FastAPI, dotenv, and the OpenAI SDK.
- `tests/` — pytest モジュール置き場。FastAPI ルーティングの基本テストを含みます。  
  - `tests/` — pytest modules, including basic FastAPI routing coverage.
- `view_prompt.txt` / `AGENTS.md` など — LLM プロンプトや運用方針を記述した補助資料。  
  - `view_prompt.txt` / `AGENTS.md` — Supplemental docs for LLM prompts and operational guidance.

## セットアップ手順
1. Python / venv — Python 3.11 で検証済み。仮想環境を推奨します。  
   - Python / venv — Verified on Python 3.11; use a virtual environment.
   ```bash
   python3.11 -m venv .venv
   . .venv/bin/activate
   ```
2. 依存関係インストール — requirements を一括導入します。  
   - Install dependencies — Pull all Python requirements.
   ```bash
   pip install -r requirements.txt
   ```
3. 環境変数の準備 — `secrets.env` を作成し、API キーやパスワードを平文で置かないようにします。  
   - Prepare environment variables — Populate `secrets.env` (or export vars) instead of hardcoding secrets.

## 実行方法
- ローカル開発（FastAPI リロード） — 環境変数を読み込み、リロード付きで 5006 番ポートを公開します。  
  - Local development (FastAPI reload) — Load env vars and expose port 5006 with the reloader.
  ```bash
  uvicorn app:app --host=0.0.0.0 --port=5006 --reload
  ```
- docker-compose — ホットリロード付きの開発用サービスをビルドし、同様のポートで公開します。  
  - docker-compose — Builds a hot-reload dev container and exposes the same port.
  ```bash
  docker-compose up --build
  ```
- Docker + Gunicorn — 本番相当の Gunicorn イメージをビルドし、`secrets.env` を指定して実行します。  
  - Docker + Gunicorn — Build the production-like image and run it with `secrets.env`.
  ```bash
  docker build -t iot-agent .
  docker run --rm -p 5006:5006 --env-file secrets.env iot-agent
  ```

## 主要環境変数と設定
- `OPENAI_API_KEY` — OpenAI モデル利用時に必須。  
  - `OPENAI_API_KEY` — Required for OpenAI models.
- `CLAUDE_API_KEY` / `ANTHROPIC_API_KEY` — Claude 系モデルを選ぶ場合に設定。  
  - `CLAUDE_API_KEY` / `ANTHROPIC_API_KEY` — Needed when selecting Claude models.
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — Gemini モデル向けの認証情報。  
  - `GEMINI_API_KEY` / `GOOGLE_API_KEY` — Credentials for Gemini models.
- `GROQ_API_KEY` — Groq (Llama) モデル利用時に設定。  
  - `GROQ_API_KEY` — Required for Groq-powered Llama models.
- `FLASK_SECRET_KEY` — セッション暗号化キー（未設定なら `"change-this-secret"`）。  
  - `FLASK_SECRET_KEY` — Session secret (defaults to `"change-this-secret"` if unset).
- `APP_PASSWORD` — ログイン用パスワード。既定は `app.py` 内で `kkawagoe` に固定されているので必ず変更してください。  
  - `APP_PASSWORD` — Login password (hard-coded to `kkawagoe` in `app.py`; change it for production).
- `MAX_COMPLETED_JOBS` — 完了ジョブの保持数（デフォルト 200）。  
  - `MAX_COMPLETED_JOBS` — Number of completed jobs to retain (default 200).
- `DEVICE_RESULT_TIMEOUT` — エッジデバイス結果を待つ秒数（デフォルト 180）。  
  - `DEVICE_RESULT_TIMEOUT` — Seconds to wait for device results (default 180).
- `IOT_AGENT_CAMERA_DIR` / `IOT_AGENT_CAMERA_WARMUP` — 画像保存先とカメラウォームアップ秒数。  
  - `IOT_AGENT_CAMERA_DIR` / `IOT_AGENT_CAMERA_WARMUP` — Photo directory and camera warm-up duration.
- `IOT_AGENT_AUTO_APPROVE` — `1` で能力登録を自動承認、`0` で手動レビューを強制。  
  - `IOT_AGENT_AUTO_APPROVE` — `1` auto-approves capability registration, `0` requires manual review.

## エッジデバイス クライアント
- `edge_device_code/jetson/jetson-iot-edge.py` — Jetson Orin/Nano 向け Python クライアント。  
  - `edge_device_code/jetson/jetson-iot-edge.py` — Python client for Jetson boards.
- `edge_device_code/raspberrypi4/raspberrypi-iot-edge.py` — Raspberry Pi 4 用テンプレート（GPIO/センサー拡張想定）。  
  - `edge_device_code/raspberrypi4/raspberrypi-iot-edge.py` — Raspberry Pi 4 template for GPIO and sensors.
- `edge_device_code/raspberrypi-pico/iot-server-edge.py` — MicroPython ベースの Pico W クライアント。  
  - `edge_device_code/raspberrypi-pico/iot-server-edge.py` — MicroPython client for Pico W.
- `edge_device_code/*/device_test/` — カメラ、センサー、接続確認用の個別テストスクリプト。対象ボード上で直接実行してください。  
  - `edge_device_code/*/device_test/` — Device-specific health checks for camera, sensors, or connectivity; run them on the target board.
- すべてのクライアントは `/api/devices/<device_id>/jobs/next` でジョブをポーリングし、`/jobs/result` へ結果（必要に応じて base64 画像）を返します。  
  - Every client polls `/api/devices/<device_id>/jobs/next` and posts results (plus optional base64 images) to `/jobs/result`.

## REST API ハイライト
| メソッド / Method | パス / Path | 説明 / Description |
| --- | --- | --- |
| GET | `/` | 認証済みならダッシュボードを返し、未認証なら `login.html` を提供します。<br>Returns the dashboard for authenticated users or `login.html` otherwise. |
| GET / POST / DELETE | `/api/session` | セッション状態確認、JSON ログイン、ログアウトをひとまとめに提供します。<br>Checks the session, accepts JSON login, and clears sessions. |
| POST | `/api/chat` | 会話履歴を LLM へ渡し、応答やキューイングされたデバイスジョブ結果を返却します。<br>Sends the conversation to the LLM and returns responses plus queued job outcomes. |
| GET | `/api/models` | 選択可能な LLM 一覧と現在の選択情報を返します。<br>Lists all available LLM options and the current selection. |
| GET | `/api/dependencies` | 主要依存関係と必須環境変数のセット状況を確認するヘルスエンドポイント。<br>Health endpoint listing dependency versions and env-variable availability. |
| POST | `/api/devices/register` | 新規デバイスメタと capabilities を受け取り、`_DEVICES` に登録します。<br>Registers a device plus its capabilities into `_DEVICES`. |
| GET | `/api/devices` | 登録済みデバイス一覧と最新ジョブ状態を配信します。<br>Returns the device list with latest job metadata. |
| PATCH | `/api/devices/<device_id>/name` | デバイス表示名を更新します。<br>Updates a device display name. |
| GET / POST | `/api/devices/<device_id>/jobs` | ジョブ履歴取得や手動ジョブ投入、`wait_for_result` 指定も可能です。<br>Reads job history or enqueues manual jobs, optionally waiting for completion. |
| GET | `/api/devices/<device_id>/jobs/next` | エッジが次のジョブをポーリングするためのエンドポイント。<br>Polling endpoint for edge clients to retrieve the next job. |
| POST | `/api/devices/<device_id>/jobs/result` | 実行結果や添付データをサーバーへ戻します。<br>Uploads execution results and attachments back to the server. |
| GET / DELETE | `/api/jobs/<job_id>` | 任意ジョブの状態確認およびキャンセル。<br>Fetches a job status or cancels it if still pending. |
| GET | `/api/ping` | 最小限のヘルスチェック応答。<br>Simple health check endpoint. |

## ジョブとデータ管理
- `_DEVICES` / `_PENDING_JOBS` / `_JOB_METADATA` / `_COMPLETED_JOBS` はすべてプロセス内メモリに存在し、プロセス再起動でリセットされます。  
  - `_DEVICES`, `_PENDING_JOBS`, `_JOB_METADATA`, and `_COMPLETED_JOBS` live in-process and reset on restart.
- 各デバイスには FIFO キューが割り当てられ、最前のジョブをポーリングしたクライアントが責任を持って処理します。  
  - Each device owns a FIFO queue; the client pulling the head job is responsible for executing it.
- `MAX_COMPLETED_JOBS` を超える結果は古い順にドロップされ、メモリ使用量を制御します。  
  - Results beyond `MAX_COMPLETED_JOBS` are dropped oldest-first to bound memory usage.

## フロントエンドと UX
- `app.js` は 5 秒間隔で `/api/devices` をポーリングし、チャットログとデバイスカードを書き換えます。  
  - `app.js` polls `/api/devices` every 5 seconds to refresh chat logs and device cards.
- チャット UI は入力サニタイズ、通知、折りたたみ、モデル切り替えドロップダウンを提供します。  
  - The chat UI offers input sanitization, notifications, collapsible sections, and a model switcher.
- `capture_camera_photo` を含むジョブでは、Raspberry Pi などから Picamera2 で撮影した JPEG を base64 で受け取り、LLM へ画像 URL として渡します。  
  - Jobs containing `capture_camera_photo` receive base64 JPEGs (e.g., from Picamera2) and forward their data URLs to a vision-capable LLM.
- `styles.css` でグリッド、モーダル、チャットレイアウトを定義し、HTML 側の id と JS セレクタを一致させています。  
  - `styles.css` defines the grid, modal, and chat layout while keeping HTML ids aligned with JS selectors.

## 開発・テストのヒント
- `tests/` に FastAPI ルーティングの基本テストを追加済みです。必要に応じて機能追加時に拡張してください。  
  - Basic FastAPI routing tests live under `tests/`; extend them when adding functionality.
- ハードウェア依存の変更を行う場合は、対象ボード上で `edge_device_code/*/device_test/` のスクリプトを直接実行し、PR で手動検証手順を記録します。  
  - For hardware-facing changes, run the scripts under `edge_device_code/*/device_test/` on the actual board and document manual validation steps in your PR.
- LLM や外部 API を変えるときは早めにレビューを依頼し、エッジクライアント所有者が追従できるようにします。  
  - Request early reviews when changing LLMs or APIs so edge-client owners can adapt quickly.
- `plan` スキルや AGENTS の指示に従い、作業計画を共有してから大きな変更を進めてください。  
  - Follow the `plan` skill / AGENTS guidance and communicate plans before landing large changes.

## セキュリティと運用上の注意
- すべてのシークレットは `secrets.env` またはインフラ側のシークレットマネージャーで管理し、リポジトリに含めないでください。  
  - Keep all secrets in `secrets.env` or a platform secret manager; never commit them.
- デプロイ前に `APP_PASSWORD`、`OPENAI_API_KEY` などを更新し、必要に応じてローテーションしてください。  
  - Rotate `APP_PASSWORD`, `OPENAI_API_KEY`, and other credentials before deployment.
- `_normalise_capabilities` を通じてデバイス能力スキーマを検証し、想定外のコマンド注入を防ぎます。  
  - Validate capability schemas via `_normalise_capabilities` to prevent unexpected command injection.
- 公開環境では TLS 終端、HTTPS 配信、永続ストレージ導入を検討し、プロセス再起動によるデータ喪失を避けます。  
  - In production, terminate TLS, serve over HTTPS, and add persistence to avoid data loss on restarts.
- `IOT_AGENT_AUTO_APPROVE=0` とすることで新規デバイスを手動承認し、運用リスクを下げられます。  
  - Set `IOT_AGENT_AUTO_APPROVE=0` to require manual approval for new devices and reduce operational risk.

## ライセンス / License
このプロジェクトは MIT ライセンスの下で公開されています。詳細は [LICENSE.md](LICENSE.md) を参照してください。  
This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.
