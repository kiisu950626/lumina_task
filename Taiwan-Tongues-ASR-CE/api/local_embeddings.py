# -*- coding: utf-8 -*-
"""
本機文字轉向量（取代 lumina_DB/database/skeleton/ai_client.py 原本呼叫 Gemini
的 get_embedding()）。改用本機模型的理由：
  1. 不吃 Gemini 額度（正規化那步已經常常把一天 20 次用光）
  2. 血壓血糖、症狀描述這類資料不用經過外部 API，本機處理更保守
  3. 「把句子轉成向量」本身是固定的數學轉換，不需要大型語言模型的推論能力，
     用專門做這件事的小模型即可，不用大材小用

模型：intfloat/multilingual-e5-base（MIT 授權，可商用），輸出天生就是 768 維，
跟 lumina_DB schema 的 events.embedding vector(768) 完全對齊，不用截斷/轉換。

e5 系列模型的使用慣例：文字前面要加 "query: " 或 "passage: " 前綴才會有最佳效果——
"passage: " 用於「要被搜尋到」的內容（例如存進資料庫的事件描述），
"query: " 用於「拿去搜尋」的內容（例如之後要查詢相似歷史事件時的查詢句）。
"""
from sentence_transformers import SentenceTransformer

_model = None


def _get_model():
    global _model
    if _model is None:
        print("[本機 Embedding] 載入 intfloat/multilingual-e5-base...")
        _model = SentenceTransformer("intfloat/multilingual-e5-base")
        print("✅ [本機 Embedding] 模型載入完成。")
    return _model


def get_embedding(text: str, is_query: bool = False) -> list[float]:
    """把文字轉成 768 維向量。存進資料庫的事件用預設值(is_query=False，passage前綴)；
    之後要做語意搜尋查詢時，呼叫端要傳 is_query=True(query前綴)。"""
    try:
        prefix = "query: " if is_query else "passage: "
        vec = _get_model().encode(prefix + text)
        return vec.tolist()
    except Exception as e:
        print(f"⚠️ [本機 Embedding 失敗，改存 NULL 不硬塞假向量]: {e}")
        return None
