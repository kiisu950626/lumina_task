"""
Seed PostgreSQL with CuraGo mock data from mock-data/ directory.

This version strictly follows the ETL architecture from TransitFlow:
  - Modular JSON loading per entity.
  - High-performance bulk inserts using psycopg2.extras.execute_values.
  - Strict dependency order (Masters -> Children).
  - Proper password hashing fallback integration.
  - 100% Schema alignment (includes all AI, resolution, and status fields).
"""
import hashlib
import json
import os
import secrets
import sys
from typing import Any
from datetime import datetime, timezone

# ── 1. 魔法設定：動態把外層資料夾加入搜尋路徑 (必須放在最前面！) ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)  # 找到上一層的 database 資料夾
sys.path.append(BASE_DIR)   
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_DATA_DIR = os.path.join(BASE_DIR, "mock_data")            # 正式加入 Python 的視野

# ── 2. 現在 Python 看得到 relational 了，可以安心 import ──
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, execute_values
from relational.queries import _hash_password

# ── 3. 設定正確的假資料路徑 (指向 database/mock_data) ──
DATA_DIR = os.path.join(BASE_DIR, "mock_data")

from skeleton.config import DB_CONFIG

def connect():
    # 直接使用引入的 DB_CONFIG
    return psycopg2.connect(**DB_CONFIG)
# ── helpers ──────────────────────────────────────────────────────────────────

def load(filename: str) -> list[dict[str, Any]]:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Data file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{filename} must contain a JSON array/list.")
    return data

def load_optional(filename: str) -> list[dict[str, Any]]:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{filename} must contain a JSON array/list.")
    return data

def connect():
    return psycopg2.connect(**DB_CONFIG)

def insert_many(cur, table: str, columns: list[str], rows: list[tuple]) -> int:
    if not rows:
        return 0
    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES %s ON CONFLICT DO NOTHING").format(
        table=sql.Identifier(table),
        columns=sql.SQL(", ").join(sql.Identifier(col) for col in columns),
    )
    execute_values(cur, query, rows)
    return cur.rowcount

# ── seeders ──────────────────────────────────────────────────────────────────

def seed_users(cur) -> None:
    data = load("users.json")
    columns = ["id", "email", "phone", "password_hash", "full_name", "role", "last_login_at"]
    rows = [
        (
            item.get("id"),
            item.get("email"),
            item.get("phone"),
            _hash_password(item.get("password", "password123")),
            item.get("full_name"),
            item.get("role"),
            item.get("last_login_at")
        )
        for item in data
    ]
    count = insert_many(cur, "users", columns, rows)
    print(f"  users: {count}")

def seed_elders(cur) -> None:
    data = load("elders.json")
    columns = ["id", "name", "birth_date", "emergency_phone"]
    rows = [
        (
            item.get("id"),
            item.get("name"),
            item.get("birth_date"),
            item.get("emergency_phone")
        )
        for item in data
    ]
    count = insert_many(cur, "elders", columns, rows)
    print(f"  elders: {count}")

def seed_user_devices(cur) -> None:
    data = load_optional("user_devices.json")
    columns = ["user_id", "device_name", "device_type", "fcm_token", "last_active_at"]
    rows = [
        (
            item.get("user_id"),
            item.get("device_name"),
            item.get("device_type"),
            item.get("fcm_token"),
            item.get("last_active_at")
        )
        for item in data
    ]
    count = insert_many(cur, "user_devices", columns, rows)
    print(f"  user_devices: {count}")

def seed_care_groups(cur) -> None:
    data = load("care_groups.json")
    columns = ["id", "elder_id", "group_name"]
    rows = [
        (
            item.get("id"),
            item.get("elder_id"),
            item.get("group_name")
        )
        for item in data
    ]
    count = insert_many(cur, "care_groups", columns, rows)
    print(f"  care_groups: {count}")

def seed_group_members(cur) -> None:
    data = load("group_members.json")
    columns = ["group_id", "user_id", "role_in_group"]
    rows = [
        (
            item.get("group_id"),
            item.get("user_id"),
            item.get("role_in_group")
        )
        for item in data
    ]
    count = insert_many(cur, "group_members", columns, rows)
    print(f"  group_members: {count}")

def seed_tasks(cur) -> None:
    data = load_optional("tasks.json")
    columns = ["id", "elder_id", "assigned_to", "task_type", "scheduled_time", "status", "completed_at"]
    rows = [
        (
            item.get("id"),
            item.get("elder_id"),
            item.get("assigned_to"),
            item.get("task_type"),
            item.get("scheduled_time"),
            item.get("status", "pending"),
            item.get("completed_at")
        )
        for item in data
    ]
    count = insert_many(cur, "tasks", columns, rows)
    print(f"  tasks: {count}")

def seed_events(cur) -> None:
    data = load_optional("events.json")
    # 補齊所有的解析與狀態欄位
    columns = [
        "id", "elder_id", "reporter_id", "source_language", "original_text", 
        "normalized_text", "translations", "embedding", "event_type", "severity", 
        "status", "resolved_at", "resolved_by", "resolution_note"
    ]
    rows = [
        (
            item.get("id"),
            item.get("elder_id"),
            item.get("reporter_id"),
            item.get("source_language"),
            item.get("original_text"),
            item.get("normalized_text"),
            Json(item.get("translations", {})),
            item.get("embedding"),
            item.get("event_type"),
            item.get("severity"),
            item.get("status", "pending"),
            item.get("resolved_at"),
            item.get("resolved_by"),
            item.get("resolution_note")
        )
        for item in data
    ]
    count = insert_many(cur, "events", columns, rows)
    print(f"  events: {count}")

def seed_notifications(cur) -> None:
    data = load_optional("notifications.json")
    columns = ["user_id", "event_id", "type", "title", "content", "is_read"]
    rows = [
        (
            item.get("user_id"),
            item.get("event_id"),
            item.get("type", "in_app"),
            item.get("title"),
            item.get("content"),
            item.get("is_read", False)
        )
        for item in data
    ]
    count = insert_many(cur, "notifications", columns, rows)
    print(f"  notifications: {count}")

def seed_chat_messages(cur) -> None:
    data = load_optional("chat_messages.json")
    columns = [
        "group_id", "sender_id", "elder_id", "message_type", "content", 
        "translated_text", "source_language", "target_language", "is_read", "created_at"
    ]
    rows = [
        (
            item.get("group_id"),
            item.get("sender_id"),
            item.get("elder_id"),
            item.get("message_type", "text"),
            item.get("content"), # 對齊 SQL，把 original_text 放到 content
            item.get("translated_text"),
            item.get("source_language"),
            item.get("target_language"),
            item.get("is_read", False),
            item.get("created_at", datetime.now(timezone.utc).isoformat()) # 改用 created_at
        )
        for item in data
    ]
    count = insert_many(cur, "chat_messages", columns, rows)
    print(f"  chat_messages: {count}")
    
def seed_health_measurements(cur) -> None:
    data = load_optional("health_measurements.json")
    # 補齊所有的 AI 推論欄位與資料來源
    columns = [
        "elder_id", "heart_rate", "systolic_bp", "diastolic_bp", "blood_sugar", 
        "meal_context", "data_source","steps", "sleep_hours", "ai_evaluation", "ai_reasoning", "ai_suggestion", "measured_at"
    ]
    rows = [
        (
            item.get("elder_id"),
            item.get("heart_rate"),
            item.get("systolic_bp"),
            item.get("diastolic_bp"),
            item.get("blood_sugar"),
            item.get("meal_context"),
            item.get("data_source", "manual"),
            item.get("steps"),
            item.get("sleep_hours"),
            item.get("ai_evaluation"),
            item.get("ai_reasoning"),
            item.get("ai_suggestion"),
            item.get("measured_at")
        )
        for item in data
    ]
    count = insert_many(cur, "health_measurements", columns, rows)
    print(f"  health_measurements: {count}")

def seed_daily_summaries(cur) -> None:
    data = load_optional("daily_summaries.json")
    columns = ["elder_id", "summary_date", "overall_status", "content"]
    rows = [
        (
            item.get("elder_id"),
            item.get("summary_date"),
            item.get("overall_status"),
            item.get("content")
        )
        for item in data
    ]
    count = insert_many(cur, "daily_summaries", columns, rows)
    print(f"  daily_summaries: {count}")

def print_summary(cur) -> None:
    tables = [
        "users", "elders", "user_devices", "care_groups", "group_members",
        "tasks", "events", "notifications", "chat_messages",
        "health_measurements", "daily_summaries"
    ]
    print("\nCurrent table counts:")
    for table in tables:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        count = cur.fetchone()[0]
        print(f"  {table}: {count}")

def seed_table(cursor, table_name, json_filename):
    """讀取 JSON 檔案並自動匯入到指定的 PostgreSQL 資料表"""
    file_path = os.path.join(MOCK_DATA_DIR, json_filename)
    
    if not os.path.exists(file_path):
        print(f"找不到檔案: {json_filename}，跳過匯入。")
        return

    with open(file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)

    if not data:
        print(f"{json_filename} 裡面沒有資料，跳過匯入。")
        return

    # 自動抓取 JSON 陣列第一筆的 Key 作為資料表欄位
    columns = list(data[0].keys())
    col_string = ", ".join(columns)
    
    # 產生對應數量的 %s 佔位符
    placeholders = ", ".join(["%s"] * len(columns))
    
    # 組裝 SQL 語法 (加上 ON CONFLICT DO NOTHING 避免重複匯入報錯)
    sql = f"INSERT INTO {table_name} ({col_string}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;"
    
    # 整理出所有的值轉換成 tuple 列表
    values = [tuple(row.get(col) for col in columns) for row in data]
    
    try:
        # 批次寫入資料庫
        cursor.executemany(sql, values)
        print(f"成功將 {len(data)} 筆資料匯入到 [{table_name}] 表格！")
    except Exception as e:
        print(f" 匯入 [{table_name}] 失敗，錯誤原因: {e}")
# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("開始執行資料庫灌水腳本 (Seeding)...")
    conn = None
    cur = None
    
    try:
        # 建立資料庫連線
        print("Connecting to PostgreSQL...")
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "lumina"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "admin")
        )
        conn.autocommit = False
        cur = conn.cursor()

        # 依序匯入資料 (主表 -> 子表)
        print("Seeding tables (dependency order):")
        seed_users(cur)
        seed_elders(cur)
        seed_user_devices(cur)
        seed_care_groups(cur)
        seed_group_members(cur)
        seed_tasks(cur)
        seed_events(cur)
        seed_notifications(cur)
        seed_chat_messages(cur)
        seed_health_measurements(cur)
        seed_daily_summaries(cur)

        print_summary(cur)
        conn.commit()
        print("所有資料匯入完畢！")
        print("\nAll done. Database seeded successfully.")

    except Exception as e:
        print(f"嚴重錯誤: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    main()