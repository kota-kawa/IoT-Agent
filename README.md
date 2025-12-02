# IoT Agent 管理システム

## 概要
このリポジトリは、Flask ベースの IoT 管理サーバーとフロントエンドダッシュボード、
および主要なエッジデバイス向けのリファレンス実装をまとめたものです。
単一の Python アプリケーション (`app.py`) がバックエンド API と Web UI 配信を担い、
OpenAI API を利用したチャット型オーケストレーションでデバイス制御を支援します。
セッション認証済みユーザーは、ブラウザ上のチャットから自然言語で指示を与え、
登録済みデバイスの機能を呼び出せます。

## リポジトリ構成
- `app.py` — Flask アプリ本体。認証、チャット、ジョブ管理、デバイス登録 API を実装。
- `index.html` / `app.js` / `styles.css` — ダッシュボード UI。チャットサイドバーとデバイスカードを描画。
- `login.html` — シンプルなパスワード入力画面。
- `edge_device_code/` — Jetson、Raspberry Pi 4、Raspberry Pi Pico W 向けの Python クライアント例。
- `Dockerfile` — 本番運用向け Gunicorn コンテナイメージのビルド手順。
- `docker-compose.yml` — 開発中にホットリロードで Flask サーバーを起動する docker-compose サービス。
- `requirements.txt` — サーバーが必要とする Python パッケージ。

## 実行前の準備
1. **Python**: バックエンドは Python 3.11 で検証されています。
2. **依存関係のインストール**:
   ```bash
   pip install -r requirements.txt
   ```
3. **環境変数 (secrets.env 推奨)**:
   - `OPENAI_API_KEY` — LLM 呼び出しに使用する OpenAI API キー。
   - `CLAUDE_API_KEY` または `ANTHROPIC_API_KEY` — Claude 系モデルを選択する場合に使用。
   - `GEMINI_API_KEY` または `GOOGLE_API_KEY` — Gemini 系モデルを選択する場合に使用。
   - `GROQ_API_KEY` — Groq (Llama) モデルを選択する場合に使用。
   - `FLASK_SECRET_KEY` — Flask セッション暗号化キー (未設定時は "change-this-secret")。
   - `MAX_COMPLETED_JOBS` — 完了ジョブの保持数 (デフォルト 200)。
   - `DEVICE_RESULT_TIMEOUT` — エッジ結果待機の秒数 (デフォルト 60 秒)。
   - `APP_PASSWORD` はコード上で `kkawagoe` に固定されています。運用時は `app.py` の定数を変更してください。

## 起動方法
### ローカル開発 (Flask リロードモード)
```bash
export FLASK_APP=app
flask run --host=0.0.0.0 --port=5006
```
または `docker-compose up --build` で同じコマンドをコンテナ内で実行できます。

### Gunicorn (Dockerfile)
```bash
docker build -t iot-agent .
docker run --rm -p 5006:5006 --env-file secrets.env iot-agent
```

## 認証とフロントエンド
- ルート (`/`) へアクセスすると、未認証の場合は `login.html` が表示されます。
- パスワードが正しいとセッションに `authenticated` フラグがセットされ、`index.html` が提供されます。
- ダッシュボードは左にチャット、右にデバイス一覧カードを表示し、登録ダイアログからデバイスを追加できます。

## チャットと LLM 連携
- `/api/chat` にユーザーとアシスタントの会話履歴を JSON で送信すると、OpenAI Responses API（デフォルトは `gpt-5.1`、ドロップダウンから即時切替可）を介して
  日本語回答とデバイスコマンド候補を生成します。
- エージェント用デバイスが登録済みの場合は、指示文を英語へ変換してエッジ側に送信し、結果を待機・要約します。
- エージェント不在時は LLM 応答のみを返し、コマンドは実行されません。
- 周囲確認やカメラ撮影を求める発話があれば、視覚モデル選択時に `capture_camera_photo` を自動キューイングし、Raspberry Pi が Picamera2 で JPEG を撮影して base64 付きでサーバーへ返送します。サーバーは受け取った画像データ URL をビジョン対応 LLM へ渡し、画像内容を踏まえた最終回答を返します（保存先は `IOT_AGENT_CAMERA_DIR`、ウォームアップ秒数は `IOT_AGENT_CAMERA_WARMUP` で調整可能）。

## REST API ダイジェスト
| メソッド | パス | 説明 |
| --- | --- | --- |
| GET | `/` | 認証済みならダッシュボード、未認証ならログイン画面。
| GET/POST/DELETE | `/api/session` | セッション状態確認、JSON ログイン、ログアウト。
| POST | `/api/chat` | チャットメッセージを処理し、LLM 応答と実行結果を返却。
| GET | `/api/models` | 利用可能な LLM モデル一覧と現在の選択を返却。
| GET | `/api/dependencies` | 主要依存関係と必須環境変数のセット状況を返却（値は返さない）。
| POST | `/api/devices/register` | 新規デバイス登録 (能力一覧とメタ情報を受け取る)。
| GET | `/api/devices` | 登録済みデバイス一覧。
| PATCH | `/api/devices/<device_id>/name` | 表示名の更新。
| DELETE | `/api/devices/<device_id>` | デバイス削除とキューのクリーンアップ。
| GET | `/api/devices/<device_id>/jobs` | ジョブ履歴と結果。
| POST | `/api/devices/<device_id>/jobs` | 手動ジョブ投入。`wait_for_result` で同期待機も可能。
| GET | `/api/devices/<device_id>/jobs/next` | エッジデバイスが次ジョブを取得するポーリング用。
| POST | `/api/devices/<device_id>/jobs/result` | エッジ側が実行結果をアップロード。
| GET | `/api/jobs/<job_id>` | 任意ジョブの状態確認。
| DELETE | `/api/jobs/<job_id>` | キュー上に残るジョブのキャンセル。
| GET | `/api/ping` | 動作確認用の簡易ヘルスチェック。

## ジョブとデータ管理
- デバイス、ジョブ、結果はすべてアプリケーションプロセス内メモリで保持されます。
- `_DEVICES` に `DeviceState` が保存され、各デバイスは FIFO ジョブキューを持ちます。
- `_PENDING_JOBS` / `_JOB_METADATA` / `_COMPLETED_JOBS` でジョブ状態を追跡し、完了済みは最大 `MAX_COMPLETED_JOBS` 件にローテーションします。
- 永続化は行われないため、プロセス再起動で全データが消去されます。

## エッジデバイス クライアント
- `edge_device_code/jetson/jetson-iot-edge.py` — Jetson 向けサンプル。REST API を通じてジョブ取得と結果送信を行います。
- `edge_device_code/raspberrypi4/raspberrypi-iot-edge.py` — Raspberry Pi 4 向け。GPIO やセンサー制御をカスタマイズするためのテンプレート。
- `edge_device_code/raspberrypi-pico/iot-server-edge.py` — MicroPython ベースの Pico W 用実装。Wi-Fi 経由でジョブを処理します。
- 上記サンプルは `secrets.py` 等でサーバー URL・デバイス ID・認証情報を設定した後、ジョブポーリング (`/jobs/next`) と結果報告 (`/jobs/result`) を行います。
- Raspberry Pi 4/Jetson エージェントは `IOT_AGENT_AUTO_APPROVE=1`（デフォルト）で起動するとサーバー側の手動承認なしで capabilities を登録します。セキュリティの都合で承認を必須にしたい場合は `IOT_AGENT_AUTO_APPROVE=0` を設定してください。

## フロントエンドの特徴
- `app.js` は 5 秒間隔で `/api/devices` をポーリングし、カード表示やメタ情報を整形します。
- チャット欄では入力のサニタイズ、折りたたみ表示、通知表示など UI コンポーネントを用意。
- `styles.css` はダッシュボード全体のレイアウト、カードグリッド、モーダルダイアログ、チャット欄のスタイルを定義します。

## 開発メモ
- すべての API はセッション認証を前提としているため、フロントエンドからの fetch は認証後に実行されます。
- LLM 呼び出しはネットワークに依存し、例外発生時は 500 応答とエラーメッセージを返します。
- 本番環境では HTTPS 経由での提供、環境変数によるパスワード設定、永続データストアの導入をご検討ください。
