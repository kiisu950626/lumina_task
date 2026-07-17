# 穿戴裝置資料需求規格（給手機 App / 資料庫團隊）

## 背景

趨勢判斷模組（AI 運算引擎層，`trend_analysis.py`）目前可以描述「疼痛事件次數變化」「血壓趨勢」，但規格要求的另外兩項——**活動量**、**睡眠時數**——目前資料庫沒有對應欄位，需要接上智慧手錶/穿戴裝置資料才能做。

**架構原則**：手錶不會直接連 AI 引擎層或資料庫，資料流程是：

```
智慧手錶 → 手機健康平台（Apple HealthKit / Android Health Connect）
        → 手機 App 讀取後呼叫後端 API
        → 存進資料庫
        → AI 引擎層（trend_analysis.py）讀取、產生趨勢描述
```

手機 App 端建議串接 **Apple HealthKit**（iOS）與 **Google Health Connect**（Android），不要為個別手錶品牌另外接 API——只要手錶有同步到手機內建健康平台，就讀得到，不限廠牌。

## 需要的欄位

活動量、睡眠時數都是「一天一筆的彙總數據」，跟現有 `health_measurements` 表的「單次即時量測」（心跳、血壓）性質不同，建議**另開一張新表**，不要混進 `health_measurements`：

```sql
CREATE TABLE daily_activity (
    id              BIGSERIAL PRIMARY KEY,
    elder_id        UUID NOT NULL REFERENCES elders(id) ON DELETE CASCADE,
    activity_date   DATE NOT NULL,               -- 這筆資料對應哪一天

    daily_steps     INT,                          -- 當日步數
    active_minutes  INT,                          -- 當日活動/運動分鐘數（比步數更能反映活動量）
    sleep_minutes   INT,                          -- 當日睡眠總時數（分鐘）

    data_source     VARCHAR(50) NOT NULL DEFAULT 'unknown',  -- 'apple_health' / 'health_connect' / 'manual'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(elder_id, activity_date)   -- 同一位長者同一天只會有一筆彙總
);

CREATE INDEX idx_daily_activity_elder_date ON daily_activity(elder_id, activity_date);
```

## 手機 App 呼叫後端時，建議傳的資料格式

```json
{
  "elder_id": "33333333-3333-3333-3333-333333333333",
  "activity_date": "2026-07-17",
  "daily_steps": 3200,
  "active_minutes": 25,
  "sleep_minutes": 410,
  "data_source": "apple_health"
}
```

- 三個數值欄位（`daily_steps`/`active_minutes`/`sleep_minutes`）都允許是 `null`——不是每支手錶、每個平台都會提供全部欄位，缺的就留空，不要塞假數字
- `activity_date` 用日期就好，不用到時分秒（睡眠橫跨半夜，用「起床那天」代表整晚睡眠，這是 Apple/Google 健康平台的通用慣例）

## 我（AI 引擎層）之後會做的事

資料進到 `daily_activity` 表之後，我會在 `trend_analysis.py` 補上 `analyze_activity_trend()` / `analyze_sleep_trend()`，邏輯跟現在的疼痛/血壓趨勢一樣（比較本週 vs 前一週平均），輸出風格也一樣是純數字描述，不含醫療判斷。**這部分現在不用做，等資料庫真的有資料再開始。**

## 待確認事項

- [ ] 手機 App 打算先支援 iOS（HealthKit）還是 Android（Health Connect），還是兩個都要
- [ ] `daily_activity` 這張新表的欄位設計，資料庫團隊確認後再實際建表
- [ ] 長者本人的手機，還是照顧者/家屬的手機負責同步資料上傳？（會影響 App 端的權限設計）
