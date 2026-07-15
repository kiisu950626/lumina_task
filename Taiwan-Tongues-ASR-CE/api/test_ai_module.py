from ai_translator import get_ai_analysis
import json

test_cases = [
    "我現在八度了啦",      # 測試：肚子餓
    "我咖逃呼很痛",       # 測試：膝蓋痛 (kha-thâu-hu)
    "阿嬤說她想要兔",     # 測試：想吐 (siūnn-beh thòo)
    "我覺得有一點胃寒"    # 測試：怕冷/發冷 (uì-kuânn)
]

print("🚀 開始執行「未知台語空耳」與「印尼文翻譯」測試...\n")

for text in test_cases:
    print(f"--- 測試輸入: {text} ---")
    result = get_ai_analysis(text)
    
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("❌ AI 未回傳結果")
    print("\n")