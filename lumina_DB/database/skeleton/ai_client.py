import os
from google import genai
from google.genai import types 
from dotenv import load_dotenv

load_dotenv()

client = genai.Client()

def get_embedding(text: str) -> list[float]:
    """呼叫 Gemini 產生文字的語意向量 (強制轉為 768 維以符合 DB)"""
    
    try:
        # 嘗試呼叫 API
        response = client.models.embed_content(
            model="gemini-embedding-001", 
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=768
            )
        )
        return response.embeddings[0].values
        
    except Exception as e:
        # 如果發生任何錯誤（網路斷線、額度用盡、API 壞掉），就會跑到這裡
        print(f"⚠️ 警告：Gemini API 呼叫失敗 - {e}")
        
        # 回傳 768 個 0.0 的陣列當作「假向量」，讓資料庫寫入不會報錯
        return [0.0] * 768