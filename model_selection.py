"""Load shared model selection from Multi-Agent-Platform/model_settings.json for IoT Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_SELECTION = {"provider": "gemini", "model": "gemini-2.0-flash-001"}

AVAILABLE_MODELS: List[Dict[str, str]] = [
    # OpenAI
    {"provider": "openai", "model": "gpt-4o", "label": "GPT-4o"},
    {"provider": "openai", "model": "o1-mini", "label": "o1 Mini (Fast/Reasoning)"},
    
    # Gemini
    {"provider": "gemini", "model": "gemini-2.0-flash-001", "label": "Gemini 2.0 Flash (New)"},
    {"provider": "gemini", "model": "gemini-1.5-pro-002", "label": "Gemini 1.5 Pro-002 (Stable)"},

    # Claude
    {"provider": "claude", "model": "claude-3-5-sonnet-latest", "label": "Claude 3.5 Sonnet"},
    {"provider": "claude", "model": "claude-3-5-haiku-latest", "label": "Claude 3.5 Haiku"},

    # Groq
    {"provider": "groq", "model": "llama-3.1-70b-versatile", "label": "Llama 3.1 70B (Groq)"},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Llama 3.1 8B (Groq)"},
]

PROVIDER_DEFAULTS: Dict[str, Dict[str, str | List[str] | None]] = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "api_key_aliases": [],
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_env_aliases": [],
        "default_base_url": None,
    },
    "claude": {
        "api_key_env": "CLAUDE_API_KEY",
        "api_key_aliases": ["ANTHROPIC_API_KEY"],
        "base_url_env": "CLAUDE_API_BASE",
        "base_url_env_aliases": [],
        # Using OpenRouter as default for Claude if using OpenAI SDK
        "default_base_url": "https://openrouter.ai/api/v1",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "api_key_aliases": ["GOOGLE_API_KEY", "PALM_API_KEY"],
        "base_url_env": "GEMINI_API_BASE",
        "base_url_env_aliases": [],
        # Correct Google OpenAI-compatible endpoint
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
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

_OVERRIDE_SELECTION: Dict[str, str] | None = None


def _coerce_selection(raw: Dict[str, str] | None) -> Dict[str, str]:
    """Normalise provider/model fields and fall back to defaults."""

    provider = DEFAULT_SELECTION["provider"]
    model = DEFAULT_SELECTION["model"]

    if isinstance(raw, dict):
        raw_provider = raw.get("provider")
        raw_model = raw.get("model")
        if isinstance(raw_provider, str) and raw_provider.strip():
            provider = raw_provider.strip()
        if isinstance(raw_model, str) and raw_model.strip():
            model = raw_model.strip()

    return {"provider": provider, "model": model}


def _load_selection(agent_key: str) -> Dict[str, str]:
    platform_path = Path(__file__).resolve().parent.parent / "Multi-Agent-Platform" / "model_settings.json"
    try:
        data = json.loads(platform_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_SELECTION)

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
            return value

    fallback = os.getenv("OPENAI_API_KEY")
    return fallback or ""


def _resolve_base_url(meta: Dict[str, str | List[str] | None]) -> str:
    """Resolve base URL override for non-OpenAI providers."""

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
            return value

    default_base = meta.get("default_base_url")
    if isinstance(default_base, str):
        return default_base

    return ""


def apply_model_selection(agent_key: str = "iot", override: Dict[str, str] | None = None) -> Tuple[str, str, str]:
    selection = _coerce_selection(override or _OVERRIDE_SELECTION or _load_selection(agent_key))
    provider = selection["provider"]
    model = selection["model"]

    meta = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
    api_key = _resolve_api_key(meta)
    base_url = _resolve_base_url(meta)

    # Apply to environment for OpenAI SDK to pick up automatically
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    existing_base = os.getenv("OPENAI_BASE_URL", "")
    resolved_base = base_url or existing_base
    if resolved_base:
        os.environ["OPENAI_BASE_URL"] = resolved_base

    return provider, model, resolved_base


def update_override(selection: Dict[str, str] | None) -> Tuple[str, str, str]:
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
