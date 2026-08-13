# Tools/node_timer.py
import time
import sys


def _print(msg):
    """强制立即输出并刷新缓冲区"""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def start_node_log(node_name: str, emoji: str, state):
    start_ts = time.perf_counter()
    start_wall = time.strftime("%H:%M:%S")
    _print(f"[{start_wall}] {node_name} 节点开始运行...")
    return start_ts


def end_node_log(node_name: str, emoji: str, start_ts: float, result=None, error=None):
    end_ts = time.perf_counter()
    duration_s = (end_ts - start_ts)
    end_wall = time.strftime("%H:%M:%S")

    if error:
        _print(f"[{end_wall}] {node_name} 节点运行失败, 耗时: {duration_s:.2f}s, 错误: {error}")
    else:
        _print(f"[{end_wall}] {node_name} 节点运行完毕, 耗时: {duration_s:.2f}s")


def print_session_summary():
    """空实现，保留接口防止 server.py 报错"""
    pass


def reset_session_stats():
    """空实现，保留接口防止 server.py 报错"""
    pass