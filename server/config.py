"""Port69 v2 - Server Configuration"""
import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    DATABASE_URL: str = "sqlite+aiosqlite:///./port69.db"
    SECRET_KEY: str = "port69-v2-secret-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30  # 30 days
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE: int = 200 * 1024 * 1024  # 200MB
    ALLOWED_EXTENSIONS: list = [
        "jpg","jpeg","png","gif","webp","svg",
        "pdf","txt","md","py","js","ts","go","rs","java","cpp","c","h",
        "mp4","mov","avi","mkv","mp3","wav","ogg",
        "zip","tar","gz","7z",
        "doc","docx","xls","xlsx","csv","json","xml","yaml","toml",
    ]
    RATE_LIMIT_MESSAGES: int = 60       # per minute
    RATE_LIMIT_WINDOW: int = 60
    MAX_MESSAGE_LENGTH: int = 4000
    MAX_ROOM_MEMBERS: int = 500
    ENABLE_BOTS: bool = True
    ENABLE_POLLS: bool = True
    ENABLE_REACTIONS: bool = True
    BOT_TOKEN_PREFIX: str = "bot_"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    S3_BUCKET: Optional[str] = None

    class Config:
        env_file = ".env"


settings = Settings()
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
