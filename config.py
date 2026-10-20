# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from urllib.parse import quote
import os


class Settings(BaseSettings):
    # 数据库配置
    POSTGRES_URI: str = "postgresql://myuser:123456@127.0.0.1:5432/marketing_db"

    # Redis 配置
    # ⚠️ 单一数据源：REDIS_URL 由 HOST/PORT/PASSWORD/DB 组装，避免各处硬编码不一致。
    #    若在 .env 中显式写了 REDIS_URL，则以它为准（便于临时排查）。
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "MySecretPass123"
    REDIS_DB: int = 0
    REDIS_URL: str = ""

    # 阿里云 DashScope 配置
    DASHSCOPE_API_KEY: str = ""

    # 项目路径配置 (建议在 .env 中配置绝对路径)
    PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
    IMAGE_DIR_NAME: str = "data/images"

    # 模型配置 (方便后续切换)
    MODEL_COPYWRITER: str = "qwen-plus"
    MODEL_IMAGE: str = "qwen-image-3.0"
    MODEL_VISION: str = "qwen-vl-plus"
    MODEL_PROMPT_REWRITER: str = "qwen-turbo"
    MODEL_RERANK: str = "gte-rerank"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra='ignore')

    @model_validator(mode="after")
    def _build_redis_url(self):
        """用 HOST/PORT/PASSWORD 组装 REDIS_URL，保证全项目只有一个 Redis 配置源。"""
        if not self.REDIS_URL:
            pwd = (self.REDIS_PASSWORD or "").strip()
            auth = f":{quote(pwd, safe='')}@" if pwd else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return self


# 全局唯一的配置实例
settings = Settings()

# 自动计算图片目录
IMAGE_DIR = os.path.join(settings.PROJECT_ROOT, settings.IMAGE_DIR_NAME)
os.makedirs(IMAGE_DIR, exist_ok=True)