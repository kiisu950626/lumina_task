from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import torch
from transformers import pipeline, M2M100ForConditionalGeneration, M2M100Tokenizer
import os
import shutil
import sys
import sqlite3
from datetime import datetime, timezone
import librosa
from langdetect import detect, DetectorFactory
import os
import json

# 取得 main.py 所在的當前目錄 (C:\VoiceTranslateApp)
base_dir = os.path.dirname(os.path.abspath(__file__))

# 加上 Taiwan-Tongues-ASR-CE 這一層，完整對齊你的資料夾結構
json_path = os.path.join(base_dir, "Taiwan-Tongues-ASR-CE", "api", "keywords.json")
care_intents_path = os.path.join(base_dir, "Taiwan-Tongues-ASR-CE", "api", "care_intents.json")

with open(json_path, "r", encoding="utf-8") as f:
    KEYWORDS = json.load(f)
with open(care_intents_path, "r", encoding="utf-8") as f:
    CARE_INTENTS = json.load(f)
DetectorFactory.seed = 0 # 確保偵測穩定

# 掛上 api/ai_translator.py（Gemini 空耳/語意正規化模組）。
# ASR 對台語/客語辨識失敗時常吐出「音對字不對」的空耳亂碼（例如把「膝蓋痛」
# 辨識成「咖逃呼很痛」），若不修正就直接丟給翻譯模型，翻出來的東西也會跟著錯。
# ai_translator.py 用 Gemini 把這種亂碼先正規化成語意正確的標準中文再繼續。
sys.path.append(os.path.join(base_dir, "Taiwan-Tongues-ASR-CE", "api"))
from ai_translator import get_ai_analysis, polish_trend_summary, analyze_conversation_tone

if not os.getenv("GEMINI_API_KEY"):
    print("⚠️ [警告] 未偵測到 GEMINI_API_KEY（應設定於 Taiwan-Tongues-ASR-CE/api/.env），"
          "AI 空耳正規化步驟會被跳過，直接使用原始 ASR 文字。")

# --- 文字紀錄（SQLite）---
# 之前每次語音處理完就直接回傳、不留痕跡，事後沒辦法查「長者今天說了什麼」。
# 用 SQLite 是跟著這個專案其他地方的做法走（api/file_asr.py、auth_api.py 都是
# 用 SQLite 存任務/帳號資料），不用另外架資料庫服務。
RECORDS_DB_PATH = os.path.join(base_dir, "care_records.db")


def _ensure_records_schema():
    os.makedirs(os.path.dirname(RECORDS_DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(RECORDS_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS care_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                audio_filename TEXT,
                detected_lang TEXT,
                raw_text TEXT,
                normalized_text TEXT,
                matched_known_intent TEXT,
                predicted_intent TEXT,
                confidence REAL,
                event_type TEXT,
                severity_level TEXT,
                notify_family INTEGER,
                vietnamese_output TEXT,
                indonesian_output TEXT
            )
            """
        )
        conn.commit()


def save_care_record(**fields):
    # 存紀錄失敗不該讓整個語音處理流程掛掉（跟其他步驟一樣採「失敗就跳過」原則），
    # 只印警告，不拋例外。
    try:
        with sqlite3.connect(RECORDS_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO care_records (
                    created_at, audio_filename, detected_lang, raw_text, normalized_text,
                    matched_known_intent, predicted_intent, confidence, event_type,
                    severity_level, notify_family, vietnamese_output, indonesian_output
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    fields.get("audio_filename"),
                    fields.get("detected_lang"),
                    fields.get("raw_text"),
                    fields.get("normalized_text"),
                    fields.get("matched_known_intent"),
                    fields.get("predicted_intent"),
                    fields.get("confidence"),
                    fields.get("event_type"),
                    fields.get("severity_level"),
                    int(bool(fields.get("notify_family"))),
                    fields.get("vietnamese_output"),
                    fields.get("indonesian_output"),
                ),
            )
            conn.commit()
    except Exception as e:
        print(f"⚠️ [紀錄寫入失敗，不影響本次回應]: {e}")


_ensure_records_schema()

# --- Postgres 正式資料庫（隊友負責的 lumina_DB/database）---
# 跟 SQLite 並存，不取代：Postgres 連不上時（例如本機沒開 Docker）整個服務
# 還是要能正常運作，只是這筆事件不會被存進 Postgres，SQLite 那份紀錄不受影響。
#
# 注意：lumina_DB/database/relational/queries.py 在「被 import 的當下」就會建立
# 資料庫連線池（ThreadedConnectionPool 是模組層級變數，不是等到真的查詢才連），
# 所以這裡的 import 也要包在 try/except 裡，不然 Postgres 沒開時 import 這一行
# 本身就會把整個 main.py 啟動炸掉。
LUMINA_DB_PATH = os.path.join(base_dir, "lumina_DB", "database")
DB_AVAILABLE = False
if os.path.isdir(LUMINA_DB_PATH):
    sys.path.append(LUMINA_DB_PATH)
    try:
        from relational.queries import execute_create_event, get_db_connection
        DB_AVAILABLE = True
        print("✅ [Postgres] 連線池建立成功，語音事件會同步寫入正式資料庫。")
    except Exception as e:
        print(f"⚠️ [警告] Postgres 連不上（{e}），本次執行僅寫入本地 SQLite，不影響其他功能。")
else:
    print("⚠️ [警告] 找不到 lumina_DB/database，僅寫入本地 SQLite。")

# 模組五：趨勢判斷（純規則統計，不叫 AI，理由見 trend_analysis.py 檔頭說明）
sys.path.append(os.path.join(base_dir, "Taiwan-Tongues-ASR-CE", "api"))
from trend_analysis import (
    generate_trend_summary, generate_daily_summary, save_daily_summary,
    get_recent_conversation_texts,
)

# 文字轉向量：改用本機模型，取代隊友原本 ai_client.py 呼叫 Gemini 的版本
# （見 local_embeddings.py 檔頭說明）。模型本身在第一次呼叫時才真的載入，
# 不拖慢服務啟動速度。
from local_embeddings import get_embedding

# 測試用預設值：來自隊友 lumina_DB/database/mock_data 的假資料（王阿公 / 看護阮氏秋）。
# 之後前端有登入系統、知道實際是哪個長者/哪個裝置在用，要把這兩個值換成真的
# elder_id / reporter_id，這裡只是先讓 API 在沒有登入系統的現在也能測試。
DEFAULT_ELDER_ID = "33333333-3333-3333-3333-333333333333"
DEFAULT_REPORTER_ID = "22222222-2222-2222-2222-222222222222"
DEFAULT_GROUP_ID = "44444444-4444-4444-4444-444444444444"  # 王阿公的照護群組（mock_data/care_groups.json）

# main.py 的方言判斷輸出是人看的完整說明（如「臺灣台語 (閩南語)」），但 Postgres
# 的 source_language 欄位限制 VARCHAR(10)，要轉成短代碼再寫入。
DIALECT_TO_LANG_CODE = {
    "現代標準漢語 (國語)": "zh-TW",
    "臺灣台語 (閩南語)": "nan",
    "臺灣客家語": "hak",
}

app = FastAPI(title="CuraGo 護家通 - 數發部規格多語偵測與真 AI 語意分析/翻譯完全體")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "./temp_audio"
os.makedirs(UPLOAD_DIR, exist_ok=True)

device = 0 if torch.cuda.is_available() else -1
device_str = "cuda" if torch.cuda.is_available() else "cpu"

print("==================================================")
print("🚀【🔥 競賽級核心啟動】正在載入全實體 ASR + NLU + NMT 翻譯大腦...")
print("==================================================")

print("[大腦 1/3] 正在載入 Breeze-ASR-26 台語 ASR 模型 (MediaTek Research)...")

# 換掉原本的 adi-gov-tw CTranslate2 模型：用官方標準測試集實測過，adi-gov-tw 在
# 播音員唸稿等「清晰語音」情境下平均 CER 64.5%，Breeze-ASR-26（10,000小時訓練資料，
# Apache 2.0）同條件下只有 37.0%，4 支測試音檔全部勝出，換模型有實測數據支撐。
# Breeze 只有 transformers/safetensors 格式（沒有 CTranslate2 版本），所以這裡改用
# transformers.pipeline，不能沿用 faster_whisper.WhisperModel。
asr_models_path = os.path.join(base_dir, "Taiwan-Tongues-ASR-CE", "breeze_asr_model")
asr_device_index = 0 if torch.cuda.is_available() else -1
asr_pipeline = pipeline(
    "automatic-speech-recognition",
    model=asr_models_path,
    device=asr_device_index,
)

print(f"✅ Breeze-ASR-26 模型載入成功！(device={'cuda' if asr_device_index == 0 else 'cpu'})")

# 🧠 大腦二：真．零樣本語意分類模型 (Zero-Shot)
# hfl/chinese-bert-wwm-ext 只是一般 MLM 預訓練模型，沒有 NLI (entailment) 頭；
# zero-shot-classification pipeline 需要 NLI 微調過的模型才能給出有意義的分數，
# 用一般 BERT 等同亂猜。改用支援中文的多語 NLI 模型。
print("[大腦 2/3] 載入自然語言語意分析 NLU 模型...")
classifier = pipeline(
    "zero-shot-classification",
    model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    device=device,
)

# 🧠 大腦三：真．AI 多語言動態翻譯模型 (Facebook M2M100)
# 從 418M 換成 1.2B：同系列、同樣 MIT 授權（可商用），實測 20 句自由句子
# 正確率從 20%/25%（越南文/印尼文）提升到 50%/40%，且已加上生成長度限制
# （見 ai_translate()）避免偶發的重複生成迴圈拖慢速度。
print("[大腦 3/3] 載入 Facebook M2M100-1.2B 實體動態翻譯模型 (支援中/印/越)...")
translation_model_name = os.path.join(base_dir, "Taiwan-Tongues-ASR-CE", "m2m100_1.2B")
tokenizer = M2M100Tokenizer.from_pretrained(translation_model_name)
translation_model = M2M100ForConditionalGeneration.from_pretrained(translation_model_name).to(device_str)

print("🎉 [系統完全就緒] 語音偵測、自由語意分析、真 AI 實體動態翻譯管線全數上線！")

AI_CARE_LABELS = [
    "日常生理需求（吃飯、喝水、上廁所、睡覺）", 
    "急性醫療警戒（不舒服、身體疼痛、頭暈、胸悶）", 
    "緊急意外傷害（跌倒、摔傷、撞到）", 
    "一般閒聊與日常對話"
]

# 輔助函式：已知照護句型比對（care_intents.json）
# 用意：ASR 對自然口語台語句子準確率不穩定，但幾個高頻、關鍵的照護意圖
# （上廁所、喝水、拒絕服藥等）如果能命中固定對照表，就不用再經過「AI 分類 +
# M2M100 自由翻譯」這條容易出錯的路，直接給保證正確的中文/越南文翻譯，
# 速度更快、結果更穩定。命中不到才 fallback 到原本的 AI 分析流程。
def match_known_care_intent(text: str):
    for intent_code, data in CARE_INTENTS.items():
        if any(phrase in text for phrase in data.get("match_phrases", [])):
            return intent_code, data
    return None, None


# 輔助函式：真正調用 Facebook M2M100 模型進行 AI 翻譯
# src_lang 開放可調（預設 zh，維持原本長者語音這條路徑的行為不變）：
# 原本這裡寫死 src_lang="zh"，不管實際餵進去什麼語言都當中文處理——多語言
# 翻譯正確率驗證時測過，餵英文文字進去雖然常常也能翻對（M2M100 分詞器對
# 語言標記錯誤有一定容錯度），但這是運氣好、不是設計保證，看護回覆長者這個
# 方向需要「越南文/印尼文→中文」，必須要能真的指定正確的來源語言。
def ai_translate(text: str, target_lang: str, src_lang: str = "zh"):
    try:
        tokenizer.src_lang = src_lang
        encoded = tokenizer(text, return_tensors="pt").to(device_str)
        # max_new_tokens 限制生成長度、num_beams=1 關掉 beam search：實測過
        # M2M100 在沒有限制的情況下，偶爾會對某些句子陷入重複生成迴圈，單次
        # 呼叫從正常的幾秒暴增到 300+ 秒（跟 Breeze ASR 之前踩過的同一種問題）。
        # 長者的照護短句通常很短，128 tokens 綽綽有餘。
        generated_tokens = translation_model.generate(
            **encoded,
            forced_bos_token_id=tokenizer.get_lang_id(target_lang),
            max_new_tokens=128,
            num_beams=1,
        )
        return tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)[0]
    except Exception as e:
        print(f"[翻譯出錯] {str(e)}")
        return "Translation Error"

@app.post("/api/voice/listen-elder")
async def listen_elder(
    file: UploadFile = File(...),
    elder_id: str = Form(DEFAULT_ELDER_ID),
    reporter_id: str = Form(DEFAULT_REPORTER_ID),
):
    # elder_id/reporter_id 先給預設值（隊友 mock_data 裡的假長者/假看護），方便在
    # 前端還沒接登入系統的現在也能測試；等前端知道實際是誰在用，改成必填、不給預設值。
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        print(f"\n[AI 核心] 收到長者實體語音: {file.filename}")
        
    try:
        # 1. 音訊解碼
        audio_data, _sample_rate = librosa.load(file_path, sr=16000, mono=True)

        # 2. 語音辨識（Breeze-ASR-26，transformers pipeline）
        # max_new_tokens 限制生成長度：不限制的話 2B 模型偶爾會陷入重複生成迴圈，
        # 一句話可能卡上數十分鐘（實測過）；num_beams=1 關閉 beam search 換取速度。
        #
        # 曾經試過改成 num_beams=5（4句清晰廣告實測錯字率 37.0%→31.3%），但後來用
        # 別的音檔測試時，num_beams=5 自己就觸發了重複生成迴圈（"小紅帽不看太陽的
        # 位置"重複12次、耗時2分47秒）——證實單獨開 num_beams=5 也不安全，不是只有
        # 加提示詞才會發生，4句樣本數太少沒測到。demo 穩定性優先，先退回 num_beams=1，
        # 之後要提升正確率，得先加 repetition_penalty/no_repeat_ngram_size 這類重複
        # 懲罰機制重新驗證過，確認不會卡死才能再開 num_beams=5。
        # return_timestamps=True 是保險：長者講話若剛好超過 30 秒，Whisper 架構
        # 會強制要求這個參數才能處理，沒加會直接丟例外（也實測過）。
        asr_out = asr_pipeline(
            audio_data,
            return_timestamps=True,
            generate_kwargs={"max_new_tokens": 128, "num_beams": 1},
        )

        # 提取模型轉錄出的實際文字
        detected_text = asr_out["text"].strip()

        # --- 語言與意圖判定邏輯 ---
        # Whisper 的語言 token 只有 <|zh|>/<|en|>/<|id|>，沒有台語(nan)/客語(hak)專屬 tag，
        # 模型本身不會回傳方言標籤，只能對辨識出的文字做關鍵字比對來猜測方言。
        # 用「命中詞數計分＋比較高低」取代舊版「先查 hakka、中一個詞就 break」的寫法：
        # 舊寫法會讓 keywords.json 裡兩邊清單都有的共用詞（如「跌倒」「頭暈」其實是純國語詞彙，
        # 沒有方言區分度）永遠因為 hakka 先檢查而誤判成客語，即使是純國語句子也一樣。
        def _score_dialect(text: str, lexicon: dict):
            best_intent, best_count = None, 0
            for intent, words in lexicon.items():
                count = sum(1 for w in words if w in text)
                if count > best_count:
                    best_intent, best_count = intent, count
            return best_intent, best_count

        hakka_intent, hakka_score = _score_dialect(detected_text, KEYWORDS.get("hakka", {}))
        taiwanese_intent, taiwanese_score = _score_dialect(detected_text, KEYWORDS.get("taiwanese", {}))

        detected_lang = "現代標準漢語 (國語)"
        detected_intent = "general"

        if hakka_score > taiwanese_score and hakka_score > 0:
            detected_lang = "臺灣客家語"
            detected_intent = hakka_intent
        elif taiwanese_score > hakka_score and taiwanese_score > 0:
            detected_lang = "臺灣台語 (閩南語)"
            detected_intent = taiwanese_intent
        # 分數相同（含兩邊都只命中同一個共用詞、或都沒中）時保守維持國語預設值，不亂猜

        # 最終除錯紀錄
        print(f"📌 [模型辨識文字]: '{detected_text}'")
        print(f"📌 [判定結果]: 語言={detected_lang}, 意圖={detected_intent} (hakka={hakka_score}, taiwanese={taiwanese_score})")

        # 2.5 已知照護句型比對（快速通道）
        # 用「正規化前」的原始文字比對，理由跟方言判定一樣：Gemini 正規化會把
        # 台語用字洗成標準中文，比對台語句型要在那之前做，才比對得到。
        known_intent_code, known_intent = match_known_care_intent(detected_text)

        if known_intent:
            # 命中已知句型：直接用固定翻譯，不經過 Gemini 正規化/AI 分類/M2M100，
            # 速度快、結果保證正確，不受 ASR 辨識自然口語不穩定的影響。
            print(f"📌 [已知句型命中]: {known_intent_code} ({known_intent['label']})")
            normalized_text = known_intent["mandarin"]
            best_intent = known_intent["label"]
            confidence_score = 1.0
            severity_level = known_intent["severity_level"]
            event_type = known_intent["event_type"]
            notify_family = known_intent["notify_family"]
            vi_trans = known_intent["vietnamese"]
            id_trans = known_intent["indonesian"]
        else:
            # 2.6 AI 空耳/語意正規化（Gemini）
            # 注意：方言判定（上面）一定要用「正規化前」的原始文字 detected_text，
            # 因為正規化會把方言用字統一改寫成標準中文，正規化後的文字已經看不出方言痕跡。
            # 但後面的意圖分類、翻譯要接語意正確的文字，不然空耳亂碼會直接被拿去翻譯。
            normalized_text = detected_text
            try:
                ai_norm = get_ai_analysis(detected_text)
                if ai_norm and ai_norm.get("normalized_chinese"):
                    normalized_text = ai_norm["normalized_chinese"]
                    print(f"📌 [AI 正規化後文字]: '{normalized_text}'")
                else:
                    print("⚠️ [AI 正規化無結果，改用原始 ASR 文字]")
            except Exception as e:
                print(f"⚠️ [AI 正規化失敗，改用原始 ASR 文字]: {e}")

            # 3. 🔥【真・AI 語意分析】
            analysis_res = classifier(normalized_text, candidate_labels=AI_CARE_LABELS)
            best_intent = analysis_res["labels"][0]
            confidence_score = analysis_res["scores"][0]

            # --- 信心門檻邏輯 ---
            # event_type 統一對應到規格定義的 9 種固定代碼：
            # abdominal_pain / dizziness / chest_tightness / pain / refuse_medication /
            # fall / help / general_need / unknown
            # 注意：信心不足時明確標記 unknown，不再像舊版那樣悄悄改判成「一般閒聊」
            # （等於裝作沒事）——AI 不確定的情況要讓看護/家屬知道「這句沒辦法判斷」，
            # 而不是被系統自己吃掉、當作沒發生過。
            if confidence_score < 0.5:
                print(f"⚠️ [AI 信心不足]: {confidence_score:.2f}，標記為 unknown")
                severity_level = "low"
                event_type = "unknown"
                notify_family = False
            else:
                print(f"📌 [AI 意圖分析結果]: {best_intent} (信心值: {confidence_score:.2f})")
                severity_level = "low"
                event_type = "general_need"
                notify_family = False

                if "意外傷害" in best_intent:
                    severity_level = "high"
                    event_type = "fall"
                    notify_family = True
                elif "醫療警戒" in best_intent:
                    severity_level = "medium"
                    event_type = "pain"
                    notify_family = True
                elif "日常生理需求" in best_intent:
                    severity_level = "low"
                    event_type = "general_need"
                # 「一般閒聊與日常對話」高信心時也歸類為 general_need
                # （規格 9 類裡沒有獨立的「純聊天」代碼，且與日常需求都屬於低風險、
                # 不用通知家屬的情境，用同一個代碼收斂即可）

            # 4. 🔥【真・AI 動態翻譯機】直接叫實體模型把正規化後的文字轉成越文與印尼文！
            print(f"[AI 翻譯執行中...] 正在實時翻譯 '{normalized_text}'...")
            vi_trans = ai_translate(normalized_text, "vi")  # 真正翻譯成越南文
            id_trans = ai_translate(normalized_text, "id")  # 真正翻譯成印尼文

        # 5. 存文字紀錄，事後可透過 GET /api/voice/records 查詢
        save_care_record(
            audio_filename=file.filename,
            detected_lang=detected_lang,
            raw_text=detected_text,
            normalized_text=normalized_text,
            matched_known_intent=known_intent_code,
            predicted_intent=best_intent,
            confidence=confidence_score,
            event_type=event_type,
            severity_level=severity_level,
            notify_family=notify_family,
            vietnamese_output=vi_trans,
            indonesian_output=id_trans,
        )

        # 5.5 同步寫入 Postgres 正式資料庫（隊友負責）。跟上面的 SQLite 一樣採
        # 「失敗就跳過」原則：Postgres 沒開/連不上/elder_id 查無此人，都只印警告，
        # 不影響本次語音處理的回應——使用者不該因為資料庫問題而收不到辨識結果。
        if DB_AVAILABLE:
            try:
                # 本機算語意向量，給之後的「相似歷史事件搜尋」用。存進資料庫的內容
                # 用 passage 前綴（見 local_embeddings.py），算失敗就存 NULL，
                # 不要塞假向量污染語意搜尋（隊友原本 ai_client.py 失敗時塞全 0 向量
                # 的做法容易讓搜尋結果被這些假向量互相誤判成「相似」，這裡刻意不這樣做）。
                embedding_vector = get_embedding(normalized_text)
                ok, result = execute_create_event(
                    elder_id=elder_id,
                    reporter_id=reporter_id,
                    source_language=DIALECT_TO_LANG_CODE.get(detected_lang, "zh-TW"),
                    original_text=detected_text,
                    event_type=event_type,
                    severity=severity_level,
                    normalized_text=normalized_text,
                    translations={"vi": vi_trans, "id": id_trans},
                    embedding_vector=embedding_vector,
                )
                if ok:
                    print(f"✅ [Postgres] 事件已寫入，event_id={result.get('id')}")
                else:
                    print(f"⚠️ [Postgres 寫入失敗，不影響本次回應]: {result}")
            except Exception as e:
                print(f"⚠️ [Postgres 寫入失敗，不影響本次回應]: {e}")

        # 固定 JSON 契約（對齊團隊 AI 運算引擎層規格，欄位名稱/結構不可因模型
        # 回答內容而改變，讓其他模組/前端可以穩定串接）：
        # {original_text, normalized_chinese, vietnamese_text, event_type,
        #  severity, notify_family, confidence}
        # indonesian_text / detected_dialect / matched_known_intent 是額外附加欄位，
        # 不在契約規定的必要欄位內，但不影響其他人只讀取契約規定欄位的解析方式。
        return {
            "original_text": detected_text,
            "normalized_chinese": normalized_text,
            "vietnamese_text": vi_trans,
            "event_type": event_type,
            "severity": severity_level,
            "notify_family": notify_family,
            "confidence": confidence_score,
            "indonesian_text": id_trans,
            "detected_dialect": detected_lang,
            "matched_known_intent": known_intent_code
        }
    except Exception as e:  # 讓 'e' 對齊 'try'
        print(f"❌ [AI 引擎崩潰] 原因: {str(e)}")
        raise HTTPException(status_code=500, detail=f"實體模型推論失敗: {str(e)}")
            
    finally:                # 讓 'f' 對齊 'try'
        if os.path.exists(file_path):
            os.remove(file_path)


@app.get("/api/voice/records")
async def get_care_records(limit: int = 50, severity_level: str = None):
    """查詢文字紀錄，預設回傳最新 50 筆（依 created_at 由新到舊）。
    可加 ?severity_level=high 只看高風險事件（例如跌倒、喘不過氣）。"""
    try:
        with sqlite3.connect(RECORDS_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            if severity_level:
                cur = conn.execute(
                    "SELECT * FROM care_records WHERE severity_level = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (severity_level, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM care_records ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = [dict(r) for r in cur.fetchall()]
        return {"status": "success", "count": len(rows), "records": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查詢紀錄失敗: {e}")


@app.get("/api/voice/trend/{elder_id}")
async def get_trend_summary(elder_id: str):
    """模組五：趨勢判斷。三級分類（紅/橘/黃），只回傳資料變化描述，不含任何
    醫療判斷/建議字眼（規格要求，紅/橘裡引用的緊急處置指引是照抄公開守則，
    不是 AI 生成的建議）。需要 Postgres 才能查。"""
    if not DB_AVAILABLE:
        raise HTTPException(status_code=503, detail="Postgres 未連線，趨勢判斷需要正式資料庫。")
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                summary = generate_trend_summary(cur, elder_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"趨勢判斷查詢失敗: {e}")

    # 把規則邏輯算好的數字描述，額外潤飾成一段給家屬看的自然語句。
    # 判斷本身在上面 generate_trend_summary() 已經算完，這裡只是「講得更順」，
    # 失敗（額度用盡/連線問題）就不設 narrative，前端改顯示三個陣列的原始清單，
    # 不會讓整個趨勢查詢因為這一步失敗而掛掉。
    summary["narrative"] = None
    if summary["has_notable_change"]:
        try:
            summary["narrative"] = polish_trend_summary(
                summary["red_findings"], summary["orange_findings"], summary["yellow_findings"]
            )
        except Exception as e:
            print(f"⚠️ [趨勢摘要潤飾失敗，不影響原始數字描述]: {e}")

    # 額外的 AI 質性觀察：讀長者最近逐字稿，找規則邏輯（純算次數）抓不到的
    # 語氣/話題模式。刻意獨立於 red/orange/yellow 分級判斷之外——這欄位的
    # 內容不會、也不能拿去決定分級，只是給家屬多一個參考角度，見
    # ai_translator.py 的 analyze_conversation_tone() 檔頭說明其安全邊界。
    summary["conversation_tone_observation"] = None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                recent_texts = get_recent_conversation_texts(cur, elder_id)
        if recent_texts:
            summary["conversation_tone_observation"] = analyze_conversation_tone(recent_texts)
    except Exception as e:
        print(f"⚠️ [言談模式觀察失敗，不影響分級判斷]: {e}")

    return summary


@app.post("/api/voice/daily-summary/{elder_id}")
async def create_daily_summary(elder_id: str):
    """把「今天」單獨一天的語音事件+健康量測彙整成一筆摘要，寫進
    daily_summaries 表（同一天重複呼叫會覆蓋更新，不會重複產生紀錄）。
    跟 /api/voice/trend 的差異：trend 是比較「這期 vs 上期」，這支是單純整理
    「今天發生了什麼」，兩者互補使用。overall_status 一樣是規則算出來的
    （urgent/attention/stable），不是 AI 判斷。"""
    if not DB_AVAILABLE:
        raise HTTPException(status_code=503, detail="Postgres 未連線，每日摘要需要正式資料庫。")
    try:
        with get_db_connection(autocommit=False) as conn:
            with conn.cursor() as cur:
                summary = generate_daily_summary(cur, elder_id)
                save_daily_summary(cur, summary)
            conn.commit()
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"每日摘要產生失敗: {e}")


@app.post("/api/voice/caregiver-reply")
async def caregiver_reply(
    file: UploadFile = File(...),
    source_language: str = Form(...),  # "vi"（越南文）或 "id"（印尼文），看護講哪種語言由呼叫端指定，不做自動偵測
    elder_id: str = Form(DEFAULT_ELDER_ID),
    group_id: str = Form(DEFAULT_GROUP_ID),
    sender_id: str = Form(DEFAULT_REPORTER_ID),
):
    """
    看護語音回覆長者：看護講越南文/印尼文 → 辨識 → 翻譯成中文，給長者聽/看得懂。
    是 listen_elder 的反方向。實測過 Breeze 對印尼文語音辨識平均 CER 8.6%
    （5句真實印尼文語料，3句零錯誤），直接沿用同一個 Breeze 模型做 ASR，
    不用另外載入新模型。翻譯沿用 M2M100，這次改用 src_lang 參數指定正確的
    來源語言（不是像 listen_elder 那樣固定假設輸入是中文）。
    """
    if source_language not in ("vi", "id"):
        raise HTTPException(status_code=400, detail="source_language 必須是 'vi' 或 'id'")

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        print(f"\n[看護回覆] 收到語音: {file.filename}（來源語言: {source_language}）")

    try:
        audio_data, _sample_rate = librosa.load(file_path, sr=16000, mono=True)

        # 語音辨識：沿用 listen_elder 同一套設定（max_new_tokens 限制生成長度，
        # 避免重複生成迴圈；num_beams=1 已驗證穩定，見 num_beams=5 那次踩雷紀錄）
        asr_out = asr_pipeline(
            audio_data,
            return_timestamps=True,
            generate_kwargs={"max_new_tokens": 128, "num_beams": 1},
        )
        original_text = asr_out["text"].strip()
        print(f"📌 [看護回覆辨識文字]: '{original_text}'")

        # 翻譯成中文給長者看/聽
        translated_text = ai_translate(original_text, target_lang="zh", src_lang=source_language)
        print(f"📌 [翻譯成中文]: '{translated_text}'")

        # 存進 chat_messages（隊友的表，直接寫完整欄位，因為他現成的
        # insert_chat_message() 沒有存 translated_text/source_language 這些，
        # 見程式碼審查時發現的缺口）
        message_id = None
        if DB_AVAILABLE:
            try:
                with get_db_connection(autocommit=False) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO chat_messages (
                                group_id, sender_id, elder_id, message_type,
                                content, translated_text, source_language, target_language
                            ) VALUES (%s::uuid, %s::uuid, %s::uuid, 'audio', %s, %s, %s, 'zh')
                            RETURNING id;
                            """,
                            (group_id, sender_id, elder_id, original_text, translated_text, source_language),
                        )
                        message_id = cur.fetchone()[0]
                    conn.commit()
                print(f"✅ [Postgres] 看護回覆已寫入 chat_messages，id={message_id}")
            except Exception as e:
                print(f"⚠️ [Postgres 寫入失敗，不影響本次回應]: {e}")

        return {
            "original_text": original_text,
            "translated_text": translated_text,
            "source_language": source_language,
            "target_language": "zh",
            "message_id": str(message_id) if message_id else None,
        }
    except Exception as e:
        print(f"❌ [看護回覆處理失敗] 原因: {str(e)}")
        raise HTTPException(status_code=500, detail=f"看護回覆處理失敗: {str(e)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


if __name__ == "__main__":
    import uvicorn
    # reload=True 會監看整個專案目錄；每次請求把音檔寫進/刪出 ./temp_audio 都會
    # 被當成「程式碼變更」，觸發整個服務（含三個大模型）重新載入一次，記憶體疊加
    # 幾次就會 OOM（mkl_malloc failed to allocate memory）。這支服務載入成本很高，
    # 不適合開 reload；要改程式碼時手動重啟即可。
    #
    # 傳 app 物件本身，不要傳 "main:app" 字串：用字串時 uvicorn 會另外把這支檔案
    # 當成一個叫 "main" 的全新模組再 import 一次（跟目前用 __main__ 執行的這份是
    # 兩個不同的模組實例），導致最上面那三個大模型在同一個 process 裡被重複載入
    # 兩遍，記憶體直接乘二。reload=False 時不需要字串形式，直接傳物件即可。
    uvicorn.run(app, host="127.0.0.1", port=8001, reload=False)