# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class Settings(BaseSettings):
    # 数据库配置
    POSTGRES_URI: str = "postgresql://myuser:123456@127.0.0.1:5432/marketing_db"

    # Redis 配置
    REDIS_URL: str = "redis://:MySecretPass123@127.0.0.1:6379/0"

    # 阿里云 DashScope 配置
    DASHSCOPE_API_KEY: str = ""
    DASH_API_KEY: str = ""

    # 项目路径配置 (建议在 .env 中配置绝对路径)
    PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
    IMAGE_DIR_NAME: str = "data/images"

    # 模型配置 (方便后续切换)
    MODEL_COPYWRITER: str = "qwen-plus"
    MODEL_IMAGE: str = "wanx-v1"
    MODEL_VISION: str = "qwen-vl-plus"
    MODEL_PROMPT_REWRITER: str = "qwen-turbo"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra='ignore')


# 全局唯一的配置实例
settings = Settings()

# 自动计算图片目录
IMAGE_DIR = os.path.join(settings.PROJECT_ROOT, settings.IMAGE_DIR_NAME)
os.makedirs(IMAGE_DIR, exist_ok=True)