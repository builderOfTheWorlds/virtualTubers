#!/usr/bin/env python3
import requests
import json

OLLAMA_HOST = "http://192.168.1.23:11434"

# Test prompt - small sample
test_text = "Mr Dursley hummed as he picked out his most boring tie for work and Mrs Dursley gossiped away happily as she wrestled a screaming Dudley into his high chair.None of them noticed a large tawny owl flutter past the window."

prompt = f"""You are a careful text restorer, NOT a rewriter.

RULES:
- Restore quotation marks around dialogue.
- Restore paragraph breaks (double newlines) based on speaker changes, scene shifts.
- Add speaker attribution (he said, she whispered, etc.) where needed for clarity.
- Restore ambiguous contractions: were→we're, ill→I'll, its→it's (use context clues).
- Do NOT change wording, add detail, or fix typos.
- Return ONLY the restored text, no explanation.

Text to restore:
{test_text}"""

try:
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": "mistral",
            "prompt": prompt,
            "stream": False,
            "temperature": 0.1,
        },
        timeout=120
    )
    
    if response.status_code == 200:
        data = response.json()
        result = data.get('response', '').strip()
        print("=== INPUT ===")
        print(test_text)
        print("\n=== OUTPUT ===")
        print(result)
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"Failed to connect: {e}")
