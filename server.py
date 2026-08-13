import sys
import asyncio

# 🔥 强制在所有操作之前覆盖 Windows 事件循环策略
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
import asyncpg
import dashscope
from db.db_repo import DatabaseRepo
import uuid
import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi import HTTPException
import json
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from agent.graph_engine import build_graph
from langgraph.types import Command
from Tools.cache_tool import get_redis
from Tools.node_timer import print_session_summary, reset_session_stats
from config import IMAGE_DIR
from logger_config import set_trace_id, get_logger  # 🔥 新增

logger = get_logger()  # 🔥 新增

# 全局唯一的 Graph 实例
DB_URI = os.getenv("POSTGRES_URI", "postgresql://myuser:123456@127.0.0.1:5432/marketing_db")
os.makedirs(IMAGE_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await asyncpg.create_pool(dsn=DB_URI, min_size=2, max_size=10, command_timeout=30)
    app.state.pg_pool = pool
    app.state.db_repo = DatabaseRepo(pool)

    async with build_graph() as graph_app:
        app.state.workflow_app = graph_app
        yield

    await pool.close()


app = FastAPI(title="营销物料生成系统 API", lifespan=lifespan)


# 🔥 全局 Trace ID 中间件
@app.middleware("http")
async def trace_id_middleware(request, call_next):
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
async def start_workflow(req: StartRequest):
    workflow_app = app.state.workflow_app
    r = get_redis()
    client_ip = "test_user"
    limit_key = f"rate_limit:start:{client_ip}"

    try:
        current_count = await r.incr(limit_key)
        if current_count == 1:
            await r.expire(limit_key, 10)
        if int(current_count) > 2:
            raise HTTPException(status_code=429, detail="请求太频繁，请喝杯茶稍后再试")
    except redis.exceptions.TimeoutError:
        pass

    thread_id = str(uuid.uuid4())
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_repo": app.state.db_repo
        }
    }
    initial_state = req.model_dump()

    async def event_generator():
        reset_session_stats()
        yield f"data: {json.dumps({'type': 'init', 'thread_id': thread_id}, ensure_ascii=False)}\n\n"

        try:
            async for event in workflow_app.astream(initial_state, config=config, stream_mode="custom"):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            current_state = await workflow_app.aget_state(config)
            clean_data = clean_state(current_state.values)

            if clean_data.get("title"):
                await app.state.db_repo.upsert_material(
                    thread_id=thread_id,
                    product_name=req.product_name,
                    key_features=req.key_features,
                    target_audience=req.target_audience,
                    title=clean_data["title"],
                    body=clean_data.get("body", ""),
                    image_prompt=clean_data.get("image_prompt", "")
                )

            logger.info("✅ 阶段1生成完毕，工作流已挂起等待人工审核。")

            final_payload = {
                "type": "finished",
                "thread_id": thread_id,
                "status": "interrupted" if current_state.next else "finished",
                "data": clean_data
            }
            yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception(f"❌ /api/start 执行出错: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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


@app.post("/api/resume/{thread_id}")
async def get_resume_workflow(thread_id: str, req: ResumeRequest):
    workflow_app = app.state.workflow_app
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_repo": app.state.db_repo
        }
    }

    if not req.approved:
        resume_input = Command(goto="copywriter", update={"feedback": req.feedback})
    else:
        current_state_before = await workflow_app.aget_state(config)
        if not current_state_before.next:
            return {"status": "error", "msg": "工作流未处于暂停状态或已完结"}
        resume_input = None

    async def event_generator():
        reset_session_stats()
        try:
            if not req.approved:
                yield f"data: {json.dumps({'type': 'log', 'content': '⏳ 收到打回重写指令，正在根据反馈重写文案...'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'log', 'content': '🚀 接收到审核通过指令，准备恢复执行...'}, ensure_ascii=False)}\n\n"

            async for event in workflow_app.astream(resume_input, config=config, stream_mode="custom"):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            current_state = (await workflow_app.aget_state(config)).values
            safe_state = clean_state(current_state)

            if req.approved and safe_state.get("image_url") and safe_state.get("title"):
                try:
                    await app.state.db_repo.finalize_material(
                        thread_id=thread_id,
                        title=safe_state["title"],
                        body=safe_state["body"],
                        image_prompt=safe_state.get("image_prompt", ""),
                        image_url=safe_state["image_url"],
                        review_passed=safe_state.get("review_passed", True),
                        review_feedback=safe_state.get("review_feedback", ""),
                        retry_count=safe_state.get("retry_count", 0)
                    )
                    yield f"data: {json.dumps({'type': 'log', 'content': '💾 物料基础信息已落库，请在完成后进行打分评价。'}, ensure_ascii=False)}\n\n"
                except Exception as db_e:
                    logger.error(f"⚠️ 落库失败: {db_e}")

            logger.info("🎉 工作流全部执行完毕！")
            yield f"data: {json.dumps({'type': 'log', 'content': '🎉 工作流全部执行完毕！'}, ensure_ascii=False)}\n\n"

            final_payload = {
                "type": "finished",
                "thread_id": thread_id,
                "status": "interrupted" if not req.approved else "finished",
                "data": safe_state
            }
            yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception(f"❌ /api/resume 执行出错: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'❌ 执行出错: {str(e)}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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

    uvicorn.run(app, host="0.0.0.0", port=8000, loop="asyncio", log_level="info")