# cache_tool.py
import os
import json
import hashlib
import redis.asyncio as redis
from config import settings
from dotenv import load_dotenv

load_dotenv()



redis_pool = redis.ConnectionPool.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1.0,  # 给 1 秒连接时间
    socket_timeout=1.0,          # 读写超时 1 秒
    retry_on_timeout=False
)

def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=redis_pool)

def generate_cache_key(*args) -> str:
    """根据输入参数生成唯一的 MD5 缓存键"""
    raw_str = "_".join([str(arg) for arg in args])
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()