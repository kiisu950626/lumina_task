# -*- coding: utf-8 -*-
# 產生一批分散在過去 3 週的測試資料，專門給「趨勢判斷模組」demo/驗證用。
# 只灌有把握、資料庫裡真的有欄位可以裝的東西：疼痛類事件次數、血壓血糖數值。
# 不灌造假的「活動量」「睡眠時數」——這兩個資料庫沒有對應欄位，之前已經跟
# 使用者確認過不自己編造，等團隊補上真正的資料來源再做那兩項。
#
# 刻意設計成「最近一週疼痛事件比前兩週多」「血壓緩慢上升」，這樣趨勢模組
# 才有東西可以描述，不是隨機亂數（隨機亂數可能剛好沒有趨勢，demo 會很尷尬）。
import os
import sys
import random
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(BASE_DIR)

import psycopg2
import psycopg2.extras
from skeleton.config import DB_CONFIG

ELDER_ID = "33333333-3333-3333-3333-333333333333"   # 王阿公
REPORTER_ID = "22222222-2222-2222-2222-222222222222"  # 看護阮氏秋

random.seed(7)
now = datetime.now(timezone.utc)

# --- 疼痛類事件：週1(21~15天前) 少 → 週2(14~8天前) 中 → 週3(7~0天前，本週) 多 ---
PAIN_TYPES = ["abdominal_pain", "pain", "dizziness", "chest_tightness"]
PAIN_TEXTS = {
    "abdominal_pain": ("我肚子痛", "Tôi đau bụng", "Perut saya sakit"),
    "pain": ("我頭痛", "Tôi đau đầu", "Saya sakit kepala"),
    "dizziness": ("我頭暈", "Tôi bị chóng mặt", "Saya pusing"),
    "chest_tightness": ("我胸口很悶", "Tôi cảm thấy tức ngực", "Dada saya terasa sesak"),
}
WEEK_COUNTS = [2, 4, 7]  # 週1、週2、週3(本週) 各自要塞幾筆疼痛事件

events_rows = []
for week_idx, count in enumerate(WEEK_COUNTS):
    day_start = 21 - week_idx * 7
    for _ in range(count):
        days_ago = random.randint(day_start - 6, day_start)
        ts = now - timedelta(days=days_ago, hours=random.randint(0, 23))
        etype = random.choice(PAIN_TYPES)
        zh, vi, idn = PAIN_TEXTS[etype]
        severity = "high" if etype == "chest_tightness" else "medium"
        events_rows.append((ELDER_ID, REPORTER_ID, "zh-TW", zh, zh, etype, severity, ts))

# --- 血壓：緩慢上升的趨勢，本週偏高 ---
health_rows = []
for days_ago in range(21, -1, -1):
    ts = now - timedelta(days=days_ago, hours=random.randint(6, 20))
    # 越接近今天，基準值越高（模擬血壓緩慢上升的趨勢）
    drift = (21 - days_ago) * 0.4
    systolic = round(122 + drift + random.uniform(-4, 4))
    diastolic = round(76 + drift * 0.5 + random.uniform(-3, 3))
    if systolic <= diastolic:
        systolic = diastolic + 10
    health_rows.append((ELDER_ID, systolic, diastolic, ts))

def main():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for elder_id, reporter_id, lang, orig, norm, etype, severity, ts in events_rows:
                cur.execute(
                    """
                    INSERT INTO events (
                        elder_id, reporter_id, source_language, original_text,
                        normalized_text, translations, event_type, severity, status, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                    """,
                    (elder_id, reporter_id, lang, orig, norm,
                     psycopg2.extras.Json({}), etype, severity, ts)
                )
            for elder_id, systolic, diastolic, ts in health_rows:
                cur.execute(
                    """
                    INSERT INTO health_measurements (
                        elder_id, systolic_bp, diastolic_bp, data_source, measured_at
                    ) VALUES (%s, %s, %s, 'demo_seed', %s)
                    """,
                    (elder_id, systolic, diastolic, ts)
                )
        conn.commit()
        print(f"完成：新增 {len(events_rows)} 筆疼痛事件、{len(health_rows)} 筆血壓量測，"
              f"時間分布在過去 21 天內。")
    except Exception as e:
        conn.rollback()
        print(f"失敗：{e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
