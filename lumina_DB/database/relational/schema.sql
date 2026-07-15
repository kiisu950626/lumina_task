DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS notifications CASCADE;
DROP TABLE IF EXISTS health_measurements CASCADE;
DROP TABLE IF EXISTS daily_summaries CASCADE;
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS group_members CASCADE;
DROP TABLE IF EXISTS care_groups CASCADE;
DROP TABLE IF EXISTS elders CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS partman;
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;
CREATE EXTENSION IF NOT EXISTS pg_cron;


CREATE OR REPLACE FUNCTION update_modified_column()   
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;   
END;
$$ language 'plpgsql';

-- =========================================================================
-- Users 、 Elders and Devices
-- =========================================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    phone           VARCHAR(20),
    password_hash   VARCHAR(255) NOT NULL,
    full_name       VARCHAR(100) NOT NULL,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('family', 'caregiver', 'admin')),

    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ -- 軟刪除
);
CREATE TRIGGER update_users_modtime BEFORE UPDATE ON users FOR EACH ROW EXECUTE PROCEDURE update_modified_column();


CREATE TABLE elders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    birth_date      DATE NOT NULL,
    emergency_phone VARCHAR(20),
    
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE TRIGGER update_elders_modtime BEFORE UPDATE ON elders FOR EACH ROW EXECUTE PROCEDURE update_modified_column();


CREATE TABLE user_devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_name     VARCHAR(100), 
    device_type     VARCHAR(20) CHECK (device_type IN ('ios', 'android', 'web')),
    fcm_token       VARCHAR(255) NOT NULL UNIQUE, 
    
    last_active_at  TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER update_user_devices_modtime 
BEFORE UPDATE ON user_devices 
FOR EACH ROW EXECUTE PROCEDURE update_modified_column();
-- =========================================================================
-- 2.  Groups
-- =========================================================================
CREATE TABLE care_groups (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elder_id        UUID NOT NULL REFERENCES elders(id) ON DELETE CASCADE,
    group_name      VARCHAR(100) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE TABLE group_members (
    group_id        UUID NOT NULL REFERENCES care_groups(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_in_group   VARCHAR(20) NOT NULL CHECK (role_in_group IN ('owner', 'member')),

    PRIMARY KEY (group_id, user_id)
);

-- =========================================================================
-- 3.Tasks, Events, Chat
-- =========================================================================
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elder_id        UUID NOT NULL REFERENCES elders(id) ON DELETE CASCADE,
    -- ON DELETE SET NULL: 如果看護離職(被刪除)，任務依然保留，但指派人變成 NULL(未指派)
    assigned_to     UUID REFERENCES users(id) ON DELETE SET NULL,

    task_type       VARCHAR(50) NOT NULL CHECK (task_type IN ('medication', 'measurement', 'meal', 'hygiene', 'other')),
    scheduled_time  TIMESTAMPTZ NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'missed')),
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE TRIGGER update_tasks_modtime BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE PROCEDURE update_modified_column();


CREATE TABLE events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elder_id        UUID NOT NULL REFERENCES elders(id) ON DELETE CASCADE,
    reporter_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    source_language VARCHAR(10) NOT NULL,
    original_text   TEXT NOT NULL,           
    normalized_text TEXT,               
    translations    JSONB,-- JSONB 格式：適合儲存結構不固定的多國語言對照表 
    embedding       vector(768),
    
    event_type      VARCHAR(50) NOT NULL,
    severity        VARCHAR(20) NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'resolved')),
    resolved_at     TIMESTAMPTZ,
    resolved_by     UUID REFERENCES users(id) ON DELETE RESTRICT,
    resolution_note TEXT,
    
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ 
);
CREATE TRIGGER update_events_modtime BEFORE UPDATE ON events FOR EACH ROW EXECUTE PROCEDURE update_modified_column();


CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_id       UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    receiver_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    elder_id        UUID REFERENCES elders(id) ON DELETE CASCADE,
    
    original_text   TEXT NOT NULL,
    translated_text TEXT,
    source_language VARCHAR(10) NOT NULL,
    target_language VARCHAR(10) NOT NULL,
    
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================================
-- 4.ai measurement
-- =========================================================================
CREATE TABLE health_measurements (
    id               BIGSERIAL PRIMARY KEY,
    elder_id         UUID NOT NULL REFERENCES elders(id) ON DELETE CASCADE,

    heart_rate       INT CHECK (heart_rate > 0),
    systolic_bp      INT,
    diastolic_bp     INT, 
    CONSTRAINT check_bp_logic CHECK (systolic_bp > diastolic_bp), -- 高壓必須大於低壓
    blood_sugar      NUMERIC(5,1) CHECK (blood_sugar > 0),
    meal_context     VARCHAR(20) CHECK (meal_context IN ('before_meal', 'after_meal', 'fasting')),

    data_source      VARCHAR(50) NOT NULL DEFAULT 'manual', --手動輸入還是手錶的
    ai_evaluation    VARCHAR(50),
    ai_reasoning     TEXT,
    ai_suggestion    TEXT,

    measured_at      TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE daily_summaries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elder_id         UUID NOT NULL REFERENCES elders(id) ON DELETE CASCADE,
    summary_date     DATE NOT NULL,
    
    overall_status   VARCHAR(50),
    content          TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(elder_id, summary_date)
);

-- =========================================================================
-- 5. Notifications & Audit Logs
-- =========================================================================
CREATE TABLE notifications (
    id               BIGSERIAL PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id         UUID REFERENCES events(id) ON DELETE CASCADE,
    
    type             VARCHAR(20) NOT NULL DEFAULT 'in_app' CHECK (type IN ('push', 'sms', 'email', 'in_app')),
    title            VARCHAR(100) NOT NULL,                
    content          TEXT NOT NULL,                      
    is_read          BOOLEAN NOT NULL DEFAULT FALSE,              
    
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 稽核表：負責接住所有的修改痕跡，支援醫療糾紛時的歷史追溯
CREATE TABLE audit_logs (
    id               BIGSERIAL,
    table_name       VARCHAR(50) NOT NULL,
    record_id        varchar(50) NOT NULL,
    action           VARCHAR(10) NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')), 
    old_data         JSONB, -- 存成 JSONB 以相容所有不同資料表的欄位格式
    new_data         JSONB,
    changed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, changed_at)
)PARTITION BY RANGE (changed_at);

SELECT partman.create_parent(
    p_parent_table => 'public.audit_logs',
    p_control => 'changed_at',
    p_interval => '1 month',
    p_premake => 3
);

UPDATE partman.part_config 
SET infinite_time_partitions = true,
    retention = '24 months',
    retention_keep_table = false -- 設為 false 會直接 DROP 超過兩年的實體表
WHERE parent_table = 'public.audit_logs';

-- 5. 註冊 pg_cron 背景排程，每天凌晨自動跑一次維護作業
SELECT cron.schedule('@daily', $$CALL partman.run_maintenance()$$);
-- ==========================================
-- 6.軟刪除時的連帶反應
-- ==========================================
CREATE OR REPLACE FUNCTION cascade_soft_delete_elder()
RETURNS TRIGGER AS $$
BEGIN
    -- 核心邏輯：只有當 deleted_at 從「空值」變成「有時間」時，才觸發連帶反應
    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN
        
        -- 1. 連帶軟刪除該長者的所有異常事件
        UPDATE events 
        SET deleted_at = NEW.deleted_at, updated_at = NOW()
        WHERE elder_id = NEW.id AND deleted_at IS NULL;
        
        -- 2. 連帶軟刪除該長者的所有任務
        UPDATE tasks 
        SET deleted_at = NEW.deleted_at, updated_at = NOW()
        WHERE elder_id = NEW.id AND deleted_at IS NULL;
        
    END IF;

    IF NEW.deleted_at IS NULL AND OLD.deleted_at IS NOT NULL THEN
        UPDATE events SET deleted_at = NULL, updated_at = NOW() WHERE elder_id = NEW.id;
        UPDATE tasks SET deleted_at = NULL, updated_at = NOW() WHERE elder_id = NEW.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_cascade_soft_delete_elder
AFTER UPDATE ON elders
FOR EACH ROW
EXECUTE PROCEDURE cascade_soft_delete_elder();


-- =========================================================================
-- 7. Indexes
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_events_embedding ON events USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_events_translations_gin ON events USING GIN (translations);
CREATE INDEX IF NOT EXISTS idx_audit_logs_new_data_gin ON audit_logs USING GIN (new_data);
CREATE INDEX IF NOT EXISTS idx_care_groups_elder_id ON care_groups(elder_id);
CREATE INDEX IF NOT EXISTS idx_group_members_user_id ON group_members(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_elder_id ON tasks(elder_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_schedule ON tasks(elder_id, scheduled_time);
CREATE INDEX IF NOT EXISTS idx_events_elder_id ON events(elder_id);
CREATE INDEX IF NOT EXISTS idx_events_reporter_id ON events(reporter_id);
CREATE INDEX IF NOT EXISTS idx_events_resolved_by ON events(resolved_by);
CREATE INDEX IF NOT EXISTS idx_events_type_status ON events(event_type, status);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_participants ON chat_messages(sender_id, receiver_id);
CREATE INDEX IF NOT EXISTS idx_chat_elder_id ON chat_messages(elder_id);
CREATE INDEX IF NOT EXISTS idx_chat_sent_at ON chat_messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_health_elder_id ON health_measurements(elder_id);
CREATE INDEX IF NOT EXISTS idx_health_measured_at ON health_measurements(elder_id, measured_at);
CREATE INDEX IF NOT EXISTS idx_daily_summaries_date ON daily_summaries(summary_date);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notifications_event_id ON notifications(event_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_record_id ON audit_logs(record_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_table_name ON audit_logs(table_name);
CREATE INDEX IF NOT EXISTS idx_user_devices_user_id ON user_devices(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_elder_status ON tasks(elder_id, status);



-- =========================================================================
-- 8. Audit Logs Trigger 
-- =========================================================================
CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        INSERT INTO audit_logs (table_name, record_id, action, old_data)
        VALUES (TG_TABLE_NAME, OLD.id::varchar, 'DELETE', row_to_json(OLD)::jsonb);
        RETURN OLD;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO audit_logs (table_name, record_id, action, old_data, new_data)
        VALUES (TG_TABLE_NAME, NEW.id::varchar, 'UPDATE', row_to_json(OLD)::jsonb, row_to_json(NEW)::jsonb);
        RETURN NEW;
    ELSIF (TG_OP = 'INSERT') THEN
        INSERT INTO audit_logs (table_name, record_id, action, new_data)
        VALUES (TG_TABLE_NAME, NEW.id::varchar, 'INSERT', row_to_json(NEW)::jsonb);
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- 將稽核功能綁定在最關鍵的異常事件表 (events)
CREATE TRIGGER audit_events_trigger
AFTER INSERT OR UPDATE OR DELETE ON events
FOR EACH ROW EXECUTE PROCEDURE audit_trigger_func();

-- 將稽核功能綁定在「任務表」(追蹤誰完成或漏掉了照護任務)
CREATE TRIGGER audit_tasks_trigger
AFTER INSERT OR UPDATE OR DELETE ON tasks
FOR EACH ROW EXECUTE PROCEDURE audit_trigger_func();

-- 將稽核功能綁定在「人員表」(追蹤密碼變更或權限修改)
CREATE TRIGGER audit_users_trigger
AFTER INSERT OR UPDATE OR DELETE ON users
FOR EACH ROW EXECUTE PROCEDURE audit_trigger_func();

-- 將稽核功能綁定在「長者表」(追蹤重要聯絡人或基本資料修改)
CREATE TRIGGER audit_elders_trigger
AFTER INSERT OR UPDATE OR DELETE ON elders
FOR EACH ROW EXECUTE PROCEDURE audit_trigger_func();