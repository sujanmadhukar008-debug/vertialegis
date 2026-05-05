from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./ccms.db"
    UPLOAD_DIR: str = "./uploads"
    OCR_ENABLED: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
Path(settings.UPLOAD_DIR).mkdir(exist_ok=True)
