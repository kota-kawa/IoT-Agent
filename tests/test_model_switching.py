
import os
import sys
import pytest
from unittest.mock import patch

# Adjust path to import from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from model_selection import (
    update_override,
    apply_model_selection,
    AVAILABLE_MODELS,
    PROVIDER_DEFAULTS
)
from iot_agent.llm import UnifiedClient

def _mock_env_for_all_providers():
    return {
        "OPENAI_API_KEY": "sk-mock-openai",
        "GEMINI_API_KEY": "AI-mock-gemini",
        "CLAUDE_API_KEY": "sk-ant-mock-claude",
        "GROQ_API_KEY": "gsk-mock-groq",
        "OPENAI_BASE_URL": "",
        "GEMINI_API_BASE": "",
        "CLAUDE_API_BASE": "",
        "GROQ_API_BASE": "",
    }

def test_all_models_switching():
    """
    Iterate through EVERY available model and verify:
    1. Provider is correct
    2. Model name is correct
    3. Base URL is correct (default or overridden)
    4. API Key is resolved from the correct environment variable
    5. Switching from Groq/Gemini back to OpenAI cleans up the Base URL
    """
    
    mock_env = _mock_env_for_all_providers()

    with patch.dict(os.environ, mock_env):
        # 1. First pass: Test each model individually from a clean state
        print("\n--- Phase 1: Individual Model Verification ---")
        for entry in AVAILABLE_MODELS:
            provider = entry["provider"]
            model_label = entry["label"]
            model_id = entry["model"]
            
            print(f"Testing {model_label} ({provider} : {model_id})...")
            
            # Select the model
            update_override({"provider": provider, "model": model_id})
            
            # Verify selection
            p, m, url, key = apply_model_selection("iot")
            
            assert p == provider
            assert m == model_id
            
            # Verify API Key
            if provider == "openai":
                assert key == "sk-mock-openai"
                assert url == "https://api.openai.com/v1"  # Default OpenAI base URL
            elif provider == "gemini":
                assert key == "AI-mock-gemini"
                expected_url = "https://generativelanguage.googleapis.com/v1beta/openai"
                assert url == expected_url
            elif provider == "claude":
                assert key == "sk-ant-mock-claude"
                assert url is None # Native Anthropic SDK
            elif provider == "groq":
                assert key == "gsk-mock-groq"
                expected_url = "https://api.groq.com/openai/v1"
                assert url == expected_url

        # 2. Second pass: Test switching FROM a custom base URL provider TO OpenAI
        # This catches the "stale URL" bug
        print("\n--- Phase 2: Stale URL Cleanup Verification ---")
        
        # Define a "poisoned" state provider (Groq) and a "clean" target (OpenAI)
        groq_model = next(m for m in AVAILABLE_MODELS if m["provider"] == "groq")
        openai_model = next(m for m in AVAILABLE_MODELS if m["provider"] == "openai")
        
        # Switch to Groq
        update_override({"provider": groq_model["provider"], "model": groq_model["model"]})
        _, _, url_groq, _ = apply_model_selection("iot")
        assert url_groq == "https://api.groq.com/openai/v1"
        
        # Switch to OpenAI (simulating frontend sending the STALE url in the request)
        # The frontend might just send the new provider/model but keep the old base_url if not careful,
        # OR the backend might retain it if we aren't careful.
        # Let's simulate the worst case: The input explicitly requests OpenAI but *with* the old Groq URL.
        
        stale_input = {
            "provider": openai_model["provider"],
            "model": openai_model["model"],
            "base_url": "https://api.groq.com/openai/v1" # <--- The poison
        }
        
        update_override(stale_input)
        p, m, url_clean, k = apply_model_selection("iot")
        
        print(f"Switching from Groq to OpenAI with stale URL input...")
        assert p == "openai"
        assert k == "sk-mock-openai"
        
        # CRITICAL CHECK: The URL must be None (default) or cleaned, NOT Groq's
        if url_clean == "https://api.groq.com/openai/v1":
            pytest.fail("Failed to clean up stale Groq Base URL when switching to OpenAI!")
        
        assert url_clean == "https://api.openai.com/v1", f"Expected OpenAI base_url, got {url_clean}"
        print("PASS: Stale URL was correctly stripped.")

        # 3. Third pass: Gemini to OpenAI
        gemini_model = next(m for m in AVAILABLE_MODELS if m["provider"] == "gemini")
        
        # Switch to Gemini
        update_override({"provider": gemini_model["provider"], "model": gemini_model["model"]})
        _, _, url_gemini, _ = apply_model_selection("iot")
        assert url_gemini == "https://generativelanguage.googleapis.com/v1beta/openai"

        # Switch to OpenAI with stale Gemini URL
        stale_input_gemini = {
            "provider": openai_model["provider"],
            "model": openai_model["model"],
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"
        }
        update_override(stale_input_gemini)
        p, m, url_clean_2, k = apply_model_selection("iot")
        
        print(f"Switching from Gemini to OpenAI with stale URL input...")
        assert url_clean_2 == "https://api.openai.com/v1", f"Expected OpenAI base_url, got {url_clean_2}"
        print("PASS: Stale Gemini URL was correctly stripped.")

if __name__ == "__main__":
    # Manually run if executed as script
    try:
        test_all_models_switching()
        print("\nAll tests passed successfully!")
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nTEST ERROR: {e}")
        sys.exit(1)
