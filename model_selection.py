"""Load shared model selection from Multi-Agent-Platform/model_settings.json for IoT Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv

# Load env immediately to ensure keys are available even if config.py hasn't run
load_dotenv(Path(__file__).resolve().parent / "secrets.env")

# Multi-Agent-Platform の設定モーダルと揃えるデフォルト
DEFAULT_SELECTION = {"provider": "groq", "model": "openai/gpt-oss-20b", "base_url": ""}

# OpenAI 公式ベースURL（/v1 まで含める）
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"

AVAILABLE_MODELS: List[Dict[str, str]] = [
    # OpenAI
    {"provider": "openai", "model": "gpt-5.1", "label": "GPT-5.1"},

    # Gemini (Google)
    {"provider": "gemini", "model": "gemini-3-pro-preview", "label": "Gemini 3 Pro Preview"},

    # Claude (Anthropic)
    {"provider": "claude", "model": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    {"provider": "claude", "model": "claude-opus-4-5", "label": "Claude Opus 4.5"},

    # Groq
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B (Groq)"},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Llama 3.1 8B (Groq)"},
    {"provider": "groq", "model": "openai/gpt-oss-20b", "label": "GPT-OSS 20B (Groq)"},
    {"provider": "groq", "model": "qwen/qwen3-32b", "label": "Qwen3 32B (Groq)"},
]

PROVIDER_DEFAULTS: Dict[str, Dict[str, str | List[str] | None]] = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "api_key_aliases": [],
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_env_aliases": [],
        "default_base_url": OPENAI_DEFAULT_BASE_URL,  # 公式ベースURL
    },
    "claude": {
        "api_key_env": "CLAUDE_API_KEY",
        "api_key_aliases": ["ANTHROPIC_API_KEY"],
        "base_url_env": "CLAUDE_API_BASE",
        "base_url_env_aliases": [],
        # Native Anthropic API
        "default_base_url": None,
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "api_key_aliases": ["GOOGLE_API_KEY", "PALM_API_KEY"],
        "base_url_env": "GEMINI_API_BASE",
        "base_url_env_aliases": [],
        # Google の OpenAI 互換エンドポイント（Multi-Agent-Platform と共通）
        # 公式ドキュメント: https://generativelanguage.googleapis.com/v1beta/openai/
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "groq": {
        "api_key_env": "GROQ_API_KEY",
        "api_key_aliases": [],
        "base_url_env": "GROQ_API_BASE",
        "base_url_env_aliases": [],
        "default_base_url": "https://api.groq.com/openai/v1",
    },
}
VISION_SUPPORTED_PROVIDERS = {"openai", "claude", "gemini"}

_OVERRIDE_SELECTION: Dict[str, str | None] | None = None


def _coerce_selection(raw: Dict[str, str] | None) -> Dict[str, str | None]:
    """Normalise provider/model/base_url fields and fall back to defaults."""

    provider = DEFAULT_SELECTION["provider"]
    model = DEFAULT_SELECTION["model"]
    base_url: str | None = None

    if isinstance(raw, dict):
        raw_provider = raw.get("provider")
        raw_model = raw.get("model")
        raw_base_url = raw.get("base_url")
        if isinstance(raw_provider, str) and raw_provider.strip():
            provider = raw_provider.strip().lower()
        if isinstance(raw_model, str) and raw_model.strip():
            model = raw_model.strip()
        if isinstance(raw_base_url, str) and raw_base_url.strip():
            base_url = raw_base_url.strip()

    return {"provider": provider, "model": model, "base_url": base_url}


def _load_selection(agent_key: str) -> Dict[str, str | None]:
    env_path = os.getenv("MULTI_AGENT_SETTINGS_PATH")
    
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    
    # Try relative path to platform (standard dev structure)
    candidates.append(Path(__file__).resolve().parent.parent / "Multi-Agent-Platform" / "model_settings.json")
    # Try local fallback (e.g. Docker container or standalone)
    candidates.append(Path(__file__).resolve().parent / "model_settings.json")

    data = {}
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
    
    # If no file found, data is empty, will fall back to DEFAULT_SELECTION below

    selection = data.get("selection") or data
    chosen = selection.get(agent_key) if isinstance(selection, dict) else None
    if not isinstance(chosen, dict):
        return dict(DEFAULT_SELECTION)

    return _coerce_selection(chosen)


def _resolve_api_key(meta: Dict[str, str | List[str] | None]) -> str:
    """Resolve provider-specific API key without exposing the value."""

    candidates = []
    primary = meta.get("api_key_env")
    aliases = meta.get("api_key_aliases") or []
    if isinstance(primary, str):
        candidates.append(primary)
        candidates.append(primary.lower())
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str):
                candidates.append(alias)
                candidates.append(alias.lower())

    for env_name in candidates:
        value = os.getenv(env_name)
        if value:
            return value.strip()

    return ""


def _normalise_base_url(provider: str, base_url: str | None, meta: Dict[str, str | List[str] | None]) -> str | None:
    """Clamp base_url to the expected host/path for each provider to avoid stale values."""

    default_base = meta.get("default_base_url")
    if not isinstance(default_base, str):
        default_base = None

    cleaned = (base_url or "").strip()
    if provider == "gemini":
        target = cleaned or default_base or ""
        if not target:
            return None
        lower = target.lower()
        if "generativelanguage.googleapis.com" in lower and "/openai" not in lower:
            target = target.rstrip("/") + "/openai"
        elif "groq" in lower and default_base:
            # Selection leaked from another provider; snap back to Gemini default
            target = default_base
        return target.rstrip("/")

    if provider == "groq":
        target = cleaned or default_base or ""
        if target and "groq" not in target.lower():
            target = default_base or target
        return target.rstrip("/") if target else None

    if provider == "openai":
        target = cleaned or default_base or ""
        if not target:
            return None
        lowered = target.lower()
        # Check for known other providers FIRST to filter them out
        if any(key in lowered for key in ("groq", "generativelanguage.googleapis.com")):
            # Drop stale base URLs pointing to other providers to avoid auth mismatch
            return default_base
        if "api.openai.com" in lowered and "/v1" not in lowered:
            target = target.rstrip("/") + "/v1"
            lowered = target.lower()
        # Then allow valid OpenAI-compatible hosts
        if any(key in lowered for key in ("openai", ".azure.com", "localhost", "127.0.0.1")):
            return target.rstrip("/")
        return target.rstrip("/")

    # Claude and other providers keep the explicit override or default as-is
    target = cleaned or default_base or ""
    return target.rstrip("/") if target else None


def _resolve_base_url(meta: Dict[str, str | List[str] | None]) -> str | None:
    """Resolve base URL override for non-OpenAI providers. Returns None if using default."""

    env_names: List[str] = []
    base_env = meta.get("base_url_env")
    aliases = meta.get("base_url_env_aliases") or []
    if isinstance(base_env, str):
        env_names.append(base_env)
        env_names.append(base_env.lower())
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str):
                env_names.append(alias)
                env_names.append(alias.lower())

    for env_name in env_names:
        value = os.getenv(env_name)
        if value:
            return value.strip()

    default_base = meta.get("default_base_url")
    if isinstance(default_base, str):
        return default_base

    return None


def apply_model_selection(agent_key: str = "iot", override: Dict[str, str] | None = None) -> Tuple[str, str, str | None, str]:
    selection = _coerce_selection(override or _OVERRIDE_SELECTION or _load_selection(agent_key))
    provider = selection["provider"]
    model = selection["model"]

    meta = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
    base_candidate = selection.get("base_url") or _resolve_base_url(meta)
    base_url = _normalise_base_url(provider, base_candidate, meta)
    api_key = _resolve_api_key(meta)

    # Note: We do NOT modify os.environ here to avoid polluting the global state
    # or overwriting the user's original OPENAI_API_KEY.
    # The caller is responsible for passing api_key and base_url to the client.

    return provider, model, base_url, api_key


def update_override(selection: Dict[str, str] | None) -> Tuple[str, str, str | None, str]:
    """Set in-memory override and return applied config."""

    global _OVERRIDE_SELECTION
    _OVERRIDE_SELECTION = _coerce_selection(selection) if selection else None
    return apply_model_selection(override=_OVERRIDE_SELECTION or None)


def provider_supports_vision(provider: str) -> bool:
    """Return True when the selected provider allows multimodal/vision prompts."""

    if not isinstance(provider, str):
        return False
    return provider.strip().lower() in VISION_SUPPORTED_PROVIDERS


def current_available_models() -> List[Dict[str, str]]:
    """Expose available models list for the frontend."""

    return [dict(item) for item in AVAILABLE_MODELS]
