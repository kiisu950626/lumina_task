from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# 匯入你已經寫好的神兵利器
from skeleton.ai_client import get_embedding
from relational.queries import (
    execute_create_event, 
    query_similar_historical_events,
    execute_record_health_measurement,
    query_recent_health_measurements,
    query_daily_pending_tasks,
    execute_complete_task,
    get_user_profile,
    get_elder_profile,
    get_user_notifications,
    mark_notification_read,
    get_group_messages,
    register_user_device,
    register_user,
    login_user,
    query_authorized_elders,
    insert_chat_message,

)

# 建立 FastAPI 應用程式
app = FastAPI(title="Lumina 照護系統 API", description="結合 Gemini 向量搜尋的醫療照護後端")

# 加入 CORS 中介軟體，允許前端跨域連線
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 定義前端傳送過來的資料格式 (Pydantic Models)
# ==========================================
class EventCreateRequest(BaseModel):
    elder_id: str
    user_id: str
    language: str = "zh-TW"
    original_text: str
    category: str
    severity: str

class UserRegisterRequest(BaseModel):
    email: str
    phone: str
    full_name: str
    password: str
    role: str = "family" 
class UserLoginRequest(BaseModel):
    email: str
    password: str

class EventSearchRequest(BaseModel):
    elder_id: str
    query_text: str
    limit: int = 5

class HealthMetricCreate(BaseModel):
    elder_id: str
    data_source: str = "manual"
    heart_rate: Optional[int] = None
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    blood_sugar: Optional[float] = None
    meal_context: Optional[str] = None

class TaskUpdate(BaseModel):
    status: str  # 例如: "completed", "missed"

class DeviceRegisterRequest(BaseModel):
    user_id: str
    device_token: str
    platform: str # "ios", "android", "web"

class ChatMessageRequest(BaseModel):
    sender_id: str
    message_type: str # "text", "image", "audio"
    content: str

# ==========================================
# 實作 API 路由 (Routes)
# ==========================================

@app.get("/")
def root():
    return {"message": "Lumina API 運行中！請前往 /docs 查看文件"}

# --- Events API ---
@app.post("/api/v1/events", summary="新增照護事件")
def create_event(req: EventCreateRequest):
    try:
        embedding = get_embedding(req.original_text)
        success, result = execute_create_event(
            req.elder_id, req.user_id, req.language, req.original_text, 
            req.category, req.severity, embedding
        )
        if not success: raise Exception(result)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/events/search", summary="AI 語意搜尋歷史事件")
def search_events(req: EventSearchRequest):
    try:
        embedding = get_embedding(req.query_text)
        results = query_similar_historical_events(req.elder_id, embedding, limit=req.limit)
        return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Health API ---
@app.post("/api/v1/health", summary="上傳健康數據")
def upload_health_metric(req: HealthMetricCreate):
    try:
        now_str = datetime.utcnow().isoformat()
        success, result = execute_record_health_measurement(
            req.elder_id, now_str, req.data_source, 
            req.heart_rate, req.systolic_bp, req.diastolic_bp, 
            req.blood_sugar, req.meal_context
        )
        if not success: raise Exception(result)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/health/{elder_id}", summary="取得健康數據歷史")
def get_health_history(elder_id: str, limit: int = 10):
    try:
        data = query_recent_health_measurements(elder_id, limit)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Tasks API ---
@app.get("/api/v1/tasks/{elder_id}", summary="取得長者今日任務")
def get_tasks(elder_id: str):
    try:
        data = query_daily_pending_tasks(elder_id)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/v1/tasks/{task_id}", summary="更新任務狀態")
def update_task(task_id: str, req: TaskUpdate):
    try:
        if req.status == "completed":
            success, result = execute_complete_task(task_id)
            if not success: raise Exception(result)
            return {"status": "success", "data": result}
        return {"status": "failed", "message": "Currently only supports 'completed' status"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Users & Elders API ---
@app.get("/api/v1/users/{user_id}", summary="取得使用者資料")
def get_user(user_id: str):
    data = get_user_profile(user_id)
    if not data: raise HTTPException(status_code=404, detail="User not found")
    return {"status": "success", "data": data}

@app.get("/api/v1/elders/{elder_id}", summary="取得長者基本資料")
def get_elder(elder_id: str):
    data = get_elder_profile(elder_id)
    if not data: raise HTTPException(status_code=404, detail="Elder not found")
    return {"status": "success", "data": data}

# --- Notifications API ---
@app.get("/api/v1/notifications/{user_id}", summary="取得通知列表")
def get_notifications(user_id: str):
    data = get_user_notifications(user_id)
    return {"status": "success", "data": data}

@app.patch("/api/v1/notifications/{notification_id}/read", summary="標記通知為已讀")
def read_notification(notification_id: str):
    success = mark_notification_read(notification_id)
    if not success: raise HTTPException(status_code=400, detail="Failed to mark notification")
    return {"status": "success", "message": "已標記為已讀"}

# --- Chat Messages API ---
@app.get("/api/v1/chat/{group_id}/messages", summary="取得群組歷史訊息")
def get_chat_history(group_id: str):
    data = get_group_messages(group_id)
    return {"status": "success", "data": data}

@app.post("/api/v1/chat/{group_id}/messages", summary="發送新訊息")
def send_chat_message(group_id: str, req: ChatMessageRequest):
    try:
        success, result = insert_chat_message(group_id, req.sender_id, req.message_type, req.content)
        if not success:
            raise HTTPException(status_code=400, detail=result)
        return {"status": "success", "message": "訊息已成功發送", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# --- User Devices API ---
@app.post("/api/v1/devices/register", summary="註冊推播裝置")
def register_device(req: DeviceRegisterRequest):
    success = register_user_device(req.user_id, req.device_token, req.platform)
    if not success: raise HTTPException(status_code=400, detail="Failed to register device")
    return {"status": "success", "message": "裝置註冊成功"}


@app.post("/api/v1/auth/register", summary="使用者註冊")
def register(req: UserRegisterRequest):
    try:
        success, result = register_user(req.email, req.phone, req.full_name, req.password, req.role)
        if not success:
            raise HTTPException(status_code=400, detail=result)
        return {"status": "success", "message": "註冊成功", "user_id": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/auth/login", summary="使用者登入")
def login(req: UserLoginRequest):
    try:
        user_data = login_user(req.email, req.password)
        if not user_data:
            raise HTTPException(status_code=401, detail="信箱或密碼錯誤")
        return {"status": "success", "message": "登入成功", "data": user_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/users/{user_id}/elders", summary="取得使用者有權限查看的長者清單")
def get_authorized_elders(user_id: str):
    try:
        data = query_authorized_elders(user_id)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))