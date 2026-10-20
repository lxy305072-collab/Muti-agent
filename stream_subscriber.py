# stream_subscriber.py
"""
Redis Stream 订阅器
- 把 SSE 推送从"任务执行"中解耦出来
- 客户端断线重连时，通过 Last-Event-ID 或 ?from= 参数续传
"""
import asyncio
import json
from typing import AsyncGenerator
from Tools.cache_tool import get_redis
from logger_config import get_logger

logger = get_logger()

STREAM_KEY_PREFIX = "task:stream:"
# 客户端断线重连后，如果 stream 长时间无新事件，订阅超时退出
SUBSCRIBE_IDLE_TIMEOUT = 600  # 10 分钟
# XREAD 阻塞时长（毫秒）
XREAD_BLOCK_MS = 2000


def _decode(v):
    """兼容 redis-py 返回 bytes 或 str 的情况"""
    if isinstance(v, bytes):
        return v.decode("utf-8")
    return v


async def subscribe_events(thread_id: str, last_event_id: str = "0") -> AsyncGenerator[str, None]:
    """
    订阅指定任务的事件流。

    :param thread_id: 任务 ID
    :param last_event_id: 起始事件 ID
        - "0"：从 stream 开头读（首次连接，可拿全部历史）
        - "$"：只读新事件
        - 具体 ID（如 "1234-0"）：从该 ID 之后读（断线重连续传）
    """
    r = get_redis()
    stream_key = f"{STREAM_KEY_PREFIX}{thread_id}"
    current_id = last_event_id or "0"
    idle_start = asyncio.get_event_loop().time()

    try:
        while True:
            # XREAD 阻塞读取，2 秒超时
            try:
                messages = await r.xread(
                    {stream_key: current_id},
                    count=100,
                    block=XREAD_BLOCK_MS,
                )
            except Exception as e:
                logger.error(f"⚠️ XREAD 异常: {e}")
                # 退避后重试
                await asyncio.sleep(1)
                continue

            if not messages:
                # 没有新消息：发心跳维持连接 + 检查超时
                idle_time = asyncio.get_event_loop().time() - idle_start
                if idle_time > SUBSCRIBE_IDLE_TIMEOUT:
                    yield f"data: {json.dumps({'type': 'timeout', 'message': '订阅超时，请重新连接'}, ensure_ascii=False)}\n\n"
                    return
                # ⚠️ 必须发真正的 data 事件：SSE 注释行（以 : 开头）会被浏览器直接丢弃，
                #    前端 onmessage 收不到就无法刷新看门狗计时，会被误判为"后端失联"而断连
                yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                continue

            idle_start = asyncio.get_event_loop().time()

            # messages 格式：[(stream_name, [(msg_id, {field: value}), ...])]
            for _stream_name, stream_messages in messages:
                for msg_id, msg_data in stream_messages:
                    msg_id_str = _decode(msg_id)
                    current_id = msg_id_str

                    data_str = _decode(msg_data.get("data") or msg_data.get(b"data") or "{}")

                    # ⚠️ 关键：把 redis stream 的 msg_id 作为 SSE 的 id 字段
                    # 浏览器 EventSource 自动重连时会通过 Last-Event-ID 头带上它
                    yield f"id: {msg_id_str}\ndata: {data_str}\n\n"

                    # 检查是否是终结事件
                    try:
                        event = json.loads(data_str)
                        if event.get("type") in ("finished", "error", "timeout"):
                            return
                    except Exception:
                        pass

    except asyncio.CancelledError:
        # 客户端断开连接时，StreamingResponse 会取消生成器
        logger.info(f"📤 客户端断开对 {thread_id} 的订阅")
        raise
    except Exception as e:
        logger.exception(f"❌ 订阅 {thread_id} 出错: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"