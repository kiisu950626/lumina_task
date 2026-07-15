import os
import sys

# ── 魔法設定：動態把外層資料夾加入搜尋路徑 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(BASE_DIR)

import psycopg2
from psycopg2.extras import RealDictCursor
from relational.queries import execute_create_event, query_similar_historical_events, PG_DSN

# ⭐ 引入你寫好的 Gemini AI 客戶端
from skeleton.ai_client import get_embedding

print("=== 🚀 開始測試 Gemini Vector 向量搜尋 (真實語意測試) ===")

# 1. 自動抓取資料庫裡現有的 elder_id 和 user_id 來測試
conn = psycopg2.connect(PG_DSN)
with conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("SELECT id FROM elders LIMIT 1;")
    elder_id = str(cur.fetchone()['id'])
    cur.execute("SELECT id FROM users LIMIT 1;")
    user_id = str(cur.fetchone()['id'])
conn.close()

print(f"✅ 取得測試用 Elder ID: {elder_id}")

# 2. 定義三個測試句子
text1 = "阿公說他頭暈"
text2 = "阿公昨天肚子痛"
text3 = "阿公昨天也覺得頭昏昏的"

# 3. 呼叫真實的 Gemini API 產生語意向量
print("\n🧠 正在呼叫 Gemini 產生真實語意向量 (請稍候)...")
vec1 = get_embedding(text1)
vec2 = get_embedding(text2)
vec3 = get_embedding(text3)

# 4. 寫入資料庫
print("\n📝 正在寫入測試事件...")
execute_create_event(elder_id, user_id, "zh-TW", text1, "dizziness", "high", embedding_vector=vec1)
execute_create_event(elder_id, user_id, "zh-TW", text2, "stomachache", "medium", embedding_vector=vec2)
execute_create_event(elder_id, user_id, "zh-TW", text3, "dizziness", "medium", embedding_vector=vec3)

# 5. 用「頭暈」的向量去搜尋歷史紀錄
print(f"\n🔍 正在使用『{text1}』的向量搜尋相似歷史事件...")
results = query_similar_historical_events(elder_id, vec1, limit=5)

for r in results:
    print(f"-> 找到事件: {r['original_text']} (向量距離: {r['cosine_distance']:.4f})")