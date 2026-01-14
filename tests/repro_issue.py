import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# Mock secrets.env
SECRETS_PATH = BASE_DIR / "secrets.env"
if not SECRETS_PATH.exists():
    with open(SECRETS_PATH, "w") as f:
        f.write("OPENAI_API_KEY=sk-test-openai\n")
        f.write("GROQ_API_KEY=gsk_test_groq\n")

# Import modules to test
from model_selection import apply_model_selection, update_override
from iot_agent.llm import _client, UnifiedClient

def test_switching():
    print("--- Test Start ---")
    
    # 1. Initial State (Default: Groq)
    print("1. Initial state (should be Groq)")
    provider, model, base_url, api_key = apply_model_selection("iot")
    print(f"   Provider: {provider}")
    print(f"   Key starts with: {api_key[:4] if api_key else 'None'}")
    
    client1 = _client()
    print(f"   Client1 provider: {client1.provider}")
    print(f"   Client1 key: {client1.api_key[:4] if client1.api_key else 'None'}")

    # 2. Switch to OpenAI
    print("\n2. Switching to OpenAI (GPT-5.1)")
    update_override({"provider": "openai", "model": "gpt-5.1"})
    
    provider, model, base_url, api_key = apply_model_selection("iot")
    print(f"   Provider: {provider}")
    print(f"   Key starts with: {api_key[:4] if api_key else 'None'}")
    
    client2 = _client()
    print(f"   Client2 provider: {client2.provider}")
    print(f"   Client2 key: {client2.api_key[:4] if client2.api_key else 'None'}")
    
    if client2.provider != "openai":
        print("!! FAILURE: Client provider did not update to OpenAI")
    if not client2.api_key.startswith("sk-"):
        print(f"!! FAILURE: Client key does not look like OpenAI key: {client2.api_key[:10]}")

    # 3. Switch back to Groq
    print("\n3. Switching back to Groq")
    update_override({"provider": "groq", "model": "openai/gpt-oss-20b"})
    
    provider, model, base_url, api_key = apply_model_selection("iot")
    print(f"   Provider: {provider}")
    print(f"   Key starts with: {api_key[:4] if api_key else 'None'}")
    
    client3 = _client()
    print(f"   Client3 provider: {client3.provider}")
    print(f"   Client3 key: {client3.api_key[:4] if client3.api_key else 'None'}")

    if client3.provider != "groq":
        print("!! FAILURE: Client provider did not update to Groq")
    if not client3.api_key.startswith("gsk_"):
        print(f"!! FAILURE: Client key does not look like Groq key: {client3.api_key[:10]}")

    # 4. Switch to OpenAI again
    print("\n4. Switching to OpenAI again")
    update_override({"provider": "openai", "model": "gpt-5.1"})
    
    client4 = _client()
    print(f"   Client4 provider: {client4.provider}")
    print(f"   Client4 key: {client4.api_key[:4] if client4.api_key else 'None'}")

    if not client4.api_key.startswith("sk-"):
         print(f"!! FAILURE: Client key does not look like OpenAI key: {client4.api_key[:10]}")

if __name__ == "__main__":
    test_switching()
