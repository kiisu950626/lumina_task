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


def polish_trend_summary(
    red_findings: list[str], orange_findings: list[str], yellow_findings: list[str],
    max_retries: int = 2,
):
    """
    把 trend_analysis.py 算好的三級分類（紅/橘/黃）純數字描述，潤飾成給家屬看
    的排版格式，並額外附上一段「AI 觀察」。

    判斷（有沒有變化、算不算異常、屬於哪一級）永遠由規則邏輯算完才傳進來，AI
    在這裡拿到的已經是「法官判決書」，只負責排版跟補一句非醫療性質的關懷提醒
    （💭AI觀察）——AI 觀察刻意限制只能建議「多陪伴、留意作息」這類照護行動，
    不能講病因、不能斷言原因，避免真的變成醫療判斷。紅/橘段落裡如果提到「撥打
    119」「聯絡醫療人員」，那是規則邏輯傳進來的公開緊急處置指引原文，AI 只能
    照轉述，不能自己新增或加重語氣。

    呼叫頻率是一天一次（趨勢查詢時才叫），不是每次語音互動都叫，額度成本低。
    失敗就回傳 None，呼叫端要 fallback 顯示原始的三個陣列，不能讓這步的失敗
    擋住整個功能——核心判斷結果永遠不依賴這個函式是否成功。
    """
    if not red_findings and not orange_findings and not yellow_findings:
        return None

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20000))

    system_prompt = """
    你是長照系統的家屬摘要助手。你會收到一組「已經計算好、已經分好紅/橘/黃
    三個等級」的數字變化描述，工作分兩部分：(1) 把這些句子精簡整理成有排版、
    好讀的格式；(2) 額外加一句「AI 觀察」給家屬參考。

    嚴格規則：
    1. 🔴🟠🟡 三段只能重新表達給你的句子內容，不能新增任何數字、判斷、或這些
       句子沒有提到的資訊。如果句子裡本來就有「撥打119」「聯絡醫療人員」這類
       字眼，可以照樣保留（那是規則邏輯傳進來的公開指引原文），但不能自己
       多加其他醫囑用語。
    2. 除了規則邏輯原文裡已經有的指引字句外，不能再自己新增診斷用語
       （例如「疑似」「患有」）或醫療建議/醫囑用語。
    3. 「AI 觀察」這段的定位是「提醒家屬可以留意的照護面向」，不是「猜測醫療
       原因」——只能建議非醫療性質的關懷行動（例如多陪伴聊聊、留意作息/環境
       有無變化、觀察情緒狀態），絕對不能講「這可能是因為...(病因/病名)」這種
       因果推測，也不能提到任何身體器官、疾病名稱、藥物。用「可以留意」
       「不妨關心」這類語氣，不要用肯定句斷言原因。
    4. 輸出格式固定如下（沒有對應內容的段落整段省略，不要留空段落）：

    🔴 立即處理
    [每一項紅色內容精簡成一行，保留關鍵數字跟「撥打119」等指引]

    🟠 建議聯絡醫療人員
    [每一項橘色內容精簡成一行，10-25字]

    🟡 需留意
    [每一項黃色內容精簡成一行，10-20字，去掉重複的贅字]

    💭 AI 觀察（僅供參考，非醫療判斷）
    [一句話，20-40字，給家屬的非醫療性關懷提醒，語氣委婉、不斷言原因]

    5. 每一行都要精簡，不要把好幾件事塞進同一行，也不要每個數字都照抄，抓重點
       講完就好。不要加上面固定格式以外的其他文字（不要開場白、不要結語）。
    """

    parts = []
    if red_findings:
        parts.append("紅色: " + " ".join(red_findings))
    if orange_findings:
        parts.append("橘色: " + " ".join(orange_findings))
    if yellow_findings:
        parts.append("黃色: " + " ".join(yellow_findings))
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


def analyze_conversation_tone(recent_texts: list[str], max_retries: int = 2):
    """
    讀長者最近的原始逐字稿，找規則邏輯（單純算次數/平均值）抓不到的「質性」
    變化——例如語氣是否比較低落、是否常有抱怨/負面用詞、話題是否反覆圍繞
    同一件事。這是刻意跟紅/橘/黃分級判斷分開的獨立觀察層：

      - 分級判斷（紅/橘/黃）永遠是規則邏輯的權威輸出，這裡的結果不會、也不能
        拿去決定分級，只是額外附加的參考觀察。
      - 輸出只能是「觀察到的言談模式描述」，不能是「診斷」或「醫療判斷」——
        不能講病名、不能講「可能是憂鬱症/失智」這類臨床推測，只能描述觀察到
        的表面語言現象（例如「較常出現負面詞彙」），不能推論背後成因。
      - 沒有明顯模式時要老實說「沒有明顯變化」，不能為了「有東西可以顯示」
        硬找一個模式出來。

    呼叫頻率一天一次（趨勢查詢時才叫），額度成本低。失敗回傳 None，不影響
    分級判斷的正常運作。
    """
    if not recent_texts:
        return None

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=20000))

    system_prompt = """
    你是長照系統的言談模式觀察助手。你會收到長者最近一段時間、依時間排序的
    逐字稿列表（由新到舊）。

    嚴格規則：
    1. 只能描述你在這些逐字稿裡「實際觀察到的表面語言現象」，例如：是否常
       出現抱怨/負面詞彙、是否反覆圍繞同一件事、語氣是否比之前提到的內容
       更急促或低落。
    2. 絕對禁止：診斷用語（疑似/患有）、病名（憂鬱症/失智症/焦慮症等任何
       醫學名詞）、醫療建議（就醫/服藥/治療）、推論成因（不能講「這可能是
       因為身體不舒服/心情不好」這種因果猜測）。
    3. 只描述「有沒有觀察到什麼模式」，不要評價這個模式好不好、嚴不嚴重。
    4. 如果逐字稿內容平淡、沒有特別模式（多數情況都是如此），就直接說
       「近期言談內容與平常相近，無明顯特殊模式」，不要硬掰一個觀察出來。
    5. 輸出 1 句話，30-50 字，純文字，不要加任何格式符號或開場白。
    """

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(recent_texts))

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=[system_prompt, numbered],
                config=types.GenerateContentConfig(temperature=0.3),
            )
            return response.text.strip()
        except Exception as e:
            print(f"⚠️ [言談模式觀察] 第 {attempt + 1} 次嘗試失敗: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                print("⚠️ [言談模式觀察失敗，不影響分級判斷]")
                return None