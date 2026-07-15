import os
from google import genai
from dotenv import load_dotenv
from pathlib import Path

# 讀取你的 .env 檔案
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

# 建立連線
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

print("🔍 正在查詢你的金鑰支援的模型清單...\n")

# 列出所有可用的模型
try:
    for model in client.models.list():
        print(f"- {model.name}")
except Exception as e:
    print(f"查詢失敗: {e}")