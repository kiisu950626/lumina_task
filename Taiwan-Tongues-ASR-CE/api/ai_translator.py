import os
import json
import time
from pydantic import BaseModel
from typing import Literal
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pathlib import Path

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

# 1. 契約更新：正式改為印尼文 (indonesian_text)
class CareLuminaContract(BaseModel):
    original_text: str
    normalized_chinese: str
    indonesian_text: str
    event_type: Literal["abdominal_pain", "dizziness", "chest_tightness", "pain", "refuse_medication", "fall", "help", "general_need", "unknown"]
    severity: Literal["low", "medium", "high"]
    notify_family: bool
    confidence: float

# 2. 加入 max_retries 防護罩
def get_ai_analysis(text: str, max_retries: int = 3):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ 錯誤：無法讀取 GEMINI_API_KEY")
        return None

    client = genai.Client(api_key=api_key)
    
    # 3. 提示詞更新：拿掉台語範例，改為翻譯印尼文
    system_prompt = """
    你是一個專業的台灣長照雙向溝通 AI。
    你會收到長輩的日常對話（包含極度口語的台語與客語與中文與英文直譯或語音辨識 ASR 錯字）。
    請執行以下任務：
    1. 準確理解並將文字正規化為標準中文。
    2. 將標準中文精準翻譯為日常口語的印尼文 (Indonesian)。
    3. 嚴格對事件進行分類，並評估嚴重程度。若有立即危險（如跌倒、胸悶），severity 設為 high。
    """

    print(f"🧠 AI 正在分析：{text}")
    
    # 4. 自動重試機制 (解決 503 錯誤)
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[system_prompt, f"長者說：{text}"],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CareLuminaContract,
                    temperature=0.1 
                ),
            )
            return json.loads(response.text)
            
        except Exception as e:
            print(f"⚠️ 第 {attempt + 1} 次嘗試失敗: {e}")
            if attempt < max_retries - 1:
                print("⏳ 伺服器忙碌中，2秒後自動重試...\n")
                time.sleep(2)
            else:
                print("❌ 已達最大重試次數，請稍後再試。")
                return None