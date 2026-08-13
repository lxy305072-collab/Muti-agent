# Tools/node_timer.py
import time
from logger_config import get_logger  # 🔥 引入 loguru

logger = get_logger()


def start_node_log(node_name: str, emoji: str, state):
    start_ts = time.perf_counter()
    # 🔥 修改：使用 logger，并加上 emoji 图标
    logger.info(f"{emoji} [{node_name}] 节点开始运行...")
    return start_ts


def end_node_log(node_name: str, emoji: str, start_ts: float, result=None, error=None):
    end_ts = time.perf_counter()
    duration_s = (end_ts - start_ts)

    if error:
        logger.error(f"❌ [{node_name}] 节点运行失败, 耗时: {duration_s:.2f}s, 错误: {error}")
    else:
        logger.info(f"✅ [{node_name}] 节点运行完毕, 耗时: {duration_s:.2f}s")


def print_session_summary():
    """空实现，保留接口防止 server.py 报错"""
    pass


def reset_session_stats():
    """空实现，保留接口防止 server.py 报错"""
    pass