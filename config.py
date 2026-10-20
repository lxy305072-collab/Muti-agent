# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from urllib.parse import quote
import os


class Settings(BaseSettings):
    # 数据库配置
    # ⚠️ 凭据不要写进代码仓库，统一放在 .env（见 .env.example）。
    #    默认值留空：未配置时由下方 _validate_required 给出明确报错，而不是静默连上弱口令库。
    #    单一数据源：POSTGRES_URI 由 USER/PASSWORD/HOST/PORT/DB 组装；
    #    若在 .env 中显式写了 POSTGRES_URI，则以它为准（便于临时排查）。
    POSTGRES_URI: str = ""
    POSTGRES_USER: str = "myuser"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "marketing_db"

    # Redis 配置
    # ⚠️ 单一数据源：REDIS_URL 由 HOST/PORT/PASSWORD/DB 组装，避免各处硬编码不一致。
    #    若在 .env 中显式写了 REDIS_URL，则以它为准（便于临时排查）。
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
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
    def _build_postgres_uri(self):
        """用 USER/PASSWORD/HOST/PORT/DB 组装 POSTGRES_URI，保证全项目只有一个数据库配置源。"""
        if not self.POSTGRES_URI:
            user = quote(self.POSTGRES_USER, safe='')
            pwd = (self.POSTGRES_PASSWORD or "").strip()
            auth = f"{user}:{quote(pwd, safe='')}@" if pwd else f"{user}@"
            self.POSTGRES_URI = (
                f"postgresql://{auth}{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return self

    @model_validator(mode="after")
    def _build_redis_url(self):
        """用 HOST/PORT/PASSWORD 组装 REDIS_URL，保证全项目只有一个 Redis 配置源。"""
        if not self.REDIS_URL:
            pwd = (self.REDIS_PASSWORD or "").strip()
            auth = f":{quote(pwd, safe='')}@" if pwd else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return self

    @model_validator(mode="after")
    def _validate_required(self):
        """关键凭据缺失时立刻报错，避免连到弱口令的默认库或以空密码启动。"""
        missing = []
        if not (self.POSTGRES_PASSWORD or "").strip():
            missing.append("POSTGRES_PASSWORD")
        if not (self.REDIS_PASSWORD or "").strip():
            missing.append("REDIS_PASSWORD")
        if missing:
            raise ValueError(
                "缺少必要的环境变量: " + ", ".join(missing)
                + "。请复制 .env.example 为 .env 并填入真实值（cp .env.example .env）。"
            )
        return self


# 全局唯一的配置实例
settings = Settings()

# 自动计算图片目录
IMAGE_DIR = os.path.join(settings.PROJECT_ROOT, settings.IMAGE_DIR_NAME)
os.makedirs(IMAGE_DIR, exist_ok=True)