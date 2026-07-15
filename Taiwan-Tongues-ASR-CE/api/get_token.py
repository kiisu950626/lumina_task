import jwt
import os

# 使用你伺服器預設的密鑰與演算法
secret = os.getenv("ASR_API_JWT_SECRET", "CHANGE_ME_SECRET")
algorithm = os.getenv("ASR_API_JWT_ALGORITHM", "HS256")

# 偽造一個測試用的使用者
token = jwt.encode({"sub": "test_grandpa"}, secret, algorithm=algorithm)

print("\n🎫 你的專屬測試 Token 是：\n")
print(token)
print("\n")