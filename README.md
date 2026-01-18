# 🤖 IoT Agent

[🇺🇸 English](README_en.md)

<img src="static/README-Logo/IoT-Agent-Logo.png" width="800" alt="IoT Agent Logo">

## 👋 はじめに
**IoT Agent** へようこそ！
このプロジェクトは、**チャットをするだけで** あなたの周りのIoTデバイス（ロボットやセンサーなど）を自由に操作できるシステムです。
「写真を撮って」「前に進んで」と話しかけるだけで、AIが理解してデバイスに指示を出してくれます。

## ✨ 主な機能
- **💬 チャットで操作**: 自然な会話でデバイスを動かせます。
- **💻 見やすいダッシュボード**: ブラウザからデバイスの状態をチェックできます。
- **📷 カメラ対応**: 「今の様子を見せて」と言えば、写真を撮って送ってくれます。
- **🧠 賢いAI**: 最新のAI（OpenAI, Geminiなど）があなたの言葉を理解します。

## 🚀 使い方

### 1. 準備するもの
- **Python 3.11** 以上
- **Node.js 18+**（フロントエンドのビルド用）
- **APIキー**: OpenAIなどのAIを使うためのキー

### 2. セットアップ
まずは必要なものをインストールしましょう。

```bash
# 仮想環境を作って有効化します
python3.11 -m venv .venv
source .venv/bin/activate  # Windowsの場合は .venv\Scripts\activate

# 必要なライブラリをインストールします
pip install -r requirements.txt
```

### 3. フロントエンドのビルド
```bash
cd frontend
npm install
npm run build
```

### 4. 設定
`secrets.env` というファイルを作って、APIキーを書き込みます。

```env
OPENAI_API_KEY="sk-..."
APP_PASSWORD="あなたの好きなパスワード"
IOT_AGENT_API_BASE_URL="https://iot-agent.example.com"
```

### 5. 実行！
さあ、動かしてみましょう。

**ローカルで動かす場合:**
```bash
uvicorn app:app --host=0.0.0.0 --port=5006 --reload
```

**Dockerで動かす場合:**
```bash
docker-compose up --build
```

ブラウザで `http://localhost:5006` にアクセスすれば、ダッシュボードが表示されます！

## 🤖 対応デバイス
以下のデバイスですぐに使えます：
- **NVIDIA Jetson** (AIロボットなどに)
- **Raspberry Pi 4** (センサーやカメラに)
- **Raspberry Pi Pico W** (小さな工作に)

## 📄 ライセンス
このプロジェクトは **MITライセンス** です。自由に改造して遊んでください！
詳細は [LICENSE.md](LICENSE.md) を見てね。
