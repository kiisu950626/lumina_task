import os
from dotenv import load_dotenv

load_dotenv() 

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "curago123"),
    "dbname": os.getenv("DB_NAME", "postgres")
}