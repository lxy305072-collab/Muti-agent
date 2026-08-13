import asyncio

import dashscope
import json
import os

import re
from contextlib import asynccontextmanager
from langgraph.graph import StateGraph,END
from pydantic import BaseModel, Field
from Tools.schema_tool import call_llm_with_schema, ReviewerSchema
from typing import TypedDict ,Optional
from dotenv import load_dotenv
from langgraph.config import get_config ,get_stream_writer # 👈 新增
from Tools.image_store import download_and_save_image  # 👈 新增
from Tools.stream_tool import stream_llm
from Tools.node_timer import start_node_log, end_node_log
from Tools.cache_tool import generate_cache_key,get_redis
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from config import settings
load_dotenv()

# dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
dashscope.api_key = settings.DASHSCOPE_API_KEY



class CampaignState(TypedDict):
    product_name: str
    key_features: str
    target_audience: str
    platform: str  # 🔥 新增：投放渠道 (xhs, wechat, etc.)

    title: str
    body: str
    image_prompt: str

    stream_text: str

    image_url:Optional[str]
    review_passed: bool
    review_feedback: str
    retry_count: int
    feedback: str

    # 🔥 新增：多智能体协作字段
    research_context: str          # Researcher 收集的资料
    critic_feedback: str           # Critic 的挑刺意见
    critic_passed: bool            # Critic 是否通过
    copy_revision_count: int       # 文案内部迭代次数
    quality_score: int
    judge_result: dict

    # 🔥 新增：用于跨模态反思的标记
    visual_fail_count: int  # 视觉审查连续失败次数
    cross_modal_feedback: str  # 跨模态反馈给文案的意见

# 🔥 优化 3：稳健的 JSON 提取函数（防 markdown 包裹防幻觉）
def extract_json(text):
    # 移除可能的 ```json 和 ``` 标记
    text = re.sub(r'^```json\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text.strip())
    # 正则匹配第一个 { ... } 块
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text


# ======================== researcher节点 ======================================
# 🔥 Researcher 的输出 Schema
class ResearchSchema(BaseModel):
    key_selling_points: str       # 提炼的核心卖点
    target_persona: str           # 目标用户画像
    tone_of_voice: str            # 建议的文案语气
    marketing_angle: str          # 营销角度/切入角度
    keywords: str                 # SEO/热词

async def researcher_node(state: CampaignState):
    _start_ts = start_node_log("researcher", "🔬", state)
    _error = None
    try:
        writer = get_stream_writer()
        writer({"type": "log", "content": "🔬 [Researcher] 正在分析产品信息并检索知识库..."})

        # 1. 检索品牌规范和历史优秀范例
        query = f"{state['product_name']} {state['key_features']} {state['target_audience']}"


        # 🔥 核心修改：通过依赖注入获取 db_repo，不再 from server import app
        cfg = get_config()
        db_repo = cfg.get("configurable", {}).get("db_repo")
        if not db_repo:
            raise ValueError("db_repo 未注入到 LangGraph config 中")

        knowledge_task = db_repo.search_knowledge(query, doc_types=["brand_guide", "good_example"], k=2)
        examples_task = db_repo.search_similar_materials(query, k=2)
        knowledge, examples = await asyncio.gather(knowledge_task, examples_task)

        # 2. 组装检索结果
        rag_text = ""
        if knowledge:
            rag_text += "【品牌规范】\n" + "\n".join([f"- {d['content']}" for d in knowledge]) + "\n"
        if examples:
            rag_text += "\n【历史优秀范例】\n"
            for ex in examples:
                rag_text += f"- 标题：{ex['title']} | 正文：{ex['body'][:80]}...\n"

        # 3. 调用大模型进行深度分析
        prompt = f"""
        你是一个资深市场研究员。请基于以下产品信息和参考资料，进行深度分析，为文案撰写提供策略指导。

        【产品信息】
        产品名称：{state['product_name']}
        核心卖点：{state['key_features']}
        目标受众：{state['target_audience']}

        【参考资料】
        {rag_text}

        请严格按 JSON 格式返回分析结果。
        """

        result = await asyncio.to_thread(
            call_llm_with_schema,
            os.getenv("MODEL_COPYWRITER"),
            prompt,
            ResearchSchema
        )

        research_context = f"""
        【研究员策略指导】
        - 核心卖点提炼：{result.key_selling_points}
        - 目标用户画像：{result.target_persona}
        - 建议文案语气：{result.tone_of_voice}
        - 营销切入角度：{result.marketing_angle}
        - 关键热词：{result.keywords}
        """

        writer({"type": "log", "content": f"✅ [Researcher] 策略分析完成！营销角度：{result.marketing_angle[:30]}..."})

        return {
            "research_context": research_context,
            "copy_revision_count": 0,
            "critic_passed": False,
            "critic_feedback": ""
        }
    except Exception as e:
        _error = e
        writer = get_stream_writer()
        writer({"type": "log", "content": f"⚠️ [Researcher] 分析异常，降级为无策略模式"})
        return {"research_context": "", "copy_revision_count": 0}
    finally:
        end_node_log("researcher", "🔬", _start_ts, error=_error)


# ============================ critic 审查员节点 ========================================
# 🔥 Critic 的输出 Schema
class CriticSchema(BaseModel):
    passed: bool
    title_feedback: str
    body_feedback: str
    overall_feedback: str
    estimated_score: int

async def critic_node(state: CampaignState):
    _start_ts = start_node_log("critic", "🧐", state)
    _error = None
    try:
        writer = get_stream_writer()
        revision = state.get("copy_revision_count", 0)
        writer({"type": "log", "content": f"🧐 [Critic] 第 {revision + 1} 轮文案审查中，正在严格挑刺..."})

        prompt = f"""
        你是一个极其严格的营销文案审查专家（Critic）。请对以下文案进行多维度审查。
        只有当文案在各维度都达到优秀水平时才给通过(passed=true)，否则必须给出具体修改意见。

        【审查维度】
        1. 标题吸引力：是否抓眼球、有记忆点？
        2. 正文质量：逻辑是否清晰？是否有说服力？emoji 使用是否恰当？
        3. 品牌调性：是否符合"年轻、活力、科技感"的品牌定位？
        4. 合规性：是否有违反广告法的极限词（如"全网最低"、"最好"等）？
        5. 目标受众匹配度：是否击中目标受众的痛点？

        【产品信息】
        产品：{state['product_name']}
        卖点：{state['key_features']}
        受众：{state['target_audience']}

        【待审查文案】
        标题：{state.get('title', '')}
        正文：{state.get('body', '')}

        【研究员策略指导】
        {state.get('research_context', '无')}

        请严格按 JSON 格式返回审查结果。预估分数(estimated_score)如果低于75分，请强制不通过。
        """

        result = await asyncio.to_thread(
            call_llm_with_schema,
            os.getenv("MODEL_COPYWRITER"),
            prompt,
            CriticSchema
        )

        is_passed = result.passed and result.estimated_score >= 75

        if is_passed:
            writer({"type": "log", "content": f"✅ [Critic] 审查通过！预估评分：{result.estimated_score} 分"})
        else:
            writer({"type": "log", "content": f"❌ [Critic] 审查未通过（预估{result.estimated_score}分），正在生成修改意见..."})
            writer({"type": "log", "content": f"📝 [Critic] 修改建议：{result.overall_feedback[:50]}..."})

        return {
            "critic_passed": is_passed,
            "critic_feedback": result.overall_feedback,
            "review_feedback": result.overall_feedback
        }
    except Exception as e:
        _error = e
        writer = get_stream_writer()
        writer({"type": "log", "content": "⚠️ [Critic] 审查异常，触发兜底放行"})
        return {"critic_passed": True, "critic_feedback": ""}
    finally:
        end_node_log("critic", "🧐", _start_ts, error=_error)



# =====================================Node  文案生成节点 =============================================
from langgraph.config import get_stream_writer


async def copywriter_node(state: CampaignState):
    _start_ts = start_node_log("copywriter", "✍️", state)
    _error = None
    try:
        print("⏳ [Node: Copywriter] 正在生成文案...",flush=True)
        writer = get_stream_writer()

        revision = state.get("copy_revision_count", 0)
        # 🔥 判断是首次生成还是 Critic 打回重写
        critic_feedback = state.get("critic_feedback", "")
        user_feedback = state.get("feedback", "")
        cross_modal_feedback = state.get("cross_modal_feedback", "")  # 🔥 新增
        # 🔥 核心修改：增加跨模态反馈的判断，且优先级最高
        if cross_modal_feedback:
            writer({"type": "log", "content": f"🖼️ [Copywriter] 收到视觉模型跨模态反馈，正在调整文案以适配画图..."})
        elif critic_feedback:
            writer({"type": "log",
                    "content": f"🔄 [Copywriter] 收到 Critic 修改意见，正在优化文案（第 {revision + 1} 版）..."})
        elif user_feedback:
            writer({"type": "log", "content": f"📝 [Copywriter] 收到人工修改意见：{user_feedback}，正在重写..."})
        else:
            writer({"type": "log", "content": "✍️ [Copywriter] 正在基于研究员策略生成文案..."})




        # 2.构建缓存Key
        # 如果是首次生成，user_feedback 为空字符串；如果是重写，user_feedback 会改变 Key，防止命中旧缓存
        cache_key = generate_cache_key(
            "copywriter",
            state["product_name"],
            state["key_features"],
            state["target_audience"],
            user_feedback,
            critic_feedback,
        )

        r=get_redis()

        #3.尝试命中缓存
        cache_data = await r.get(cache_key)
        if cache_data:
            writer({"type":"log", "content": "⚡ 命中历史缓存，极速输出文案..."})
            result = json.loads(cache_data)
            title_str = result.get("title","")
            body_str = result.get("body","")
            image_prompt_str = result.get("image_prompt","state['product_name']")

            #模拟流式输出体验
            for char in title_str:
                writer({"type":"title_chunk","content":char})
                await asyncio.sleep(0.02)
            writer({"type":"title_chunk","content":"\n\n"})
            await asyncio.sleep(0.3)

            for char in body_str:
                writer({"type":"body_chunk","content":char})
                await asyncio.sleep(0.01)

            await asyncio.sleep(0.5)
            # # 🔥 核心修复：不逐字推送，直接推送完整字符串，避免前端渲染乱码
            # writer({"type": "title_chunk", "content": title_str})
            # writer({"type": "title_chunk", "content": "\n\n"})
            # writer({"type": "body_chunk", "content": body_str})
            return {
                "title":title_str,
                "body":body_str,
                "image_prompt":image_prompt_str,
                "retry_count":0,
                "feedback":"",
                "copy_revision_count": revision + 1
            }


        rag_context = ""
        try:
            writer({"type": "log", "content": "🔍 正在执行混合检索召回..."})
            query = f"{state['product_name']} {state['key_features']} {state['target_audience']}"

            # 🔥 核心修改：通过依赖注入获取 db_repo
            cfg = get_config()
            db_repo = cfg.get("configurable", {}).get("db_repo")
            if not db_repo:
                raise ValueError("db_repo 未注入到 LangGraph config 中")

            # 🔥 1. 向量粗排：并发召回，各召回 3 条 (k=1, 3*1=3)
            knowledge_task = db_repo.search_knowledge(query, doc_types=["brand_guide", "good_example"], k=1)
            examples_task = db_repo.search_similar_materials(query, k=1)
            knowledge_recall, examples_recall = await asyncio.gather(knowledge_task, examples_task)

            # 🔥 2. Re-rank 精排：将召回结果交给大模型重新打分，只取最精准的 1 条
            rerank_knowledge_task = db_repo.rerank_documents(query, knowledge_recall, top_n=1)
            rerank_examples_task = db_repo.rerank_documents(query, examples_recall, top_n=1)
            knowledge, examples = await asyncio.gather(rerank_knowledge_task, rerank_examples_task)

            # 3. 组装 RAG 上下文
            if knowledge or examples:
                rag_context = "\n\n【参考信息】\n"
                if knowledge:
                    # 取出重排序得分最高的那条
                    best_knowledge = knowledge[0]
                    rag_context += f"品牌规范与要求（相关度: {best_knowledge.get('rerank_score', 0):.2f}）：\n- {best_knowledge['content']}\n"
                if examples:
                    best_example = examples[0]
                    rag_context += f"\n历史优秀文案范例（相关度: {best_example.get('rerank_score', 0):.2f}，请参考其风格，但不要直接抄袭）：\n"
                    rag_context += f"- 标题：{best_example['title']} | 正文：{best_example['body'][:50]}...\n"

                writer({"type": "log", "content": "✅ 混合检索+重排序完成，已注入精准上下文..."})
        except Exception as e:
            writer({"type": "log", "content": f"⚠️ 检索失败，降级为无 RAG 模式: {str(e)[:30]}"})

        # 🔥 修改：构建重写指令时，加入跨模态反馈
        rewrite_instruction = ""
        if cross_modal_feedback:
            rewrite_instruction = f"\n【⚠️ 跨模态审查反馈，请务必据此修改正文】\n{cross_modal_feedback}\n"
        elif critic_feedback:
            rewrite_instruction = f"\n【Critic 审查意见，请务必据此优化】\n{critic_feedback}\n"
        elif user_feedback:
            rewrite_instruction = f"\n【人工审核意见，请据此优化】\n{user_feedback}\n"

        research_context = state.get("research_context", "")

        prompt = f"""
        你是一个资深营销专家。请为以下产品设计营销文案。
        请严格按照如下 JSON 格式输出，【绝对不要】包含任何其他解释性文字或 Markdown 标记：
        {{
          "title": "吸引人的营销标题",
          "body": "营销正文，要求带 emoji，直接输出正文内容即可，不要写'正文：'之类的前缀",
          "image_prompt": "用于 AI 绘画的画面描述，中文最佳"
        }}
    
        产品名称：{state['product_name']}
        核心卖点：{state['key_features']}
        目标受众：{state['target_audience']}
        {rewrite_instruction}
        {rag_context}
        {research_context}
        """
        writer({"type": "log", "content": "🤖 正在生成标题..."})
        full_text = ""

        current_field = None  # 记录当前正在生成的字段: title, body, image_prompt
        # 1. 静默接收 LLM 的流式 JSON 片段
        async for token in stream_llm(os.getenv("MODEL_COPYWRITER"), prompt, writer=writer):
            full_text += token
            # 1. 状态机：检测当前进入了哪个 JSON 字段
            if '"title":' in full_text and current_field is None:
                current_field = "title"
                # 截取引号后的实际内容
                token = token.split('"', 2)[-1] if '"' in token else token
            elif '"body":' in full_text and current_field == "title":
                current_field = "body"
                token = token.split('"', 2)[-1] if '"' in token else token
                writer({"type": "title_chunk", "content": "\n\n"})  # 标题结束换行
            elif '"image_prompt":' in full_text and current_field == "body":
                current_field = "image_prompt"
                token = token.split('"', 2)[-1] if '"' in token else token

            # 2. 如果当前在某字段内，清理掉模型可能吐出的双引号和换行符
            if current_field in ["title", "body", "image_prompt"]:
                clean_token = token.replace('"', '').replace("\\n", "")
                if clean_token:
                    # 🔥 大模型吐一个字，立刻推给前端，真正的流式打字机！
                    writer({"type": f"{current_field}_chunk", "content": clean_token})

        writer({"type": "log", "content": "✅ 文案生成完毕！"})


        # 🔥 核心修复：提前定义默认值，防止 try 块报错导致变量未定义
        title_str = ""
        body_str = ""
        image_prompt_str = state['product_name']

        # 2. 解析 JSON
        try:
            clean_text = extract_json(full_text)
            result = json.loads(clean_text)
            title_str = result.get("title", "")
            body_str = result.get("body", "")
            image_prompt_str = result.get("image_prompt", "")
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON 解析失败: {e}\n原始输出: {full_text}")
            # 解析失败时，兜底处理
            title_str = "默认标题"
            body_str = full_text
            image_prompt_str = state['product_name']

        # 6. 将新生成且解析成功的结果存入 Redis (注意：只在解析成功时缓存)
        if title_str and body_str:
            await r.setex(
                cache_key,
                3600,  # 过期时间 1 小时
                json.dumps({
                    "title": title_str,
                    "body": body_str,
                    "image_prompt": image_prompt_str
                })
            )

        return {
            "title": title_str,
            "body": body_str,
            "image_prompt": image_prompt_str,
            "retry_count": state.get("retry_count", 0),
            "feedback": ""  ,# 消费完反馈后清空
            "copy_revision_count": revision + 1,
            "cross_modal_feedback": ""  # 🔥 新增：消费完跨模态反馈后清空，防止死循环
        }
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("copywriter", "✍️", _start_ts, error=_error)


# ========================= 多渠道适配节点 ========================================
# 🔥 新增：多渠道适配 Agent
class ChannelAdapterSchema(BaseModel):
    title: str = Field(description="根据渠道风格重写的标题")
    body: str = Field(description="根据渠道风格重写的正文")


async def channel_adapter_node(state: CampaignState):
    _start_ts = start_node_log("channel_adapter", "📱", state)
    _error = None
    try:
        writer = get_stream_writer()
        platform = state.get("platform", "general")

        # 🔥 修改：将英文标识转为中文展示
        platform_name_map = {
            "general": "通用",
            "xhs": "小红书",
            "wechat": "微信公众号"
        }
        platform_name = platform_name_map.get(platform, "通用")

        print(f"⏳ [Node: ChannelAdapter] 正在适配渠道风格: {platform_name}", flush=True)
        writer({"type": "log", "content": f"📱 [Adapter] 正在将文案适配为 【{platform_name}】 风格..."})

        style_guide = ""
        if platform == "xhs":
            style_guide = "小红书风格：大量使用Emoji，分段清晰，必须有吸引人的标题党，文末带Tag。"
        elif platform == "wechat":
            style_guide = "微信公众号风格：深度长文，专业客观，少用Emoji，注重逻辑和干货。"
        else:
            # 如果不指定渠道，直接跳过适配
            return {"visual_fail_count": 0}

        prompt = f"""
        你是一个社交媒体运营专家。请将以下基础营销文案改写为指定平台的风格。

        【目标平台风格要求】
        {style_guide}

        【基础文案】
        标题：{state.get('title', '')}
        正文：{state.get('body', '')}

        请严格按 JSON 格式返回改写后的文案。
        """

        result = await asyncio.to_thread(
            call_llm_with_schema,
            settings.MODEL_COPYWRITER,
            prompt,
            ChannelAdapterSchema
        )
        print(f"✅ [Node: ChannelAdapter] 适配完成！新标题: {result.title[:20]}...", flush=True)
        writer({"type": "log", "content": f"✅ [Adapter] 风格适配完成！"})

        return {
            "title": result.title,
            "body": result.body,
            "visual_fail_count": 0  # 初始化跨模态失败次数
        }
    except Exception as e:
        _error = e
        print(f"❌ [Node: ChannelAdapter] 发生异常: {e}", flush=True)
        writer = get_stream_writer()
        writer({"type": "log", "content": "⚠️ [Adapter] 适配异常，使用原本文案"})
        return {"visual_fail_count": 0}
    finally:
        end_node_log("channel_adapter", "📱", _start_ts, error=_error)




# wanx-v1 支持的合法尺寸（长宽必须能被 8 整除）
SUPPORTED_IMAGE_SIZES = ['1024*1024', '960*1280', '768*1024', '720*1280']
# 当前默认使用的生图尺寸 (修改这里即可切换全局尺寸)
DEFAULT_IMAGE_SIZE = '1024*1024'
# ==================================== Node 2: 图片生成节点 ======================================

async def image_generator_node(state:CampaignState):
    _start_ts = start_node_log("image_generator", "🎨", state)
    _error = None
    try:
        writer = get_stream_writer()
        print(f"⏳ [Node: ImageGen] 正在根据 Prompt 生成图片: {state['image_prompt'][:50]}...")
        # 🔥 新增：推送给前端
        writer({"type": "log", "content": f"🎨 [ImageGen] 正在根据 Prompt 生成图片{state['image_prompt'][:50]}..."})
        # 读取全局配置的尺寸
        target_size = DEFAULT_IMAGE_SIZE

        # 防御性校验：如果以后不小心改成了不合法的尺寸，这里直接拦截并降级为 1024*1024
        if target_size not in SUPPORTED_IMAGE_SIZES:
            print(f"⚠️ 配置的尺寸 {target_size} 不合法，降级使用默认尺寸 1024*1024")
            target_size = '1024*1024'

        # 将同步的 dashscope 调用封装成内部函数
        def _generate_image_sync():
            return dashscope.ImageSynthesis.call(
                model=os.getenv("MODEL_IMAGE"),
                prompt=state["image_prompt"],
                n=1,
                size=target_size
            )

        # 🔥 TODO 5: 增加指数退避重试兜底机制
        max_retries = 3
        remote_url = None
        for attempt in range(max_retries):
            rsp = await asyncio.to_thread(_generate_image_sync)
            if rsp and rsp.status_code == 200:
                if hasattr(rsp, 'output') and rsp.output:
                    output = rsp.output if isinstance(rsp.output, dict) else vars(rsp.output)
                    results = output.get('results', [])
                    if results and isinstance(results, list):
                        remote_url = results[0].get('url') if isinstance(results[0], dict) else results[0].url
                        break  # 成功获取，跳出重试

            print(f"⚠️ [Node: ImageGen] 第 {attempt + 1} 次画图失败，准备重试...")
            writer({"type": "log", "content": f"⚠️ 画图接口异常，第 {attempt + 1} 次重试中..."})
            await asyncio.sleep(2 ** attempt)  # 指数退避：1s, 2s, 4s

            # 🔥🔥🔥 核心修复：拿到临时 URL 后立即下载到本地
        image_url = None
        if remote_url:
            try:
                cfg = get_config()
                thread_id = cfg["configurable"]["thread_id"]
            except Exception:
                thread_id = str(id(state))  # 兜底
            writer({"type": "log", "content": "📥 正在持久化图片到本地..."})
            image_url = await download_and_save_image(remote_url, thread_id)
            if not image_url:
                # 下载失败兜底：仍然用临时 URL（至少当前会话能用）
                image_url = remote_url
                writer({"type": "log", "content": "⚠️ 图片本地化失败，使用临时链接（可能过期）"})

        if not image_url:
            writer({"type": "log", "content": "❌ 画图彻底失败，将跳过审查环节"})
        else:
            writer({"type": "log", "content": "✅ 图片生成并持久化成功！"})
        return {"image_url": image_url}
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("image_generator", "🎨", _start_ts, error=_error)


# ================================== Node 3: 视觉审查节点  ================================

async def visual_reviewer_node(state: CampaignState):
    _start_ts = start_node_log("visual_reviewer", "🔍", state)
    _error = None
    try:
        writer = get_stream_writer()
        # 🛑 核心修复 1：前置空值拦截！只要 image_url 是 None 或空，直接放行，绝不调用 VLM
        image_url = state.get("image_url")
        if not image_url:
            print("⚠️ [Node: VisualReviewer] 检测到 image_url 为空，跳过审查，默认放行防阻塞")
            return {
                "review_passed": True,
                "review_feedback": "图片生成失败，跳过视觉审查",
                "retry_count": state.get("retry_count", 0) + 1
            }

        # 🔥 核心优化：获取当前是第几次审查，并推送给前端
        current_retry = state.get("retry_count", 0)
        attempt_label = f"第 {current_retry + 1} 次审查" if current_retry > 0 else "首次审查"

        # 🔥 TODO 4: 推送详细的审查进度
        writer({"type": "log", "content": f"🔍 [{attempt_label}] 正在调用视觉大模型进行图文一致性审查..."})
        print("⏳ [Node: VisualReviewer] 正在进行图文一致性审查...")

        messages = [
            {
                "role": "user",
                "content": [
                    {"image": state["image_url"]},
                    {
                        "text": f"这是为以下营销文案生成的配图。请判断图片的内容是否与文案描述的场景/产品高度契合。\n文案：'{state['body']}'\n\n【强制要求】你必须以 JSON 格式返回，且必须包含布尔值字段 `passed` 和字符串字段 `feedback'。"
                                f"请简短回答，feedback不超过20个字。"}
                ]
            }
        ]

        def _vlm_review_sync():
            return dashscope.MultiModalConversation.call(
                model=os.getenv("MODEL_VISION"),
                messages=messages,
                response_format={"type": "json_object"}
            )
        # 注意：通义VL的多模态调用稍微不同，为了使用我们的 schema 工具，我们做一下适配
        # 这里简化处理，直接调用原生 API 并手动解析，实际工程中可以封装得更优雅

        try:
            response = await asyncio.to_thread(_vlm_review_sync)
            raw_text = response.output.choices[0].message.content[0]["text"]
            result_dict = json.loads(raw_text)
            is_passed = result_dict.get("passed", result_dict.get("is_passed", True))
            feedback = result_dict.get("feedback", result_dict.get("reason", "无反馈"))

            # 🔥 TODO 4: 推送审查结果反馈
            if is_passed:
                writer({"type": "log", "content": f"✅ [{attempt_label}] 审查通过！图文一致性良好。"})
            else:
                writer({"type": "log", "content": f"❌ [{attempt_label}] 审查未通过。反馈：{feedback}"})




            result = ReviewerSchema.model_validate({"passed": is_passed, "feedback": feedback})
        except Exception as e:
            writer({"type": "log", "content": "⚠️ 审查模型异常，触发兜底放行。"})
            return {"review_passed": True, "review_feedback": "", "retry_count": state.get("retry_count", 0) + 1}




        current_retry = state.get("retry_count", 0) + 1
        print(f"✅ 审查通过: {result.passed}" if result.passed else f"❌ 审查未通过: {result.feedback}")
        writer({
            "type": "log",
            "content":
                f"视觉审核结果: {'通过' if result.passed else '未通过'}"
        })

        # 🔥 核心升级：跨模态反思逻辑
        current_visual_fails = state.get("visual_fail_count", 0)
        if not result.passed:
            current_visual_fails += 1
            # 如果连续失败 2 次以上，生成跨模态反馈，准备让 Copywriter 改文案
            if current_visual_fails >= 2:
                cross_feedback = f"视觉模型连续{current_visual_fails}次无法根据文案生成合适图片。反馈：{result.feedback}。请修改正文，使其更容易被转化为画面。"
                writer({"type": "log", "content": "⚠️ [VLM] 连续画图失败，触发跨模态反思，准备修改文案！"})
                return {
                    "review_passed": False,
                    "review_feedback": result.feedback,
                    "retry_count": current_retry,
                    "visual_fail_count": current_visual_fails,
                    "cross_modal_feedback": cross_feedback
                }

        return {
            "review_passed": result.passed,
            "review_feedback": result.feedback,
            "retry_count": current_retry,
            "visual_fail_count": 0 if result.passed else current_visual_fails,
            "cross_modal_feedback": ""
        }




        # return {
        #     "review_passed": result.passed,
        #     "review_feedback": result.feedback,
        #     "retry_count": current_retry
        # }
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("visual_reviewer", "🔍", _start_ts, error=_error)

# --------------------------- Node 4: 提示词重写器 (V3 新增灵魂) ------------------------------
async def prompt_rewriter_node(state: CampaignState):
    _start_ts = start_node_log("prompt_rewriter", "🔄", state)
    _error = None
    try:
        writer = get_stream_writer()
        writer({"type": "log", "content": "⏳ 正在根据审查反馈重写绘图提示词..."})
        prompt = f"""
        你是一个 AI 绘画提示词专家。
        之前的画图提示词生成的图片没有通过审查。
        - 营销文案：{state['body']}
        - 旧的画图提示词：{state['image_prompt']}
        - 审查反馈：{state['review_feedback']}
    
        请结合反馈，修改画图提示词，确保新生成的图片能准确表达文案意境。
        """

        # 复用 Schema 校验，只需要 image_prompt 字段
        class PromptSchema(BaseModel):
            image_prompt: str

        result =await asyncio.to_thread (call_llm_with_schema,os.getenv("MODEL_PROMPT_REWRITER"), prompt, PromptSchema)

        print(f"✨ 新的 Prompt: {result.image_prompt[:50]}...")
        return {"image_prompt": result.image_prompt}
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("prompt_rewriter", "🔄", _start_ts, error=_error)




#===================================================搭建工作流=================================================
@asynccontextmanager
async def build_graph():
    workflow = StateGraph(CampaignState)

    # 🔥 1. 添加所有节点
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("copywriter", copywriter_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("channel_adapter", channel_adapter_node)
    workflow.add_node("image_generator", image_generator_node)
    workflow.add_node("visual_reviewer", visual_reviewer_node)
    workflow.add_node("prompt_rewriter", prompt_rewriter_node)
    # 🔥 2. 设置入口：从 Researcher 开始
    workflow.set_entry_point("researcher")

    # 🔥 3. Researcher -> Copywriter -> Critic
    workflow.add_edge("researcher", "copywriter")
    workflow.add_edge("copywriter", "critic")

    # 🔥 4. Critic 条件路由（自动迭代 vs 放行给人工）
    async def route_after_critic(state: CampaignState):
        if state.get("critic_passed") or state.get("copy_revision_count", 0) >= 3:
            return "adapt" # 通过，去适配渠道
        return "rewrite"  # 不通过，打回给 Copywriter

    workflow.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "rewrite": "copywriter",
            "adapt": "channel_adapter"  # 🔥 路由到新节点
        }
    )


    # 🔥 5. 渠道适配后 -> 人工审核中断点
    workflow.add_edge("channel_adapter", "image_generator")

    # 6. 图片生成 -> 视觉审查
    workflow.add_edge("image_generator", "visual_reviewer")

    # 🔥 7. 视觉审查条件路由 (升级：支持跨模态反思)
    async def route_after_review(state: CampaignState):
        # 如果通过了，或者重试次数超限，直接结束
        if state.get("review_passed") or state.get("retry_count", 0) >= 4:
            return "end"
        # 🔥 核心升级：如果有跨模态反馈，打回给 Copywriter 改文案
        if state.get("cross_modal_feedback"):
            return "cross_modal_rewrite"
        # 默认情况：重写 Prompt 重新画图
        return "rewrite_prompt"

    workflow.add_conditional_edges(
        "visual_reviewer",
        route_after_review,
        {
            "rewrite": "prompt_rewriter",  # 如果不通过，先重写prompt
            "cross_modal_rewrite": "copywriter",  # 🔥 跨模态：打回给文案重写
            "end": END  # 如果通过，结束
        }
    )

    # 重写后重新画图
    workflow.add_edge("prompt_rewriter", "image_generator")


    # 🔥 核心改造：使用 PostgreSQL 持久化
    DB_URI = os.getenv("POSTGRES_URI", "postgresql://myuser:123456@127.0.0.1:5432/marketing_db")
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
        # 自动在数据库里创建 langgraph 所需的 checkpoint 表
        await checkpointer.setup()
        app = workflow.compile(
            interrupt_before=["image_generator"],
            checkpointer=checkpointer,
        )
        yield app



if __name__ == '__main__':
    print("🚀 启动 V4 带人工审核的智能工作流...")
    # 1. 先调用函数获取编译好的图对象
    app = build_graph()  # 1. 先调用函数获取编译好的图对象

    config = {"configurable": {"thread_id": "thread-1"}}

    initial_state = {
        "product_name": "极光智能保温杯",
        "key_features": "温显、超长保温、轻量化",
        "target_audience": "户外爱好者、上班族"
    }

    print("\n--- 阶段1: 生成文案 ---")
    # 第一次运行，跑到 copywriter 就会暂停
    for event in app.astream(initial_state, config=config):
        for node_name, state_update in event.items():
            if "title" in state_update:
                print(f"📝 生成标题: {state_update['title']}")
                print(f"📝 生成正文: {state_update['body']}")

    # 获取当前暂停的状态
    current_state = app.get_state(config)

    print("\n" + "=" * 50)
    user_input = input("👨‍💻 请审查文案。是否满意并继续生成配图？ [y/n]\n")
    print("=" * 50 + "\n")

    if user_input.strip().lower() == 'y':
        print("👍 人工审核通过，继续执行图生视频/图片流...")

        # 🔥 核心修复：恢复执行，并收集所有后续事件
        final_image_url = None
        for event in app.astream(None, config=config):
            for node_name, state_update in event.items():
                print(f"⏳ 处理节点: {node_name}")
                # 如果图片生成节点更新了 URL，我们就捕获它
                if isinstance(state_update, dict) and "image_url" in state_update and state_update["image_url"]:
                    final_image_url = state_update["image_url"]

        # 🔥 工作流彻底跑完后，获取最终状态
        final_state = app.get_state(config).values

        print("\n" + "=" * 50)
        print("🎉 最终物料产出：")
        print(f"标题: {final_state.get('title')}")
        print(f"正文: \n{final_state.get('body')}")
        print(f"最终配图URL: {final_state.get('image_url')}")
        print(f"审查状态: {'通过' if final_state.get('review_passed') else '未通过/超时'}")
        print(f"迭代次数: {final_state.get('retry_count')}")
        print("=" * 50)
    else:
        print("❌ 人工驳回。流程终止。")
















