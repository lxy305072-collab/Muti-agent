# logger_config.py
import sys
import uuid
from contextvars import ContextVar
from loguru import logger

# 1. 初始化 ContextVar，用于存储当前请求的唯一 Trace ID
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="N/A")

# 2. 配置 Loguru
logger.remove() # 移除默认配置

# 自定义日志格式：时间 | 级别 | Trace ID | 模块 | 消息
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[trace_id]}</cyan> | "
    "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

# 输出到终端 (带颜色)
logger.add(sys.stdout, format=LOG_FORMAT, level="INFO", colorize=True)

# 输出到文件 (按天轮转，保留 7 天)
logger.add(
    "logs/app_{time:YYYY-MM-DD}.log",
    format=LOG_FORMAT,
    level="DEBUG",
    rotation="00:00", # 每天午夜轮转
    retention="7 days",
    encoding="utf-8"
)

# 3. 注入 patcher，让每条日志自动携带 trace_id
logger.configure(patcher=lambda record: record["extra"].update(trace_id=trace_id_var.get()))

def set_trace_id(tid: str = None):
    """设置当前请求的 Trace ID"""
    if not tid:
        tid = str(uuid.uuid4())[:8] # 取前8位即可
    trace_id_var.set(tid)
    return tid

def get_logger():
    return logger