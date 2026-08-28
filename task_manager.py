# task_manager.py
"""
异步任务管理器
- 任务在 asyncio 后台运行，与 HTTP 连接解耦
- 事件通过 Redis Stream 持久化，客户端可断线重连
- 替代方案中的 Celery/Arq：用 asyncio.Task + Redis Stream 实现"轻量级异步任务队列"
"""
import asyncio
import json
from typing import Optional, Dict, Any
from Tools.cache_tool import get_redis
from logger_config import get_logger, set_trace_id

logger = get_logger()

# Redis Stream key 前缀
STREAM_KEY_PREFIX = "task:stream:"
# 任务状态 Hash key 前缀（用于查询任务是否在跑/已完成）
TASK_STATUS_PREFIX = "task:status:"
# Stream 最大长度（防止无限增长，FIFO 截断）
STREAM_MAX_LEN = 500
# 任务状态 TTL（24 小时后自动清理）
TASK_STATUS_TTL = 86400


class TaskManager:
    def __init__(self):
        # 内存中保存 asyncio.Task 引用，避免被 GC
        self._tasks: Dict[str, asyncio.Task] = {}

    @staticmethod
    def _stream_key(thread_id: str) -> str:
        return f"{STREAM_KEY_PREFIX}{thread_id}"

    @staticmethod
    def _status_key(thread_id: str) -> str:
        return f"{TASK_STATUS_PREFIX}{thread_id}"

    # ============= 内部工具方法 =============
    async def _publish_event(self, thread_id: str, event: dict):
        """把事件写入 Redis Stream（持久化，支持客户端断线重连续传）"""
        r = get_redis()
        await r.xadd(
            self._stream_key(thread_id),
            {"data": json.dumps(event, ensure_ascii=False)},
            maxlen=STREAM_MAX_LEN,
            approximate=True,
        )

    async def _set_status(self, thread_id: str, status: str, extra: Optional[dict] = None):
        """更新任务状态到 Redis Hash"""
        r = get_redis()
        payload = {"status": json.dumps(status)}
        if extra:
            for k, v in extra.items():
                payload[k] = json.dumps(v)
        await r.hset(self._status_key(thread_id), mapping=payload)
        await r.expire(self._status_key(thread_id), TASK_STATUS_TTL)

    @staticmethod
    def _clean_state(obj):
        """剥离 LangGraph state 中的 messages 字段，避免序列化爆炸"""
        if isinstance(obj, dict):
            return {k: TaskManager._clean_state(v) for k, v in obj.items() if k != "messages"}
        elif isinstance(obj, list):
            return [TaskManager._clean_state(v) for v in obj]
        elif hasattr(obj, 'model_dump'):
            return TaskManager._clean_state(obj.model_dump())
        elif isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        else:
            return str(obj)

    # ============= 对外查询接口 =============
    async def get_status(self, thread_id: str) -> Optional[dict]:
        """查询任务状态"""
        r = get_redis()
        raw = await r.hgetall(self._status_key(thread_id))
        if not raw:
            return None
        # redis-py 返回的 key/value 可能是 bytes 或 str（取决于 decode_responses 配置）
        result = {}
        for k, v in raw.items():
            k_str = k.decode() if isinstance(k, bytes) else k
            v_str = v.decode() if isinstance(v, bytes) else v
            try:
                result[k_str] = json.loads(v_str)
            except Exception:
                result[k_str] = v_str
        return result

    # ============= 启动任务接口 =============
    async def start_workflow_task(
        self,
        workflow_app,
        initial_state: dict,
        config: dict,
        thread_id: str,
        req_data: dict,
        db_repo,
    ) -> bool:
        """启动【阶段1：生成文案】的后台任务"""
        # 防重复：如果该 thread_id 已有任务在跑，拒绝
        existing = self._tasks.get(thread_id)
        if existing and not existing.done():
            logger.warning(f"⚠️ 任务 {thread_id} 已在运行中，拒绝重复启动")
            return False

        # 初始化任务状态
        await self._set_status(thread_id, "running", {"phase": "phase1"})
        # 写入 init 事件（前端用来拿 thread_id）
        await self._publish_event(thread_id, {"type": "init", "thread_id": thread_id})

        # 创建后台任务（与 HTTP 请求生命周期解耦）
        task = asyncio.create_task(
            self._run_workflow(
                workflow_app=workflow_app,
                stream_input=initial_state,
                config=config,
                thread_id=thread_id,
                req_data=req_data,
                db_repo=db_repo,
                is_resume=False,
                resume_req=None,
            )
        )
        self._tasks[thread_id] = task
        logger.info(f"🚀 后台任务已启动: {thread_id}")
        return True

    # task_manager.py 中的 resume_workflow_task 方法
    async def resume_workflow_task(
            self,
            workflow_app,
            config: dict,
            thread_id: str,
            req,  # ResumeRequest
            db_repo,
    ) -> bool:
        """启动【阶段2：恢复执行】的后台任务"""
        existing = self._tasks.get(thread_id)
        # 🔥 修复：只有当任务存在且尚未完成时，才拒绝重复恢复
        if existing and not existing.done():
            logger.warning(f"⚠️ 任务 {thread_id} 仍在运行中，拒绝重复恢复")
            return False

        # 校验工作流是否处于中断态
        current_state_before = await workflow_app.aget_state(config)
        if not current_state_before.next and req.approved:
            logger.warning(f"⚠️ 任务 {thread_id} 未处于暂停状态，无法恢复")
            return False

        # 构造 resume_input（与原 /api/resume 逻辑一致）
        from langgraph.types import Command
        if not req.approved:
            resume_input = Command(goto="copywriter", update={"feedback": req.feedback})
            await self._publish_event(thread_id,
                                      {"type": "log", "content": "⏳ 收到打回重写指令，正在根据反馈重写文案..."})
        else:
            resume_input = None
            await self._publish_event(thread_id, {"type": "log", "content": "🚀 接收到审核通过指令，准备恢复执行..."})

        await self._set_status(thread_id, "running", {"phase": "phase2"})

        task = asyncio.create_task(
            self._run_workflow(
                workflow_app=workflow_app,
                stream_input=resume_input,
                config=config,
                thread_id=thread_id,
                req_data=req.model_dump(),
                db_repo=db_repo,
                is_resume=True,
                resume_req=req,
            )
        )
        self._tasks[thread_id] = task
        logger.info(f"🚀 后台恢复任务已启动: {thread_id}")
        return True

    # ============= 实际工作流执行协程 =============
    async def _run_workflow(
        self,
        workflow_app,
        stream_input,   # 阶段1: initial_state dict；阶段2: Command 或 None
        config: dict,
        thread_id: str,
        req_data: dict,
        db_repo,
        is_resume: bool,
        resume_req,
    ):
        """后台实际执行 LangGraph 的协程，与 HTTP 连接完全解耦"""
        # ⚠️ 关键：为后台任务设置独立 trace_id，保证日志可追溯
        set_trace_id(thread_id[:8])

        # 🔥 新增：稍作等待，确保前端已拿到 POST 响应并发起了 GET /stream 订阅
        # 防止前端因为还没连上而丢掉最开始的 "init" 或 "log" 事件
        await asyncio.sleep(0.2)
        try:
            # 1. 流式执行 LangGraph，把每个 custom 事件写入 Redis Stream
            async for event in workflow_app.astream(stream_input, config=config, stream_mode="custom"):
                await self._publish_event(thread_id, event)

            # 2. 获取最终状态
            current_state = await workflow_app.aget_state(config)
            clean_data = self._clean_state(current_state.values)

            # 3. 根据阶段做不同的落库操作（保留原 /api/start 和 /api/resume 的逻辑）
            if not is_resume:
                # ===== 阶段1：保存物料基础信息 =====
                if clean_data.get("title"):
                    await db_repo.upsert_material(
                        thread_id=thread_id,
                        product_name=req_data.get("product_name", ""),
                        key_features=req_data.get("key_features", ""),
                        target_audience=req_data.get("target_audience", ""),
                        title=clean_data["title"],
                        body=clean_data.get("body", ""),
                        image_prompt=clean_data.get("image_prompt", "")
                    )
                logger.info("✅ 阶段1生成完毕，工作流已挂起等待人工审核。")
            else:
                # ===== 阶段2：审核通过则 finalize =====
                if resume_req.approved:
                    if clean_data.get("image_url") and clean_data.get("title"):
                        try:
                            await db_repo.finalize_material(
                                thread_id=thread_id,
                                title=clean_data["title"],
                                body=clean_data["body"],
                                image_prompt=clean_data.get("image_prompt", ""),
                                image_url=clean_data["image_url"],
                                review_passed=clean_data.get("review_passed", True),
                                review_feedback=clean_data.get("review_feedback", ""),
                                retry_count=clean_data.get("retry_count", 0)
                            )
                            await self._publish_event(thread_id, {"type": "log", "content": "💾 物料基础信息已落库，请在完成后进行打分评价。"})
                        except Exception as db_e:
                            logger.error(f"⚠️ 落库失败: {db_e}")

                    logger.info("🎉 工作流全部执行完毕！")
                    await self._publish_event(thread_id, {"type": "log", "content": "🎉 工作流全部执行完毕！"})

            # 4. 写入终结事件（前端收到后会关闭 EventSource）
            final_payload = {
                "type": "finished",
                "thread_id": thread_id,
                "status": "interrupted" if current_state.next else "finished",
                "data": clean_data,
            }
            await self._publish_event(thread_id, final_payload)

            # 5. 更新任务状态
            final_status = "interrupted" if current_state.next else "done"
            await self._set_status(thread_id, final_status)

        except Exception as e:
            logger.exception(f"❌ 后台任务 {thread_id} 执行出错: {str(e)}")
            await self._publish_event(thread_id, {"type": "error", "message": f"❌ 执行出错: {str(e)}"})
            await self._set_status(thread_id, "failed", {"error": str(e)})
        finally:
            # 🔥 修复：去掉 sleep(60)，立刻清理任务引用。
            # 这样当阶段1完成并挂起后，任务状态标记为 done，就不会阻塞 /api/resume 请求了。
            self._tasks.pop(thread_id, None)


# 全局单例
task_manager = TaskManager()