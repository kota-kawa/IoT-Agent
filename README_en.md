# 🤖 IoT Agent

[🇯🇵 日本語](README.md)

<img src="static/README-Logo/IoT-Agent-Logo.png" width="800" alt="IoT Agent Logo">

## 👋 Introduction
Welcome to **IoT Agent**!
This project allows you to freely control IoT devices (robots, sensors, etc.) around you simply by **chatting**.
Just say "Take a picture" or "Move forward", and the AI will understand and instruct the devices accordingly.

## ✨ Key Features
- **💬 Chat Control**: Operate devices with natural conversation.
- **💻 Clear Dashboard**: Check device status from your browser.
- **📷 Camera Support**: Say "Show me what's happening", and it will take a picture and send it to you.
- **🧠 Smart AI**: Latest AIs (OpenAI, Gemini, etc.) understand your words.

## 🚀 Usage

### 1. Prerequisites
- **Python 3.11** or higher
- **Node.js 18+** (for building the frontend)
- **API Keys**: Keys for using AI such as OpenAI

### 2. Setup
First, let's install the necessary dependencies.

```bash
# Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # For Windows: .venv\Scripts\activate

# Install required libraries
pip install -r requirements.txt
```

### 3. Build the Frontend
```bash
cd frontend
npm install
npm run build
```

### 4. Configuration
Create a `secrets.env` file and write your API keys.

```env
OPENAI_API_KEY="sk-..."
APP_PASSWORD="your_preferred_password"
```

### 5. Run!
Let's get it moving.

**Running Locally:**
```bash
uvicorn app:app --host=0.0.0.0 --port=5006 --reload
```

**Running with Docker:**
```bash
docker-compose up --build
```

Access `http://localhost:5006` in your browser to see the dashboard!

## 🤖 Supported Devices
Ready to use with the following devices:
- **NVIDIA Jetson** (For AI robots, etc.)
- **Raspberry Pi 4** (For sensors and cameras)
- **Raspberry Pi Pico W** (For small projects)

## 📄 License
This project is licensed under the **MIT License**. Feel free to modify and play with it!
See [LICENSE.md](LICENSE.md) for details.
