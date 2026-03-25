> 📖 一番下に日本語版もあります

# 🤖 IoT Agent

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?logo=openai&logoColor=white)
![Anthropic](https://img.shields.io/badge/Anthropic-Claude-D97757?logo=anthropic&logoColor=white)
![Gemini](https://img.shields.io/badge/Google-Gemini-4285F4?logo=google&logoColor=white)

<img src="static/README-Logo/IoT-Agent-Logo.png" width="800" alt="IoT Agent Logo">

## 🖼️ UI Preview
<img src="assets/images/iot-agent-page.png" width="100%" alt="IoT Agent Dashboard UI Preview">

## 🎬 Demo Videos
Click a thumbnail to open the video on YouTube.

<table width="100%"><tr>
<td width="33%" align="center"><a href="https://youtu.be/sbWKMEcJsyg"><img src="https://img.youtube.com/vi/sbWKMEcJsyg/hqdefault.jpg" width="100%" alt="Demo Video 1: UI example and chat system operation demo"></a><br><b>UI example and chat system operation demo</b></td>
<td width="33%" align="center"><a href="https://youtu.be/RnNsK7jrAZI"><img src="https://img.youtube.com/vi/RnNsK7jrAZI/hqdefault.jpg" width="100%" alt="Demo Video 2: Measure distance and display the result"></a><br><b>Measure distance and display the result</b></td>
<td width="33%" align="center"><a href="https://youtu.be/nN1FbHb85XQ"><img src="https://img.youtube.com/vi/nN1FbHb85XQ/hqdefault.jpg" width="100%" alt="Demo Video 3: Move motors on all devices"></a><br><b>Move motors on all devices</b></td>
</tr></table>

## 👋 Introduction
Welcome to **IoT Agent**!
This project lets you control nearby IoT devices (robots, sensors, and more) just by **chatting**.
Say things like "Take a picture" or "Move forward" and the AI will understand and instruct your devices.

## ✨ Key Features
- **💬 Chat Control**: Operate devices with natural conversation.
- **💻 Clear Dashboard**: Check device status from your browser.
- **📷 Camera Support**: Ask for a snapshot anytime.
- **🧠 Smart AI**: Works with modern AI providers (OpenAI, Gemini, etc.).

## 🚀 Quick Start (Docker Compose)

### 1. Prerequisites
- **Docker**
- **Docker Compose** (Docker Desktop includes it)

### 2. Configure secrets
Create `secrets.env` from the example and set your keys.

```bash
cp secrets.env.example secrets.env
```

```env
OPENAI_API_KEY="sk-..."
LLM_DAILY_API_LIMIT="1000"
IOT_AGENT_API_BASE_URL="https://iot-agent.example.com"
```

If `IOT_AGENT_API_BASE_URL` is blank, it defaults to `http://localhost:5006/`.
`LLM_DAILY_API_LIMIT=0` disables the daily limit.

### 3. Run
```bash
docker-compose up --build
```

Open `http://localhost:5006` in your browser to see the dashboard.

## 🤖 Supported Devices
- **NVIDIA Jetson** (AI robots, etc.)
- **Raspberry Pi 4** (sensors, cameras)
- **Raspberry Pi Pico W** (small projects)

## 🧪 Evaluation

### IoT Agent

**Role**
The IoT Agent translates natural-language requests into executable device-control commands across heterogeneous edge devices.

**Evaluation Protocol**
I designed 10 tasks ranging from:
- single-device control
- multi-device coordination
- context-aware actions
- visually grounded interaction

Each task was evaluated with a three-level outcome:
- **○** full success
- **△** partial success
- **×** failure

**Result**
High-capability models such as **Claude Opus 4.5** and **Gemini 3 Pro** showed strong robustness, including on abstract requests.
At the same time, the experiments also showed that **small edge LLMs can still execute practical device-control tasks reliably** when function schemas and system prompts are carefully designed.

**Failure Analysis**
Some failures were caused not by raw reasoning weakness, but by mismatches in tool invocation, timeout handling, and device-specific branching.

**Why this matters**
This supports a **hierarchical cloud-edge architecture**: high-level planning in the cloud, low-latency/private execution on edge devices.

## 📄 License
This project is licensed under the **MIT License**. Feel free to modify and share.
See [LICENSE.md](LICENSE.md) for details.

<details>
<summary>日本語</summary>

## 🖼️ UIプレビュー
<img src="assets/images/iot-agent-page.png" width="100%" alt="IoT Agent ダッシュボード UI プレビュー">

## 🎬 デモ動画
サムネイルをクリックすると、YouTubeに移動して動画を再生できます。

<table width="100%"><tr>
<td width="33%" align="center"><a href="https://youtu.be/sbWKMEcJsyg"><img src="https://img.youtube.com/vi/sbWKMEcJsyg/hqdefault.jpg" width="100%" alt="デモ動画 1: UIの例で、チャットシステムの動作例"></a><br><b>UIの例で、チャットシステムの動作例</b></td>
<td width="33%" align="center"><a href="https://youtu.be/RnNsK7jrAZI"><img src="https://img.youtube.com/vi/RnNsK7jrAZI/hqdefault.jpg" width="100%" alt="デモ動画 2: 距離を測った後に、その結果をディスプレイに表示"></a><br><b>距離を測った後に、その結果をディスプレイに表示</b></td>
<td width="33%" align="center"><a href="https://youtu.be/nN1FbHb85XQ"><img src="https://img.youtube.com/vi/nN1FbHb85XQ/hqdefault.jpg" width="100%" alt="デモ動画 3: すべてのデバイスのモーターを動かした"></a><br><b>すべてのデバイスのモーターを動かした</b></td>
</tr></table>

## 👋 はじめに
**IoT Agent** へようこそ！
このプロジェクトは、**チャットをするだけで** 周りのIoTデバイス（ロボットやセンサーなど）を操作できるシステムです。
「写真を撮って」「前に進んで」と話しかけるだけで、AIが理解してデバイスに指示を出してくれます。

## ✨ 主な機能
- **💬 チャットで操作**: 自然な会話でデバイスを動かせます。
- **💻 見やすいダッシュボード**: ブラウザから状態を確認できます。
- **📷 カメラ対応**: いつでも写真を撮って送ってくれます。
- **🧠 賢いAI**: OpenAIやGeminiなどのAIに対応しています。

## 🚀 はじめ方（Docker Compose）

### 1. 必要なもの
- **Docker**
- **Docker Compose**（Docker Desktopに同梱）

### 2. 設定
`secrets.env` を例から作成し、キーを設定します。

```bash
cp secrets.env.example secrets.env
```

```env
OPENAI_API_KEY="sk-..."
LLM_DAILY_API_LIMIT="1000"
IOT_AGENT_API_BASE_URL="https://iot-agent.example.com"
```

`IOT_AGENT_API_BASE_URL` を空にすると `http://localhost:5006/` が使われます。
`LLM_DAILY_API_LIMIT=0` で日次制限を無効化できます。

### 3. 起動
```bash
docker-compose up --build
```

ブラウザで `http://localhost:5006` を開くとダッシュボードが表示されます。

## 🤖 対応デバイス
- **NVIDIA Jetson**（AIロボットなど）
- **Raspberry Pi 4**（センサーやカメラ）
- **Raspberry Pi Pico W**（小さな工作向け）

## 🧪 評価

### IoT Agent

**役割**
IoT Agentは、自然言語によるリクエストを、異種エッジデバイス上で実行可能なデバイス制御コマンドに変換します。

**評価プロトコル**
以下の内容を含む10タスクを設計しました：
- 単一デバイス制御
- 複数デバイスの連携操作
- コンテキストに応じた動作
- 視覚情報を活用したインタラクション

各タスクは3段階の結果で評価しました：
- **○** 完全成功
- **△** 部分的成功
- **×** 失敗

**結果**
**Claude Opus 4.5** や **Gemini 3 Pro** などの高性能モデルは、抽象的なリクエストに対しても高い堅牢性を示しました。
同時に、**関数スキーマとシステムプロンプトを適切に設計すれば、小規模なエッジLLMでも実用的なデバイス制御タスクを安定して実行できる**ことが実験から示されました。

**失敗分析**
一部の失敗は、推論能力の低さではなく、ツール呼び出しの不一致・タイムアウト処理・デバイス固有の分岐処理におけるミスマッチが原因でした。

**意義**
この結果は、**階層型クラウドエッジアーキテクチャ**（クラウドで高度な計画を行い、エッジデバイスで低レイテンシかつプライベートに実行する）の有効性を支持しています。

## 📄 ライセンス
このプロジェクトは **MITライセンス** です。自由に改造して遊んでください！
詳細は [LICENSE.md](LICENSE.md) をご覧ください。

</details>
