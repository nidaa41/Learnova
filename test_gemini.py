import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# 1. List every "flash" model this key can use
print("Flash models available to your key:\n")
flash_models = []
for m in client.models.list():
    name = m.name.replace("models/", "")
    if "flash" in name and "tts" not in name and "live" not in name and "image" not in name:
        flash_models.append(name)
        print("  ", name)

# 2. Quickly test each one
print("\nTesting each model (a few seconds each)...\n")
for name in flash_models:
    try:
        reply = client.models.generate_content(model=name, contents="Reply with just: OK")
        print(f"  WORKS    {name}  ->  {reply.text.strip()[:20]}")
    except Exception as e:
        code = getattr(e, "code", "")
        print(f"  FAILED   {name}  ->  {code} {str(e)[:60]}")