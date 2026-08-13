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

# 全局唯一的 Graph 实例（真实生产环境可替换为 PostgresSaver）
DB_URI = os.getenv("POSTGRES_URI", "postgresql://myuser:123456@127.0.0.1:5432/marketing_db")
# PROJECT_ROOT = "E:/xt/muti-agent_dm1"
# IMAGE_DIR = os.path.join(PROJECT_ROOT,"data","images")
os.makedirs(IMAGE_DIR, exist_ok=True)
# 🔥 使用 FastAPI 的 lifespan 来管理异步上下文
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 初始化 PostgreSQL 连接池
    pool = await asyncpg.create_pool(dsn=DB_URI, min_size=2, max_size=10, command_timeout=30)
    app.state.pg_pool = pool
    app.state.db_repo = DatabaseRepo(pool)

    # 2. 初始化 LangGraph 工作流
    async with build_graph() as graph_app:
        app.state.workflow_app = graph_app
        yield

    await pool.close()


app = FastAPI(title="营销物料生成系统 API", lifespan=lifespan)

# 允许跨域（Streamlit 默认端口是 8501，FastAPI 是 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")
# ================= 请求/响应 数据模型 =================
class StartRequest(BaseModel):
    product_name: str
    key_features: str
    target_audience: str
    platform: str = "general"  # 🔥 新增：默认通用风格

class ResumeRequest(BaseModel):
    approved: bool
    feedback: str = ""

# 🔥 新增：打分请求模型
class RateRequest(BaseModel):
    thread_id: str
    score: int  # 0 - 100


# ================= 全局工具函数 =================
def clean_state(obj):
    """强制将 State 转换为纯 JSON 兼容格式，洗掉无法序列化的对象"""
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
    """调用 DashScope 获取文本向量"""
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

# ================= API 接口 =================
@app.post("/api/start")
async def start_workflow(req: StartRequest):
    """启动工作流，执行到中断点前暂停(SSE)"""
    workflow_app = app.state.workflow_app
    r = get_redis()
    #基于IP的10秒内防限流
    client_ip = "test_user"  # 实际可从 request.client.host 获取
    limit_key = f"rate_limit:start:{client_ip}"

    try:
        current_count = await r.incr(limit_key)
        if current_count == 1:
            # 第一次访问，设置 10 秒过期时间
            await r.expire(limit_key, 10)

        if int(current_count) > 2:  # 10秒内只允许发起 2 次生成
            raise HTTPException(status_code=429, detail="请求太频繁，请喝杯茶稍后再试")
    except redis.exceptions.TimeoutError:
        pass  # Redis 挂了不影响主流程，直接放行


    thread_id = str(uuid.uuid4())
    # 🔥 核心修改：将 db_repo 注入到 configurable 中
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_repo": app.state.db_repo  # 关键点！
        }
    }
    initial_state = req.model_dump()

    async def event_generator():
        # 🔥 新增：重置节点统计
        reset_session_stats()
        # 🔥 优化 5：使用标准 SSE 格式推送
        yield f"data: {json.dumps({'type': 'init', 'thread_id': thread_id}, ensure_ascii=False)}\n\n"

        try:
            # 2. 流式执行图
            # stream_mode="messages" 获取LLM token流

            async for event in workflow_app.astream(initial_state,config=config,stream_mode="custom"):

                # 将后端 writer 推送的自定义事件，原样转发给前端
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


            # 3. 图执行到中断点暂停，获取当前状态
            current_state =await workflow_app.aget_state(config)
            # 调用你文档中提到的 clean_state 防止序列化报错
            clean_data = clean_state(current_state.values)

            # 🔥 新增：文案生成完毕后，先存入数据库
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

            # 🔥 新增：打印耗时汇总表到终端
            print_session_summary()


            # 4. 推送最终状态数据
            final_payload = {
                "type": "finished",
                "thread_id": thread_id,
                "status": "interrupted" if current_state.next else "finished",
                "data": clean_data
            }
            yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 异常兜底
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
    # 返回流式响应
    return StreamingResponse(event_generator(), media_type="text/event-stream")



@app.get("/api/status/{thread_id}")
async def get_status(thread_id: str):
    """查询当前工作流状态"""
    workflow_app = app.state.workflow_app
    config = {"configurable":{"thread_id": thread_id}}
    current_state =await workflow_app.aget_state(config)

    # 同样需要 clean_state 防止 Pydantic 对象报错
    clean_data = clean_state(current_state.values) if current_state else {}
    return {
        "status":"interrupted" if current_state.next else "finished",
        "next_nodes": current_state.next if current_state else [],
        "data": clean_data,
    }




@app.get("/api/history")
async def get_history():
    """🔥 核心改造：直接查 PostgreSQL"""
    try:
        history_list = await app.state.db_repo.list_recent(limit=24)
        return history_list
    except Exception as e:
        print(f"⚠️ 查询历史失败: {e}")
        return []


#删除指定物料的接口
@app.delete("/api/history/{thread_id}")
async def delete_history_item(thread_id: str):

    """🔥 核心改造：删除 PG 数据和 checkpoint"""
    try:
        # 1. 删除业务表数据 (会级联删除向量表)
        await app.state.db_repo.delete_material(thread_id)

        # 2. 删除 LangGraph 在 PG 中保存的 checkpoint 记录
        # (如果这两行有红线，不用管，这是 LangGraph 动态生成的表，或者加上 # noinspection SqlResolve)
        # noinspection SqlResolve
        await app.state.pg_pool.execute("DELETE FROM checkpoints WHERE thread_id = $1", thread_id)
        # noinspection SqlResolve
        await app.state.pg_pool.execute("DELETE FROM checkpoint_writes WHERE thread_id = $1", thread_id)

        # 3. 清理 Redis 缓存
        r = get_redis()
        await r.zrem("history:materials", thread_id)

        return {"status": "success", "msg": f"已删除{thread_id}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}






@app.post("/api/resume/{thread_id}")
async def get_resume_workflow(thread_id: str,req: ResumeRequest):
    """恢复工作流异步执行(SSE流式版）"""
    workflow_app = app.state.workflow_app
    # 🔥 核心修改：同样注入 db_repo
    config = {
        "configurable": {
            "thread_id": thread_id,
            "db_repo": app.state.db_repo  # 关键点！
        }
    }

    if not req.approved:
        resume_input = Command(goto="copywriter", update={"feedback": req.feedback})
    else:
        # 🔥 核心修复 1：恢复前先检查状态，确认确实停在中断点
        current_state_before = await workflow_app.aget_state(config)
        if not current_state_before.next:
            return {"status": "error", "msg": "工作流未处于暂停状态或已完结"}
        resume_input = None

    # # 🔥 TODO 1: 如果用户打回重写，跳转至 copywriter 节点并传入 feedback
    # if not req.approved:
    #     resume_input = Command(goto="copywriter", update={"review_feedback": req.feedback})
    # else:
    #     resume_input = None  # 正常放行

    #定义SSE流式生成器
    async def event_generator():
        # 🔥 新增：重置节点统计
        reset_session_stats()
        try:
            if not req.approved:
                yield f"data: {json.dumps({'type': 'log', 'content': '⏳ 收到打回重写指令，正在根据反馈重写文案...'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'log', 'content': '🚀 接收到审核通过指令，准备恢复执行...'}, ensure_ascii=False)}\n\n"


                    # yield f"data: {json.dumps({'type': 'log', 'content': msg}, ensure_ascii=False)}\n\n"
            # 🔥 TODO 4: 使用 stream_mode="custom" 捕获 writer 发送的详细日志
            async for event in workflow_app.astream(resume_input, config=config, stream_mode="custom"):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            #工作流跑完，获取最终状态
            current_state =(await workflow_app.aget_state(config)).values
            safe_state  = clean_state(current_state)

            # # 🔥 核心新增：如果审核通过且生成了有效图片，记录到 Redis ZSet 中
            # if req.approved and safe_state.get("image_url") and safe_state.get("title"):
            #     r = get_redis()
            #     # 以当前时间戳作为 score 写入 ZSet
            #     await r.zadd("history:materials", {thread_id: time.time()})
            #
            #     # 自动清理：仅保留最新的 6 个 (倒序拿第 7 个开始的所有元素，然后删除)
            #     all_items = await r.zrevrange("history:materials", 0, -1)
            #     if len(all_items) > 6:
            #         old_items = all_items[6:]
            #         if old_items:
            #             await r.zrem("history:materials", *old_items)
            #             # 顺便把过期的老数据从 SQLite 里也清掉，防止数据库膨胀
            #             for old_id in old_items:
            #                 execute_delete_thread(old_id)
            # 🔥 核心新增：工作流执行完毕，落库与向量化

            if req.approved and safe_state.get("image_url") and safe_state.get("title"):
                try:
                    # 1. 仅存入主表 (不提前向量化了，等用户打分 >=90 再写入)
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
                    print(f"⚠️ 落库失败: {db_e}")

            # 🔥 新增：打印耗时汇总表到终端
            print_session_summary()
            yield f"data: {json.dumps({'type': 'log', 'content': '🎉 工作流全部执行完毕！'}, ensure_ascii=False)}\n\n"

            final_payload = {
                "type": "finished",
                "thread_id": thread_id,
                "status": "interrupted" if not req.approved else "finished",
                "data": safe_state
            }

            yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"

        except Exception as e:
            #如果还报EmptyInputError,说明MemorySaver 状态确实丢了，全局实例化没生效
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'❌ 执行出错: {str(e)}'}, ensure_ascii=False)}\n\n"

    # 🔥 返回原生的 StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# 🔥 新增：用户打分接口
@app.post("/api/rate")
async def rate_material(req: RateRequest):
    """接收用户打分，如果>=90且未向量化，则自动写入RAG向量库"""
    try:
        if not req.thread_id:
            return {"status": "error", "msg": "thread_id 不能为空"}
        # 1. 更新数据库评分
        await app.state.db_repo.update_quality_score(req.thread_id, req.score)

        # 2. 判断是否触发 RAG 自动写入
        if req.score >= 90:
            # 检查是否已经有向量了，防止重复打分重复写入
            exists = await app.state.db_repo.check_embedding_exists(req.thread_id)
            if not exists:
                # 🔥 改为调用专门的方法查询
                mat_data = await app.state.db_repo.get_material_for_embedding(req.thread_id)

                if mat_data and mat_data.get('title') and mat_data.get('body'):
                    emb_text = f"{mat_data['title']}\n{mat_data['body']}"
                    embedding = await get_embedding(emb_text)
                    if embedding:
                        await app.state.db_repo.save_embedding(
                            thread_id=req.thread_id,
                            title=mat_data['title'],
                            body=mat_data['body'],
                            audience="",  # 传空字符串，防止因为没存 audience 报错
                            embedding=embedding
                        )
                        # 🔥 修改提示文案
                        return {"status": "success", "msg": "评分成功，物料已自动沉淀至标准范例库！"}
                    else:
                        return {"status": "success", "msg": "评分成功，但向量化获取失败。"}
                else:
                    return {"status": "success", "msg": "评分成功，但数据库缺失物料文本，未写入范例库。"}
            else:
                # 🔥 修改提示文案
                return {"status": "success", "msg": "评分成功！该优质物料已在标准范例库中。"}

        else:
            return {
                "status": "success",
                "msg": f"评分成功，当前评分 {req.score} 分，未进入标准范例库。"
            }
    except Exception as e:
        import traceback
        traceback.print_exc()  # 🔥 在后端终端打印完整的堆栈错误
        return {"status": "error", "msg": f"后端内部错误: {str(e)}"}









if __name__ == "__main__":
    import uvicorn

    # 🔥 显式指定 loop 为 asyncio，防止 uvicorn 内部使用其他事件循环
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        loop="asyncio",  # 关键参数
        log_level="info"
    )





