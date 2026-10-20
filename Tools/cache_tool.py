# cache_tool.py
import os
import json
import hashlib
from dotenv import load_dotenv

# ⚠️ 必须先加载 .env，再 import config —— 否则 settings 读不到最新环境变量
load_dotenv()

import redis.asyncio as redis
from config import settings


redis_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5.0,  # 给 1 秒连接时间
    socket_timeout=10.0,          # 读写超时 1 秒
    retry_on_timeout=False
)

def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=redis_pool)

def generate_cache_key(*args) -> str:
    """根据输入参数生成唯一的 MD5 缓存键"""
    raw_str = "_".join([str(arg) for arg in args])
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()