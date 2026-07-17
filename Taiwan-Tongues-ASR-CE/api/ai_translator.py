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

    # 一定要設 timeout：實測過沒設的話，Gemini 配額用盡時偶爾會讓連線掛住不回應，
    # 而不是快速回 429 錯誤，導致這個呼叫卡住不放，把整個單執行緒的 main.py 服務
    # 一起卡死，後面所有請求都連不進來。20 秒逾時後會拋例外，讓下面的重試/
    # fallback 機制接手，不會再拖垮整個服務。
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20000))
    
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


def polish_trend_summary(findings: list[str], urgent_findings: list[str], max_retries: int = 2):
    """
    把 trend_analysis.py 算好的純數字描述，潤飾成給家屬看的排版格式，並額外
    附上一段「AI 觀察」。

    判斷（有沒有變化、算不算異常）永遠由規則邏輯算完才傳進來，AI 在這裡拿到的
    已經是「法官判決書」，只負責排版（⚠️需留意/📊近期變化）跟補一句非醫療性質
    的關懷提醒（💭AI觀察）——AI 觀察刻意限制只能建議「多陪伴、留意作息」這類
    照護行動，不能講病因、不能斷言原因，避免真的變成醫療判斷。

    呼叫頻率是一天一次（趨勢查詢時才叫），不是每次語音互動都叫，額度成本低。
    失敗就回傳 None，呼叫端要 fallback 顯示原始的 findings 清單，不能讓這步的
    失敗擋住整個功能——核心判斷結果永遠不依賴這個函式是否成功。
    """
    if not findings and not urgent_findings:
        return None

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20000))

    system_prompt = """
    你是長照系統的家屬摘要助手。你會收到一組「已經計算好的數字變化描述」，
    工作分兩部分：(1) 把這些句子精簡整理成有排版、好讀的格式；(2) 額外加一句
    「AI 觀察」給家屬參考。

    嚴格規則：
    1. 前兩段（⚠️需留意、📊近期變化）只能重新表達給你的句子內容，不能新增任何
       數字、判斷、或這些句子沒有提到的資訊。
    2. 全文絕對不能出現診斷用語（例如「疑似」「患有」）或醫療建議/醫囑用語
       （例如「建議就醫」「請立即送醫」「應該吃藥」）。
    3. 「AI 觀察」這段的定位是「提醒家屬可以留意的照護面向」，不是「猜測醫療
       原因」——只能建議非醫療性質的關懷行動（例如多陪伴聊聊、留意作息/環境
       有無變化、觀察情緒狀態），絕對不能講「這可能是因為...(病因/病名)」這種
       因果推測，也不能提到任何身體器官、疾病名稱、藥物。用「可以留意」
       「不妨關心」這類語氣，不要用肯定句斷言原因。
    4. 輸出格式固定如下（沒有對應內容的段落整段省略，不要留空段落）：

    ⚠️ 需留意
    [每一項 urgent 內容精簡成一行，10-25字，一行一件事]

    📊 近期變化
    [每一項一般 findings 精簡成一行，10-20字，一行一件事，去掉重複的贅字]

    💭 AI 觀察（僅供參考，非醫療判斷）
    [一句話，20-40字，給家屬的非醫療性關懷提醒，語氣委婉、不斷言原因]

    5. 每一行都要精簡，不要把好幾件事塞進同一行，也不要每個數字都照抄，抓重點
       講完就好。不要加上面固定格式以外的其他文字（不要開場白、不要結語）。
    """

    parts = []
    if urgent_findings:
        parts.append("urgent: " + " ".join(urgent_findings))
    if findings:
        parts.append("一般: " + " ".join(findings))
    user_content = "\n".join(parts)

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[system_prompt, user_content],
                config=types.GenerateContentConfig(temperature=0.2),
            )
            return response.text.strip()
        except Exception as e:
            print(f"⚠️ [趨勢摘要潤飾] 第 {attempt + 1} 次嘗試失敗: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                print("⚠️ [趨勢摘要潤飾失敗，改顯示原始數字描述]")
                return None