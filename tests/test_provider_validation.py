import os
from unittest.mock import patch

from model_selection import apply_model_selection, update_override
from iot_agent.llm import UnifiedClient


def test_openai_base_url_normalizes_v1_suffix():
    env = {"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": "https://api.openai.com"}
    with patch.dict(os.environ, env):
        update_override({"provider": "openai", "model": "gpt-5.1"})
        provider, _, base_url, _ = apply_model_selection("iot")
        assert provider == "openai"
        assert base_url == "https://api.openai.com/v1"


def test_provider_lowercase_normalization():
    env = {"OPENAI_API_KEY": "sk-test"}
    with patch.dict(os.environ, env):
        update_override({"provider": "OpenAI", "model": "gpt-5.1"})
        provider, _, _, _ = apply_model_selection("iot")
        assert provider == "openai"


def test_openai_admin_key_rejected():
    env = {"OPENAI_API_KEY": "sk-admin-test"}
    with patch.dict(os.environ, env):
        update_override({"provider": "openai", "model": "gpt-5.1"})
        client = UnifiedClient()
        assert client.init_error is not None
        assert "admin" in str(client.init_error).lower()
