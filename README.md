# 🤖 IoT Agent

<img src="static/README-Logo/IoT-Agent-Logo.png" width="800" alt="IoT Agent Logo">

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

## 🎬 Demo Videos
Click a thumbnail to open the video on YouTube.

| [![Demo Video 1: UI example and chat system operation demo](https://img.youtube.com/vi/sbWKMEcJsyg/hqdefault.jpg)](https://youtu.be/sbWKMEcJsyg) | [![Demo Video 2: Measure distance and display the result](https://img.youtube.com/vi/RnNsK7jrAZI/hqdefault.jpg)](https://youtu.be/RnNsK7jrAZI) | [![Demo Video 3: Move motors on all devices](https://img.youtube.com/vi/nN1FbHb85XQ/hqdefault.jpg)](https://youtu.be/nN1FbHb85XQ) |
| --- | --- | --- |
| UI example and chat system operation demo | Measure distance and display the result | Move motors on all devices |

## 📄 License
This project is licensed under the **MIT License**. Feel free to modify and share.
See [LICENSE.md](LICENSE.md) for details.

<details>
<summary>日本語</summary>

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

## 🎬 デモ動画
サムネイルをクリックすると、YouTubeに移動して動画を再生できます。

| [![デモ動画 1: UIの例で、チャットシステムの動作例](https://img.youtube.com/vi/sbWKMEcJsyg/hqdefault.jpg)](https://youtu.be/sbWKMEcJsyg) | [![デモ動画 2: 距離を測った後に、その結果をディスプレイに表示](https://img.youtube.com/vi/RnNsK7jrAZI/hqdefault.jpg)](https://youtu.be/RnNsK7jrAZI) | [![デモ動画 3: すべてのデバイスのモーターを動かした](https://img.youtube.com/vi/nN1FbHb85XQ/hqdefault.jpg)](https://youtu.be/nN1FbHb85XQ) |
| --- | --- | --- |
| UIの例で、チャットシステムの動作例 | 距離を測った後に、その結果をディスプレイに表示 | すべてのデバイスのモーターを動かした |

## 📄 ライセンス
このプロジェクトは **MITライセンス** です。自由に改造して遊んでください！
詳細は [LICENSE.md](LICENSE.md) をご覧ください。

</details>
