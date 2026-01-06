# Repository Guidelines

## Project Structure & Module Organization
- `app.py` — FastAPI backend orchestrating chat-driven device jobs.
- `app.js`, `index.html`, `styles.css`, `login.html` — dashboard UI and auth screens served as static assets.
- `edge_device_code/` — hardware-specific client references; note `jetson/`, `raspberrypi4/`, and `raspberrypi-pico/` with `device_test/` utilities.
- `Dockerfile` and `docker-compose.yml` define container targets; `requirements.txt` tracks server dependencies.

## Build, Test, and Development Commands
- `python3.11 -m venv .venv && . .venv/bin/activate` creates the tested runtime locally.
- `pip install -r requirements.txt` installs FastAPI, dotenv, and the OpenAI SDK.
- `uvicorn app:app --host=0.0.0.0 --port=5006 --reload` starts the reloadable dev server.
- `docker-compose up --build` mirrors the dev setup with hot reload inside a container.
- `docker build -t iot-agent . && docker run --rm -p 5006:5006 --env-file secrets.env iot-agent` packages and runs the Gunicorn image.

## Coding Style & Naming Conventions
- Python follows PEP 8 with 4-space indents, dataclasses for state, and explicit type hints; keep module-level constants in screaming snake case (e.g., `DEVICE_RESULT_TIMEOUT`).
- JavaScript in `app.js` uses `const`/`let`, arrow utilities, and 2-space indents; prefer descriptive functions such as `formatRelativeTime`.
- Align HTML ids with JS selectors (`#deviceGrid`, `#registerNotice`) and avoid inline styling.
- Configuration secrets belong in `secrets.env`; do not persist environment-specific values in source.

## Testing Guidelines
- pytest modules live under `tests/` and cover basic FastAPI routing; extend them when changing the server.
- Hardware checks live in `edge_device_code/*/device_test/`; run individually (e.g., `python edge_device_code/raspberrypi4/device_test/camera_test.py`) on the matching board.
- Document manual validation steps in PRs when features touch live hardware or third-party APIs.

## Commit & Pull Request Guidelines
- Follow the existing concise summaries (e.g., "Add Raspberry Pi device test actions"); use imperative voice, ≤72 characters, Japanese or English acceptable.
- Squash or amend before review to keep history tidy; avoid merge commits in feature branches.
- PRs should link issues, describe user-facing impact, include test evidence, and highlight hardware dependencies or new environment variables.
- Request early review for schema or API shifts so edge client owners can adapt.

## Security & Configuration Tips
- Update `APP_PASSWORD` and other secrets before deploying; store overrides in `secrets.env` or platform secret managers.
- Rotate OpenAI credentials regularly and document fallbacks in `app.py` when adding new providers.
- Validate device capability schemas via `_normalise_capabilities` before exposing new agent actions.

## Critical Instruction / 重要指示
どれだけ時間とコストをかけてもよいので、確実に目標が達成されるようにしてほしい。
