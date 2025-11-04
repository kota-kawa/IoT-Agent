# マルチエージェント連携機能

このドキュメントでは、IoT Agent が他のエージェント（FAQ エージェント、Browser エージェント）と連携する機能について説明します。

## 概要

IoT Agent は、タスク実行中に他のエージェントに助けを求めることができます。これにより、各エージェントの専門知識を活用して、より複雑なタスクを効率的に処理できます。

## 対応エージェント

### 1. FAQ エージェント (FAQ_Gemini)

**役割**: 家庭内IoTデバイスと家電製品の専門知識エージェント

**機能**:
- ベクトルデータベース (RAG) を活用した質問応答
- IoT デバイスの使用方法、トラブルシューティング、設定方法などに回答
- デバイス操作の不明点、エラー解決方法、製品仕様の確認

**接続先エンドポイント**:
- `/rag_answer` - 通常の質問応答（会話履歴保存あり）
- `/agent_rag_answer` - エージェント間通信用（会話履歴保存なし）
- `/analyze_conversation` - 会話履歴分析

**デフォルト接続先**:
- `http://localhost:5000`
- `http://faq_gemini:5000`

**環境変数**:
```bash
FAQ_AGENT_API_BASE=http://your-faq-agent:5000
FAQ_AGENT_TIMEOUT=30
```

### 2. Browser エージェント (web_agent02)

**役割**: Web ブラウザ自動化エージェント

**機能**:
- Web サイトの自動操作とスクレイピング
- ページ閲覧、情報抽出、フォーム入力、クリック操作
- IoT デバイスの Web 管理画面操作、オンライン情報収集

**接続先エンドポイント**:
- `/api/chat` - チャットベースのタスク実行
- `/api/check-conversation-history` - 会話履歴確認
- `/api/agent-relay` - エージェント間リレー

**デフォルト接続先**:
- `http://localhost:5005`
- `http://browser-agent:5005`

**環境変数**:
```bash
BROWSER_AGENT_API_BASE=http://your-browser-agent:5005
BROWSER_AGENT_TIMEOUT=120
```

## API エンドポイント

### POST /api/agents/request-help

他のエージェントに助けを求めるエンドポイント

**リクエスト形式**:
```json
{
  "task": "タスクの説明",
  "agent": "faq",
  "context": "追加のコンテキスト情報"
}
```

**パラメータ**:
- `task` (required): タスクの説明
- `agent` (optional): "faq", "browser", "auto" のいずれか。"auto" で自動選択
- `context` (optional): 追加のコンテキスト情報

**レスポンス形式（成功時）**:
```json
{
  "agent_used": "faq",
  "response": "エージェントからの応答",
  "success": true
}
```

**レスポンス形式（エラー時）**:
```json
{
  "agent_used": "faq",
  "success": false,
  "error": "エラーメッセージ"
}
```

### 使用例

#### 1. FAQ エージェントに質問する

```bash
curl -X POST http://localhost:5006/api/agents/request-help \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Raspberry Pi のカメラモジュールの設定方法を教えて",
    "agent": "faq"
  }'
```

#### 2. Browser エージェントに Web タスクを依頼する

```bash
curl -X POST http://localhost:5006/api/agents/request-help \
  -H "Content-Type: application/json" \
  -d '{
    "task": "気象庁のサイトから東京の天気予報を取得して",
    "agent": "browser"
  }'
```

#### 3. 自動でエージェントを選択

```bash
curl -X POST http://localhost:5006/api/agents/request-help \
  -H "Content-Type: application/json" \
  -d '{
    "task": "温度センサーのエラーの解決方法を調べて",
    "agent": "auto"
  }'
```

## コード内でのエージェント呼び出し

### FAQ エージェントを呼び出す

```python
from app import _call_faq_agent

result = _call_faq_agent("Raspberry Pi のセンサーの接続方法は？")
if result:
    answer = result.get("answer")
    sources = result.get("sources")
    print(f"回答: {answer}")
    print(f"ソース: {sources}")
```

### Browser エージェントを呼び出す

```python
from app import _call_browser_agent

result = _call_browser_agent("Googleで'IoT sensor calibration'を検索して結果を要約して")
if result:
    response = result.get("response")
    print(f"結果: {response}")
```

### 最適なエージェントを自動選択

```python
from app import _select_optimal_agent_for_task

task = "温度センサーの校正方法を調べて"
agent = _select_optimal_agent_for_task(task)
print(f"推奨エージェント: {agent}")  # "faq" または "browser"
```

## エージェント選択ロジック

`_select_optimal_agent_for_task()` 関数は、タスクの内容から最適なエージェントを自動選択します：

### FAQ エージェント選択のキーワード
- 質問、教えて、方法、使い方、設定、トラブル、エラー、仕様

### Browser エージェント選択のキーワード
- web, ブラウザ、検索、サイト、ページ、情報収集

### IoT エージェント（自身）選択のキーワード
- デバイス、センサー、制御、操作、測定

## 参照リポジトリ

このマルチエージェント連携機能は、以下のリポジトリを参考に実装されています：

- [Multi-Agent-Platform](https://github.com/kota-kawa/Multi-Agent-Platform) - オーケストレーター
- [FAQ_Gemini](https://github.com/kota-kawa/FAQ_Gemini) - FAQ エージェント
- [web_agent02](https://github.com/kota-kawa/web_agent02) - Browser エージェント

## トラブルシューティング

### エージェントに接続できない

1. エージェントが起動していることを確認
2. 環境変数 `FAQ_AGENT_API_BASE` や `BROWSER_AGENT_API_BASE` が正しく設定されているか確認
3. ネットワーク接続を確認（Docker コンテナ間の場合はネットワーク設定も確認）

### タイムアウトエラー

タイムアウト値を環境変数で調整できます：

```bash
FAQ_AGENT_TIMEOUT=60        # FAQ エージェントのタイムアウト（秒）
BROWSER_AGENT_TIMEOUT=180   # Browser エージェントのタイムアウト（秒）
```

## セキュリティ考慮事項

- エージェント間通信には認証機能がありません。本番環境では適切なネットワークセグメント化やファイアウォール設定を行ってください
- 外部エージェントからの応答を信頼する前に、必要に応じて検証を行ってください
