# server.py
import asyncio
import sys
# 🔥 强制在所有操作之前覆盖 Windows 事件循环策略
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from dotenv import load_dotenv
load_dotenv()

import os
import asyncpg
import dashscope
from db.db_repo import DatabaseRepo
import uuid
import redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi import HTTPException
import json
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from Tools.cache_tool import get_redis
from task_manager import task_manager
from stream_subscriber import subscribe_events
from config import IMAGE_DIR, settings
from logger_config import set_trace_id, get_logger
from agent.graph_engine import build_graph
from langgraph.types import Command

logger = get_logger()

DB_URI = os.getenv("POSTGRES_URI", settings.POSTGRES_URI)
os.makedirs(IMAGE_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await asyncpg.create_pool(dsn=DB_URI, min_size=2, max_size=10, command_timeout=30)
    app.state.pg_pool = pool
    app.state.db_repo = DatabaseRepo(pool)

    # 🔥 核心修复：在 lifespan 中启动 graph
    async with build_graph() as graph_app:
        app.state.workflow_app = graph_app
        yield

    await pool.close()


app = FastAPI(title="营销物料生成系统 API", lifespan=lifespan)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    # 🔥 核心修复：如果是静态图片请求，直接放行，不打印日志，避免刷屏
    if request.url.path.startswith("/images/"):
        return await call_next(request)
    tid = request.headers.get("X-Trace-ID", set_trace_id())
    set_trace_id(tid)
    logger.info(f"📥 收到请求: {request.method} {request.url.path}")
    response = await call_next(request)
    response.headers["X-Trace-ID"] = tid
    logger.info(f"📤 请求完成: Status {response.status_code}")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")


class StartRequest(BaseModel):
    product_name: str
    key_features: str
    target_audience: str
    platform: str = "general"


class ResumeRequest(BaseModel):
    approved: bool
    feedback: str = ""


class RateRequest(BaseModel):
    thread_id: str
    score: int


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown_client"


def clean_state(obj):
    if isinstance(obj, dict):
        return {k: clean_state(v) for k, v in obj.items() if k != "messages"}
    elif isinstance(obj, list):
        return [clean_state(v) for v in obj]
    elif hasattr(obj, 'model_dump'):
        return clean_state(obj.model_dump())
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    else:
        return str(obj)


async def get_embedding(text: str) -> list:
    rsp = await asyncio.to_thread(
        dashscope.TextEmbedding.call,
        model="text-embedding-v3",
        input=text,
        dimension=1024,
        output_type="dense"
    )
    if rsp.status_code == 200:
        return rsp.output["embeddings"][0]["embedding"]
    return []


@app.post("/api/start")
async def start_workflow(req: StartRequest, request: Request):
    workflow_app = app.state.workflow_app
    r = get_redis()
    client_ip = get_client_ip(request)
    limit_key = f"rate_limit:start:{client_ip}"

    # try:
    #     current_count = await r.incr(limit_key)
    #     if current_count == 1:
    #         await r.expire(limit_key, 10)
    #     if int(current_count) > 2:
    #         logger.warning(f"⚠️ IP {client_ip} 触发限流")
    #         raise HTTPException(status_code=429, detail="请求太频繁，请喝杯茶稍后再试")
    # except redis.exceptions.TimeoutError:
    #     pass
    # except HTTPException:
    #     raise
    # except Exception as e:
    #     logger.error(f"⚠️ 限流逻辑异常: {e}")

    thread_id = str(uuid.uuid4())
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_repo": app.state.db_repo
        }
    }
    initial_state = req.model_dump()

    # 🔥 修改点：删除原来的 async def event_generator() 和 StreamingResponse
    # 改为：启动后台任务，立即返回 thread_id
    started = await task_manager.start_workflow_task(
        workflow_app=workflow_app,
        initial_state=initial_state,
        config=config,
        thread_id=thread_id,
        req_data=req.model_dump(),
        db_repo=app.state.db_repo,
    )

    if not started:
        raise HTTPException(status_code=409, detail="任务启动失败（可能已在运行）")

    # 返回 thread_id，前端用它去订阅 GET /api/stream/{thread_id}
    return {"thread_id": thread_id, "status": "started"}


@app.post("/api/resume/{thread_id}")
async def get_resume_workflow(thread_id: str, req: ResumeRequest):
    workflow_app = app.state.workflow_app
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_repo": app.state.db_repo
        }
    }

    # 🔥 修改点：删除原来的 if not req.approved 判断和 async def event_generator()
    # 改为：启动后台任务，立即返回
    started = await task_manager.resume_workflow_task(
        workflow_app=workflow_app,
        config=config,
        thread_id=thread_id,
        req=req,
        db_repo=app.state.db_repo,
    )

    if not started:
        raise HTTPException(status_code=409, detail="任务恢复失败（未处于暂停状态或已在运行）")

    return {"thread_id": thread_id, "status": "resumed"}


@app.get("/api/stream/{thread_id}")
async def stream_events(thread_id: str, request: Request):
    """
    SSE 订阅任务事件流（支持断线重连）。

    断线重连机制：
    - 浏览器 EventSource 自动重连时，会自动带上 Last-Event-ID 请求头
    - 也可通过 ?from=<msg_id> 手动指定起始位置（用于前端主动重连场景）

    使用示例（前端）：
        const es = new EventSource('/api/stream/' + threadId);
        es.onmessage = (e) => { ... };
        // 服务端在每条消息前会发 id: <msg_id>，浏览器自动记录 lastEventId
    """
    # 校验任务是否存在
    status = await task_manager.get_status(thread_id)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    # 优先用 query 参数 from，其次用 Last-Event-ID 头，最后默认 "0"（读全部历史）
    last_id = (
            request.query_params.get("from")
            or request.headers.get("Last-Event-ID")
            or "0"
    )

    return StreamingResponse(
        subscribe_events(thread_id, last_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲，保证 SSE 实时推送
        },
    )


@app.get("/api/tasks/{thread_id}/status")
async def get_task_status_api(thread_id: str):
    """查询任务当前状态（用于前端轮询兜底）"""
    status = await task_manager.get_status(thread_id)
    if not status:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return status




@app.get("/api/status/{thread_id}")
async def get_status(thread_id: str):
    workflow_app = app.state.workflow_app
    config = {"configurable": {"thread_id": thread_id}}
    current_state = await workflow_app.aget_state(config)
    clean_data = clean_state(current_state.values) if current_state else {}
    return {
        "status": "interrupted" if current_state.next else "finished",
        "next_nodes": current_state.next if current_state else [],
        "data": clean_data,
    }


@app.get("/api/history")
async def get_history():
    try:
        history_list = await app.state.db_repo.list_recent(limit=24)
        return history_list
    except Exception as e:
        logger.error(f"⚠️ 查询历史失败: {e}")
        return []


@app.delete("/api/history/{thread_id}")
async def delete_history_item(thread_id: str):
    try:
        await app.state.db_repo.delete_material(thread_id)
        await app.state.pg_pool.execute("DELETE FROM checkpoints WHERE thread_id = $1", thread_id)
        await app.state.pg_pool.execute("DELETE FROM checkpoint_writes WHERE thread_id = $1", thread_id)
        r = get_redis()
        await r.zrem("history:materials", thread_id)
        return {"status": "success", "msg": f"已删除{thread_id}"}
    except Exception as e:
        logger.error(f"⚠️ 删除历史失败: {e}")
        return {"status": "error", "msg": str(e)}


@app.post("/api/rate")
async def rate_material(req: RateRequest):
    try:
        if not req.thread_id:
            return {"status": "error", "msg": "thread_id 不能为空"}

        await app.state.db_repo.update_quality_score(req.thread_id, req.score)

        if req.score >= 90:
            exists = await app.state.db_repo.check_embedding_exists(req.thread_id)
            if not exists:
                mat_data = await app.state.db_repo.get_material_for_embedding(req.thread_id)
                if mat_data and mat_data.get('title') and mat_data.get('body'):
                    emb_text = f"{mat_data['title']}\n{mat_data['body']}"
                    embedding = await get_embedding(emb_text)
                    if embedding:
                        await app.state.db_repo.save_embedding(
                            thread_id=req.thread_id,
                            title=mat_data['title'],
                            body=mat_data['body'],
                            audience="",
                            embedding=embedding
                        )
                        return {"status": "success", "msg": "评分成功，物料已自动沉淀至标准范例库！"}
                    else:
                        return {"status": "success", "msg": "评分成功，但向量化获取失败。"}
                else:
                    return {"status": "success", "msg": "评分成功，但数据库缺失物料文本，未写入范例库。"}
            else:
                return {"status": "success", "msg": "评分成功！该优质物料已在标准范例库中。"}
        else:
            return {"status": "success", "msg": f"评分成功，当前评分 {req.score} 分，未进入标准范例库。"}
    except Exception as e:
        logger.exception(f"❌ /api/rate 内部错误: {str(e)}")
        return {"status": "error", "msg": f"后端内部错误: {str(e)}"}


if __name__ == "__main__":
    import uvicorn

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )

    server = uvicorn.Server(config)

    # Windows + psycopg 异步模式必须使用 SelectorEventLoop
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()