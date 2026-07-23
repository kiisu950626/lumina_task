# -*- coding: utf-8 -*-
"""
模組五：趨勢判斷

規格要求：AI 不負責醫療診斷，只做資料變化描述。
這裡全部用確定性的統計比較（次數、平均值），不叫任何 AI/LLM，理由：
  1. 血壓血糖有醫學公認的比較方式（跟過去平均比），不需要語言模型「判斷」
  2. 不吃 Gemini 額度
  3. 天生就不會踩到「AI 給醫療建議/診斷」這條規格明文禁止的紅線——
     每一句輸出都可以回推是哪個數字比較出來的，不是模型憑感覺生成的文字
"""
from datetime import datetime, timedelta, timezone

PAIN_EVENT_TYPES = ("abdominal_pain", "pain", "dizziness", "chest_tightness")


def _period_counts(cur, elder_id: str, event_types, days: int):
    """回傳 (最近 days 天次數, 再往前 days 天次數) 的 tuple。"""
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE created_at > NOW() - (%s || ' days')::interval) AS recent,
            COUNT(*) FILTER (
                WHERE created_at <= NOW() - (%s || ' days')::interval
                  AND created_at > NOW() - (%s || ' days')::interval
            ) AS previous
        FROM events
        WHERE elder_id = %s::uuid
          AND event_type = ANY(%s)
          AND deleted_at IS NULL;
        """,
        (days, days, days * 2, elder_id, list(event_types)),
    )
    row = cur.fetchone()
    return (row[0] or 0, row[1] or 0)


def analyze_pain_trend(cur, elder_id: str, days: int = 7) -> str | None:
    """比較「過去 N 天」跟「再前 N 天」的疼痛/不適類事件次數。
    只在次數增加時回報——這個函式結果會被放進橘色「建議聯絡醫療人員」層級，
    次數減少是好消息、不是警訊，不該出現在需要留意的分級裡（之前的版本兩種
    方向都回報，混進橘色分級後會誤導成「減少也要聯絡醫療人員」，已修正）。"""
    recent, previous = _period_counts(cur, elder_id, PAIN_EVENT_TYPES, days)
    if recent == 0:
        return None
    if previous == 0:
        return f"過去 {days} 天出現 {recent} 次疼痛/不適相關事件，前 {days} 天無相關紀錄。"
    diff = recent - previous
    if diff <= 0:
        return None  # 持平或減少，不用特別提
    return (
        f"過去 {days} 天疼痛/不適相關事件 {recent} 次，"
        f"較前 {days} 天的 {previous} 次增加 {diff} 次。"
    )


def analyze_bp_trend(cur, elder_id: str, days: int = 7) -> str | None:
    """比較「過去 N 天」血壓平均值跟「再前 N 天」的平均值。"""
    cur.execute(
        """
        SELECT
            AVG(systolic_bp) FILTER (WHERE measured_at > NOW() - (%s || ' days')::interval) AS recent_sys,
            AVG(diastolic_bp) FILTER (WHERE measured_at > NOW() - (%s || ' days')::interval) AS recent_dia,
            AVG(systolic_bp) FILTER (
                WHERE measured_at <= NOW() - (%s || ' days')::interval
                  AND measured_at > NOW() - (%s || ' days')::interval
            ) AS prev_sys,
            AVG(diastolic_bp) FILTER (
                WHERE measured_at <= NOW() - (%s || ' days')::interval
                  AND measured_at > NOW() - (%s || ' days')::interval
            ) AS prev_dia
        FROM health_measurements
        WHERE elder_id = %s::uuid;
        """,
        (days, days, days, days * 2, days, days * 2, elder_id),
    )
    recent_sys, recent_dia, prev_sys, prev_dia = cur.fetchone()
    if recent_sys is None or prev_sys is None:
        return None  # 任一區間沒有量測資料，無法比較，不硬湊結論

    sys_diff = round(recent_sys - prev_sys, 1)
    # 收縮壓變化在 ±3 mmHg 內視為量測誤差範圍，不特別描述
    if abs(sys_diff) < 3:
        return None
    direction = "上升" if sys_diff > 0 else "下降"
    return (
        f"過去 {days} 天血壓平均 {round(recent_sys)}/{round(recent_dia)} mmHg，"
        f"較前 {days} 天的 {round(prev_sys)}/{round(prev_dia)} mmHg{direction}"
        f"約 {abs(sys_diff)} mmHg（收縮壓）。"
    )


def analyze_bp_vs_baseline(cur, elder_id: str, recent_days: int = 7, baseline_days: int = 90) -> str | None:
    """
    比較「最近 N 天」跟「這位長者自己過去更長一段時間（不含最近 N 天）」的平均值。
    用意：長者長期血壓穩定在偏高但已用藥控制的狀態很常見，跟「一般人標準」比
    每天都會被標記異常、家屬看久了會麻痺；跟「他自己平常的基準」比，才抓得到
    「本來穩定，這週突然變化」這種真正值得注意的情況，而不是一直誤判已控制的
    慢性狀況。基準線刻意排除最近 N 天，避免最近的變化把自己的基準線也拉高/拉低。
    """
    cur.execute(
        """
        SELECT
            AVG(systolic_bp) FILTER (WHERE measured_at > NOW() - (%s || ' days')::interval) AS recent_sys,
            AVG(diastolic_bp) FILTER (WHERE measured_at > NOW() - (%s || ' days')::interval) AS recent_dia,
            AVG(systolic_bp) FILTER (
                WHERE measured_at <= NOW() - (%s || ' days')::interval
                  AND measured_at > NOW() - (%s || ' days')::interval
            ) AS baseline_sys,
            AVG(diastolic_bp) FILTER (
                WHERE measured_at <= NOW() - (%s || ' days')::interval
                  AND measured_at > NOW() - (%s || ' days')::interval
            ) AS baseline_dia
        FROM health_measurements
        WHERE elder_id = %s::uuid;
        """,
        (recent_days, recent_days, recent_days, baseline_days, recent_days, baseline_days, elder_id),
    )
    recent_sys, recent_dia, baseline_sys, baseline_dia = cur.fetchone()
    if recent_sys is None or baseline_sys is None:
        return None  # 基準線資料不夠長（例如剛開始使用系統），無法比較，不硬湊

    sys_diff = round(recent_sys - baseline_sys, 1)
    # 跟個人基準線比較用比較寬的門檻（8 mmHg），因為基準線本身涵蓋較長期間、
    # 波動本來就比「這週 vs 上週」的短期比較大，門檻太小會太常誤報
    if abs(sys_diff) < 8:
        return None
    direction = "偏高" if sys_diff > 0 else "偏低"
    return (
        f"過去 {recent_days} 天血壓平均 {round(recent_sys)}/{round(recent_dia)} mmHg，"
        f"較這位長者過去 {baseline_days} 天的個人平均 {round(baseline_sys)}/{round(baseline_dia)} mmHg"
        f"{direction}約 {abs(sys_diff)} mmHg（收縮壓），與平常狀態不同。"
    )


# 紅色等級要求「血壓≥180/120 合併胸痛/呼吸困難/肢體麻木無力/視力改變/說話困難」。
# 但團隊定義的 9 種 event_type（見 care_intents.json）裡沒有肢體無力、視力改變、
# 說話困難這幾種中風警示症狀的專屬分類——這是已知缺口，先用 chest_tightness
# （胸悶/胸痛）跟 help（求助，語意上涵蓋「講不清楚、身體不對勁」等長者講不出
# 具體症狀但明顯求助的情況）當替代近似值。要更精確，需要團隊擴充 event_type
# 分類，或另外設計一個獨立的症狀標記機制。
RED_SYMPTOM_EVENT_TYPES = ("chest_tightness", "help")

BP_CRISIS_SYSTOLIC = 180
BP_CRISIS_DIASTOLIC = 120


def _latest_bp_crisis_reading(cur, elder_id: str, days: int):
    """回傳最近 N 天內最新一筆血壓≥180/120的量測，沒有則回傳 None。"""
    cur.execute(
        """
        SELECT systolic_bp, diastolic_bp, measured_at
        FROM health_measurements
        WHERE elder_id = %s::uuid
          AND measured_at > NOW() - (%s || ' days')::interval
          AND (systolic_bp >= %s OR diastolic_bp >= %s)
        ORDER BY measured_at DESC
        LIMIT 1;
        """,
        (elder_id, days, BP_CRISIS_SYSTOLIC, BP_CRISIS_DIASTOLIC),
    )
    return cur.fetchone()


def check_bp_red_alert(cur, elder_id: str, days: int = 3) -> str | None:
    """
    紅色「立即處理」：血壓≥180/120，且同一個 N 天窗口內同時出現
    chest_tightness/help 事件（見上方 RED_SYMPTOM_EVENT_TYPES 說明其為近似值）。
    符合條件才輸出標準緊急處置指引——這段文字是公開既有的血壓緊急處置共同
    守則（先休息重新量測、有合併症狀撥打119），不是 AI 生成的醫療判斷，
    是照抄公開指引內容。
    """
    bp_row = _latest_bp_crisis_reading(cur, elder_id, days)
    if not bp_row:
        return None
    systolic, diastolic, measured_at = bp_row

    cur.execute(
        """
        SELECT COUNT(*) FROM events
        WHERE elder_id = %s::uuid
          AND event_type = ANY(%s)
          AND created_at > NOW() - (%s || ' days')::interval
          AND deleted_at IS NULL;
        """,
        (elder_id, list(RED_SYMPTOM_EVENT_TYPES), days),
    )
    symptom_count = cur.fetchone()[0]
    if symptom_count == 0:
        return None  # 血壓雖高但沒有合併症狀事件，不到紅色等級，歸類到橘色

    return (
        f"{measured_at.strftime('%m/%d %H:%M')} 血壓 {systolic}/{diastolic} mmHg"
        f"（≥180/120），且近期同時出現胸悶/求助類事件。"
        f"標準處置：若同時有胸痛、呼吸困難、肢體麻木無力、視力改變、說話困難等"
        f"症狀，請立即撥打119。"
    )


def check_bp_orange_alert(cur, elder_id: str, days: int = 3) -> str | None:
    """
    橘色「建議聯絡醫療人員」之一：血壓≥180/120，但沒有合併紅色等級的症狀事件
    （紅色條件見 check_bp_red_alert）。標準處置：休息後重新量測，仍偏高則
    儘速聯絡醫療人員——同樣是照抄公開指引，不是 AI 判斷。
    """
    bp_row = _latest_bp_crisis_reading(cur, elder_id, days)
    if not bp_row:
        return None
    systolic, diastolic, measured_at = bp_row

    cur.execute(
        """
        SELECT COUNT(*) FROM events
        WHERE elder_id = %s::uuid
          AND event_type = ANY(%s)
          AND created_at > NOW() - (%s || ' days')::interval
          AND deleted_at IS NULL;
        """,
        (elder_id, list(RED_SYMPTOM_EVENT_TYPES), days),
    )
    symptom_count = cur.fetchone()[0]
    if symptom_count > 0:
        return None  # 已經在紅色等級裡回報過，橘色不重複講

    return (
        f"{measured_at.strftime('%m/%d %H:%M')} 血壓 {systolic}/{diastolic} mmHg（≥180/120）。"
        f"標準處置：先休息至少1分鐘後重新量測，若再次量測仍偏高，請儘速聯絡醫療人員。"
    )


def analyze_medication_refusal_trend(cur, elder_id: str, days: int = 7) -> str | None:
    """橘色「經常忘記/拒絕服藥」：比較拒絕服藥事件次數的變化。"""
    recent, previous = _period_counts(cur, elder_id, ("refuse_medication",), days)
    if recent == 0:
        return None
    if previous == 0:
        return f"過去 {days} 天出現 {recent} 次拒絕服藥紀錄，前 {days} 天無相關紀錄。"
    diff = recent - previous
    if diff <= 0:
        return None  # 沒有增加就不用特別提
    return f"過去 {days} 天拒絕服藥 {recent} 次，較前 {days} 天的 {previous} 次增加 {diff} 次。"


def check_missing_bp_data(cur, elder_id: str, gap_days: int = 5) -> str | None:
    """
    黃色「資料缺漏」：檢查最近一筆血壓量測距今有沒有超過 gap_days 天。
    這個檢查本身也是規則邏輯（比對日期差），純粹提醒「資料太久沒更新」，
    不代表長者身體狀況異常，只是資料完整度不足、沒辦法做出可信的趨勢判斷。
    """
    cur.execute(
        """
        SELECT MAX(measured_at) FROM health_measurements WHERE elder_id = %s::uuid;
        """,
        (elder_id,),
    )
    last_measured = cur.fetchone()[0]
    if last_measured is None:
        return "尚無任何血壓量測紀錄。"

    gap = datetime.now(timezone.utc) - last_measured
    if gap.days < gap_days:
        return None
    return f"最近一筆血壓量測是 {last_measured.strftime('%m/%d')}，已經 {gap.days} 天沒有新的量測資料。"


# 語音事件對應的嚴重度，用來決定當天的 overall_status（跟 main.py 產生 events
# 時用的 severity 值 low/medium/high 對齊，crisis 只有血壓急症門檻會用到）
_STATUS_RANK = {"stable": 0, "attention": 1, "urgent": 2}


def generate_daily_summary(cur, elder_id: str, target_date=None) -> dict:
    """
    把「今天」單獨一天發生的事（語音事件 + 健康量測）彙整成一份摘要，對應
    daily_summaries 表。跟 generate_trend_summary() 的差異：趨勢判斷是比較
    「這期 vs 上期」，這裡是單純整理「今天發生了什麼」，兩者互補、不重疊。

    overall_status 一樣是規則算出來的（不是 AI 判斷）：
      urgent：今天有 high severity 的語音事件，或今天有血壓急症門檻讀數
      attention：今天有 medium severity 事件，或今天有疼痛/不適類事件
      stable：以上皆無
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    # 當天的語音事件
    cur.execute(
        """
        SELECT event_type, severity, original_text, created_at
        FROM events
        WHERE elder_id = %s::uuid
          AND created_at::date = %s
          AND deleted_at IS NULL
        ORDER BY created_at;
        """,
        (elder_id, target_date),
    )
    event_rows = cur.fetchall()

    # 當天的健康量測
    cur.execute(
        """
        SELECT systolic_bp, diastolic_bp, heart_rate, blood_sugar, measured_at
        FROM health_measurements
        WHERE elder_id = %s::uuid
          AND measured_at::date = %s
        ORDER BY measured_at;
        """,
        (elder_id, target_date),
    )
    health_rows = cur.fetchall()

    # --- 決定 overall_status（規則邏輯）---
    status = "stable"
    has_pain_event = any(row[0] in PAIN_EVENT_TYPES for row in event_rows)
    has_high_severity = any(row[1] == "high" for row in event_rows)
    has_medium_severity = any(row[1] == "medium" for row in event_rows)
    has_crisis_bp = any(
        (row[0] is not None and row[0] >= 180) or (row[1] is not None and row[1] >= 120)
        for row in health_rows
    )
    if has_high_severity or has_crisis_bp:
        status = "urgent"
    elif has_medium_severity or has_pain_event:
        status = "attention"

    # --- 組裝內容描述（純事實列點，不下判斷）---
    lines = []
    if event_rows:
        lines.append(f"今日語音互動共 {len(event_rows)} 次。")
        type_counts = {}
        for row in event_rows:
            type_counts[row[0]] = type_counts.get(row[0], 0) + 1
        type_desc = "、".join(f"{t} {c} 次" for t, c in type_counts.items())
        lines.append(f"事件類型：{type_desc}。")
    else:
        lines.append("今日無語音互動紀錄。")

    if health_rows:
        bp_readings = [(r[0], r[1]) for r in health_rows if r[0] is not None]
        if bp_readings:
            avg_sys = round(sum(r[0] for r in bp_readings) / len(bp_readings))
            avg_dia = round(sum(r[1] for r in bp_readings) / len(bp_readings))
            lines.append(f"今日血壓量測 {len(bp_readings)} 筆，平均 {avg_sys}/{avg_dia} mmHg。")
    else:
        lines.append("今日無健康數據量測紀錄。")

    return {
        "elder_id": elder_id,
        "summary_date": target_date.isoformat(),
        "overall_status": status,
        "content": " ".join(lines),
        "event_count": len(event_rows),
        "health_measurement_count": len(health_rows),
    }


def get_recent_conversation_texts(cur, elder_id: str, days: int = 7, limit: int = 30) -> list[str]:
    """
    取出最近 N 天的原始逐字稿（normalized_text 優先，沒有就用 original_text），
    給 ai_translator.py 的 analyze_conversation_tone() 做語氣/情緒模式的質性
    觀察用。這裡只負責撈資料，不做任何判斷——判斷是 AI 那層的事，且那層的
    輸出被嚴格限制只能是「觀察描述」，見 ai_translator.py 檔頭說明。
    """
    cur.execute(
        """
        SELECT COALESCE(normalized_text, original_text) AS text
        FROM events
        WHERE elder_id = %s::uuid
          AND created_at > NOW() - (%s || ' days')::interval
          AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT %s;
        """,
        (elder_id, days, limit),
    )
    return [row[0] for row in cur.fetchall() if row[0]]


def save_daily_summary(cur, summary: dict) -> None:
    """把 generate_daily_summary() 算好的結果寫進 daily_summaries 表。
    同一位長者同一天只會有一筆（schema 的 UNIQUE(elder_id, summary_date)
    限制），重複呼叫會更新內容，不會產生重複紀錄。"""
    cur.execute(
        """
        INSERT INTO daily_summaries (elder_id, summary_date, overall_status, content)
        VALUES (%s::uuid, %s, %s, %s)
        ON CONFLICT (elder_id, summary_date)
        DO UPDATE SET overall_status = EXCLUDED.overall_status, content = EXCLUDED.content;
        """,
        (summary["elder_id"], summary["summary_date"], summary["overall_status"], summary["content"]),
    )


def generate_trend_summary(cur, elder_id: str) -> dict:
    """
    彙整所有趨勢描述成三級分類（紅/橘/黃），回傳給 API / 每日摘要使用。
    分級邏輯全部是規則比對（次數、平均值、日期差），不叫 AI 判斷：

      🔴 red_findings    —— 血壓≥180/120 且合併胸悶/求助類事件（近似中風警示症狀，
                            見 RED_SYMPTOM_EVENT_TYPES 說明）。附標準緊急處置指引
                            （照抄公開守則，非 AI 生成的醫療建議）。
      🟠 orange_findings —— 血壓≥180/120 但無合併症狀／疼痛不適事件增加／
                            拒絕服藥次數增加。「經醫師設定值」規格要求的醫囑
                            門檻，目前用個人基準線（analyze_bp_vs_baseline）
                            代替，因為資料庫還沒有醫師個別設定值的欄位。
      🟡 yellow_findings —— 血壓高於個人基準線／近期持續上升／資料缺漏過久。

    三個陣列裡的每一句話都可以回推是哪個數字/門檻比較出來的，沒有一句是模型
    憑感覺生成的判斷。
    """
    red_findings = []
    result = check_bp_red_alert(cur, elder_id)
    if result:
        red_findings.append(result)

    orange_findings = []
    for fn in (check_bp_orange_alert, analyze_pain_trend, analyze_medication_refusal_trend):
        result = fn(cur, elder_id)
        if result:
            orange_findings.append(result)

    yellow_findings = []
    for fn in (analyze_bp_vs_baseline, analyze_bp_trend, check_missing_bp_data):
        result = fn(cur, elder_id)
        if result:
            yellow_findings.append(result)

    return {
        "elder_id": elder_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "red_findings": red_findings,
        "orange_findings": orange_findings,
        "yellow_findings": yellow_findings,
        "has_notable_change": bool(red_findings or orange_findings or yellow_findings),
    }
