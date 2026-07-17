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
    """比較「過去 N 天」跟「再前 N 天」的疼痛/不適類事件次數，只描述變化，不下結論。"""
    recent, previous = _period_counts(cur, elder_id, PAIN_EVENT_TYPES, days)
    if recent == 0 and previous == 0:
        return None
    if previous == 0:
        return f"過去 {days} 天出現 {recent} 次疼痛/不適相關事件，前 {days} 天無相關紀錄。"
    diff = recent - previous
    if diff == 0:
        return None  # 沒有變化，不用特別提，避免資訊過載
    direction = "增加" if diff > 0 else "減少"
    return (
        f"過去 {days} 天疼痛/不適相關事件 {recent} 次，"
        f"較前 {days} 天的 {previous} 次{direction} {abs(diff)} 次。"
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


def generate_trend_summary(cur, elder_id: str) -> dict:
    """
    彙整所有趨勢描述，回傳給 API / 每日摘要使用。
    findings 是純資料變化描述的字串陣列，不包含任何「建議就醫」「疑似」等
    醫療判斷用語——如規格要求，AI 只描述變化，判斷交給家屬/醫療人員。
    """
    findings = []
    for fn in (analyze_pain_trend, analyze_bp_trend):
        result = fn(cur, elder_id)
        if result:
            findings.append(result)

    return {
        "elder_id": elder_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "has_notable_change": len(findings) > 0,
    }
