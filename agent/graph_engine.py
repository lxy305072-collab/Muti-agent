import asyncio
import dashscope
import json
import os
import re
from contextlib import asynccontextmanager
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from Tools.schema_tool import call_llm_with_schema, ReviewerSchema
from typing import TypedDict, Optional
from dotenv import load_dotenv
from langgraph.config import get_config, get_stream_writer
from Tools.image_store import download_and_save_image
from Tools.stream_tool import stream_llm
from Tools.node_timer import start_node_log, end_node_log
from Tools.cache_tool import generate_cache_key, get_redis
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from config import settings
from logger_config import get_logger  # 🔥 新增

logger = get_logger()  # 🔥 新增
load_dotenv()
dashscope.api_key = settings.DASHSCOPE_API_KEY


class CampaignState(TypedDict):
    product_name: str
    key_features: str
    target_audience: str

    platform: str
    title: str
    body: str

    image_prompt: str
    stream_text: str
    image_url: Optional[str]

    review_passed: bool
    review_feedback: str
    retry_count: int

    feedback: str
    research_context: str
    critic_feedback: str
    critic_passed: bool

    copy_revision_count: int
    quality_score: int
    judge_result: dict
    visual_fail_count: int
    cross_modal_feedback: str


def extract_json(text):
    text = re.sub(r'^```json\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text.strip())
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text


#=============================== research 节点 ====================================
class ResearchSchema(BaseModel):
    key_selling_points: str
    target_persona: str
    tone_of_voice: str
    marketing_angle: str
    keywords: str


async def researcher_node(state: CampaignState):
    _start_ts = start_node_log("researcher", "🔬", state)
    _error = None
    try:
        writer = get_stream_writer()
        writer({"type": "log", "content": "🔬 [Researcher] 正在分析产品信息并检索知识库..."})
        query = f"{state['product_name']} {state['key_features']} {state['target_audience']}"

        cfg = get_config()
        db_repo = cfg.get("configurable", {}).get("db_repo")
        if not db_repo:
            raise ValueError("db_repo 未注入到 LangGraph config 中")

        knowledge_task = db_repo.search_knowledge(query, doc_types=["brand_guide", "good_example"], k=2)
        examples_task = db_repo.search_similar_materials(query, k=2)
        knowledge, examples = await asyncio.gather(knowledge_task, examples_task)

        rag_text = ""
        if knowledge:
            rag_text += "【品牌规范】\n" + "\n".join([f"- {d['content']}" for d in knowledge]) + "\n"
        if examples:
            rag_text += "\n【历史优秀范例】\n"
            for ex in examples:
                rag_text += f"- 标题：{ex['title']} | 正文：{ex['body'][:80]}...\n"

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

        result = await asyncio.to_thread(call_llm_with_schema, settings.MODEL_COPYWRITER, prompt, ResearchSchema)

        research_context = f"""
        【研究员策略指导】
        - 核心卖点提炼：{result.key_selling_points}
        - 目标用户画像：{result.target_persona}
        - 建议文案语气：{result.tone_of_voice}
        - 营销切入角度：{result.marketing_angle}
        - 关键热词：{result.keywords}
        """
        writer({"type": "log", "content": f"✅ [Researcher] 策略分析完成！营销角度：{result.marketing_angle[:30]}..."})

        return {"research_context": research_context, "copy_revision_count": 0, "critic_passed": False,
                "critic_feedback": ""}
    except Exception as e:
        _error = e
        writer = get_stream_writer()
        writer({"type": "log", "content": f"⚠️ [Researcher] 分析异常，降级为无策略模式"})
        return {"research_context": "", "copy_revision_count": 0}
    finally:
        end_node_log("researcher", "🔬", _start_ts, error=_error)

# ============================== 模型审核节点===================================
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
        你是一个极其严格的营销文案审查专家。请对以下文案进行多维度审查。
        【待审查文案】
        标题：{state.get('title', '')}
        正文：{state.get('body', '')}
        【研究员策略指导】
        {state.get('research_context', '无')}
        请严格按 JSON 格式返回审查结果。预估分数低于75分强制不通过。
        """

        result = await asyncio.to_thread(call_llm_with_schema, settings.MODEL_COPYWRITER, prompt, CriticSchema)
        is_passed = result.passed and result.estimated_score >= 75

        if is_passed:
            writer({"type": "log", "content": f"✅ [Critic] 审查通过！预估评分：{result.estimated_score} 分"})
        else:
            writer({"type": "log",
                    "content": f"❌ [Critic] 审查未通过（预估{result.estimated_score}分），正在生成修改意见..."})
            writer({"type": "log", "content": f"📝 [Critic] 修改建议：{result.overall_feedback[:50]}..."})

        return {"critic_passed": is_passed, "critic_feedback": result.overall_feedback,
                "review_feedback": result.overall_feedback}
    except Exception as e:
        _error = e
        writer = get_stream_writer()
        writer({"type": "log", "content": "⚠️ [Critic] 审查异常，触发兜底放行"})
        return {"critic_passed": True, "critic_feedback": ""}
    finally:
        end_node_log("critic", "🧐", _start_ts, error=_error)


#================================= 生成文案节点 =========================================
async def copywriter_node(state: CampaignState):
    _start_ts = start_node_log("copywriter", "✍️", state)
    _error = None
    try:
        logger.info("⏳ [Node: Copywriter] 正在生成文案...")
        writer = get_stream_writer()
        revision = state.get("copy_revision_count", 0)
        critic_feedback = state.get("critic_feedback", "")
        user_feedback = state.get("feedback", "")
        cross_modal_feedback = state.get("cross_modal_feedback", "")

        if cross_modal_feedback:
            writer({"type": "log", "content": f"🖼️ [Copywriter] 收到视觉模型跨模态反馈，正在调整文案以适配画图..."})
        elif critic_feedback:
            writer({"type": "log",
                    "content": f"🔄 [Copywriter] 收到 Critic 修改意见，正在优化文案（第 {revision + 1} 版）..."})
        elif user_feedback:
            writer({"type": "log", "content": f"📝 [Copywriter] 收到人工修改意见：{user_feedback}，正在重写..."})
        else:
            writer({"type": "log", "content": "✍️ [Copywriter] 正在基于研究员策略生成文案..."})

        cache_key = generate_cache_key("copywriter", state["product_name"], state["key_features"],
                                       state["target_audience"], user_feedback, critic_feedback)
        r = get_redis()
        cache_data = await r.get(cache_key)

        if cache_data:
            writer({"type": "log", "content": "⚡ 命中历史缓存，极速输出文案..."})
            result = json.loads(cache_data)
            title_str = result.get("title", "")
            body_str = result.get("body", "")
            image_prompt_str = result.get("image_prompt", state['product_name'])

            for char in title_str:
                writer({"type": "title_chunk", "content": char})
                await asyncio.sleep(0.02)
            writer({"type": "title_chunk", "content": "\n\n"})
            await asyncio.sleep(0.3)
            for char in body_str:
                writer({"type": "body_chunk", "content": char})
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.5)
            return {"title": title_str, "body": body_str, "image_prompt": image_prompt_str,
                    "retry_count": state.get("retry_count", 0), "feedback": "", "copy_revision_count": revision + 1,
                    "cross_modal_feedback": ""}

        rag_context = ""
        try:
            writer({"type": "log", "content": "🔍 正在执行混合检索召回..."})
            query = f"{state['product_name']} {state['key_features']} {state['target_audience']}"
            cfg = get_config()
            db_repo = cfg.get("configurable", {}).get("db_repo")
            if not db_repo: raise ValueError("db_repo 未注入")

            knowledge_task = db_repo.search_knowledge(query, doc_types=["brand_guide", "good_example"], k=1)
            examples_task = db_repo.search_similar_materials(query, k=1)
            knowledge_recall, examples_recall = await asyncio.gather(knowledge_task, examples_task)

            rerank_knowledge_task = db_repo.rerank_documents(query, knowledge_recall, top_n=1)
            rerank_examples_task = db_repo.rerank_documents(query, examples_recall, top_n=1)
            knowledge, examples = await asyncio.gather(rerank_knowledge_task, rerank_examples_task)

            if knowledge or examples:
                rag_context = "\n\n【参考信息】\n"
                if knowledge:
                    best_knowledge = knowledge[0]
                    rag_context += f"品牌规范与要求（相关度: {best_knowledge.get('rerank_score', 0):.2f}）：\n- {best_knowledge['content']}\n"
                if examples:
                    best_example = examples[0]
                    rag_context += f"\n历史优秀文案范例（相关度: {best_example.get('rerank_score', 0):.2f}）：\n- 标题：{best_example['title']} | 正文：{best_example['body'][:50]}...\n"
                writer({"type": "log", "content": "✅ 混合检索+重排序完成，已注入精准上下文..."})
        except Exception as e:
            writer({"type": "log", "content": f"⚠️ 检索失败，降级为无 RAG 模式: {str(e)[:30]}"})

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
          "body": "营销正文，要求带 emoji，直接输出正文内容即可",
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
        current_field = None

        async for token in stream_llm(settings.MODEL_COPYWRITER, prompt, writer=writer):
            full_text += token
            if '"title":' in full_text and current_field is None:
                current_field = "title"
                token = token.split('"', 2)[-1] if '"' in token else token
            elif '"body":' in full_text and current_field == "title":
                current_field = "body"
                token = token.split('"', 2)[-1] if '"' in token else token
                writer({"type": "title_chunk", "content": "\n\n"})
            elif '"image_prompt":' in full_text and current_field == "body":
                current_field = "image_prompt"
                token = token.split('"', 2)[-1] if '"' in token else token

            if current_field in ["title", "body", "image_prompt"]:
                clean_token = token.replace('"', '').replace("\\n", "")
                if clean_token:
                    writer({"type": f"{current_field}_chunk", "content": clean_token})

        writer({"type": "log", "content": "✅ 文案生成完毕！"})

        title_str, body_str, image_prompt_str = "", "", state['product_name']
        try:
            clean_text = extract_json(full_text)
            result = json.loads(clean_text)
            title_str = result.get("title", "")
            body_str = result.get("body", "")
            image_prompt_str = result.get("image_prompt", "")
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ JSON 解析失败: {e}\n原始输出: {full_text}")
            title_str = "默认标题"
            body_str = full_text
            image_prompt_str = state['product_name']

        if title_str and body_str:
            await r.setex(cache_key, 3600,
                          json.dumps({"title": title_str, "body": body_str, "image_prompt": image_prompt_str}))

        return {"title": title_str, "body": body_str, "image_prompt": image_prompt_str,
                "retry_count": state.get("retry_count", 0), "feedback": "", "copy_revision_count": revision + 1,
                "cross_modal_feedback": ""}
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("copywriter", "✍️", _start_ts, error=_error)


#======================================== 自动适配节点 ==================================================
class ChannelAdapterSchema(BaseModel):
    title: str = Field(description="根据渠道风格重写的标题")
    body: str = Field(description="根据渠道风格重写的正文")


async def channel_adapter_node(state: CampaignState):
    _start_ts = start_node_log("channel_adapter", "📱", state)
    _error = None
    try:
        writer = get_stream_writer()
        platform = state.get("platform", "general")
        platform_name_map = {"general": "通用", "xhs": "小红书", "wechat": "微信公众号"}
        platform_name = platform_name_map.get(platform, "通用")

        logger.info(f"⏳ [Node: ChannelAdapter] 正在适配渠道风格: {platform_name}")
        writer({"type": "log", "content": f"📱 [Adapter] 正在将文案适配为 【{platform_name}】 风格..."})

        style_guide = ""
        if platform == "xhs":
            style_guide = "小红书风格：大量使用Emoji，分段清晰，必须有吸引人的标题党，文末带Tag。"
        elif platform == "wechat":
            style_guide = "微信公众号风格：深度长文，专业客观，少用Emoji，注重逻辑和干货。"
        else:
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
        result = await asyncio.to_thread(call_llm_with_schema, settings.MODEL_COPYWRITER, prompt, ChannelAdapterSchema)
        logger.info(f"✅ [Node: ChannelAdapter] 适配完成！新标题: {result.title[:20]}...")
        writer({"type": "log", "content": f"✅ [Adapter] 风格适配完成！"})
        return {"title": result.title, "body": result.body, "visual_fail_count": 0}
    except Exception as e:
        _error = e
        logger.error(f"❌ [Node: ChannelAdapter] 发生异常: {e}")
        writer = get_stream_writer()
        writer({"type": "log", "content": "⚠️ [Adapter] 适配异常，使用原本文案"})
        return {"visual_fail_count": 0}
    finally:
        end_node_log("channel_adapter", "📱", _start_ts, error=_error)


SUPPORTED_IMAGE_SIZES = ['1024*1024', '960*1280', '768*1024', '720*1280']
DEFAULT_IMAGE_SIZE = '1024*1024'


async def image_generator_node(state: CampaignState):
    _start_ts = start_node_log("image_generator", "🎨", state)
    _error = None
    try:
        writer = get_stream_writer()
        logger.info(f"⏳ [Node: ImageGen] 正在根据 Prompt 生成图片: {state['image_prompt'][:50]}...")
        writer({"type": "log", "content": f"🎨 [ImageGen] 正在根据 Prompt 生成图片..."})

        target_size = DEFAULT_IMAGE_SIZE
        if target_size not in SUPPORTED_IMAGE_SIZES:
            logger.warning(f"⚠️ 配置的尺寸 {target_size} 不合法，降级使用默认尺寸 1024*1024")
            target_size = '1024*1024'

        def _generate_image_sync():
            return dashscope.ImageSynthesis.call(model=settings.MODEL_IMAGE, prompt=state["image_prompt"], n=1,
                                                 size=target_size)

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
                        break
            logger.warning(f"⚠️ [Node: ImageGen] 第 {attempt + 1} 次画图失败，准备重试...")
            writer({"type": "log", "content": f"⚠️ 画图接口异常，第 {attempt + 1} 次重试中..."})
            await asyncio.sleep(2 ** attempt)

        image_url = None
        if remote_url:
            try:
                cfg = get_config()
                thread_id = cfg["configurable"]["thread_id"]
            except Exception:
                thread_id = str(id(state))
            writer({"type": "log", "content": "📥 正在持久化图片到本地..."})
            image_url = await download_and_save_image(remote_url, thread_id)
            if not image_url:
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


async def visual_reviewer_node(state: CampaignState):
    _start_ts = start_node_log("visual_reviewer", "🔍", state)
    _error = None
    try:
        writer = get_stream_writer()
        image_url = state.get("image_url")
        if not image_url:
            logger.warning("⚠️ [Node: VisualReviewer] 检测到 image_url 为空，跳过审查，默认放行防阻塞")
            return {"review_passed": True, "review_feedback": "图片生成失败，跳过视觉审查",
                    "retry_count": state.get("retry_count", 0) + 1}

        current_retry = state.get("retry_count", 0)
        attempt_label = f"第 {current_retry + 1} 次审查" if current_retry > 0 else "首次审查"
        writer({"type": "log", "content": f"🔍 [{attempt_label}] 正在调用视觉大模型进行图文一致性审查..."})
        logger.info("⏳ [Node: VisualReviewer] 正在进行图文一致性审查...")

        messages = [{"role": "user", "content": [{"image": state["image_url"]}, {
            "text": f"这是为以下营销文案生成的配图。请判断图片的内容是否与文案描述的场景/产品高度契合。\n文案：'{state['body']}'\n\n【强制要求】你必须以 JSON 格式返回，且必须包含布尔值字段 `passed` 和字符串字段 `feedback'。请简短回答，feedback不超过20个字。"}]}]

        def _vlm_review_sync():
            return dashscope.MultiModalConversation.call(model=settings.MODEL_VISION, messages=messages,
                                                         response_format={"type": "json_object"})

        try:
            response = await asyncio.to_thread(_vlm_review_sync)
            raw_text = response.output.choices[0].message.content[0]["text"]
            result_dict = json.loads(raw_text)
            is_passed = result_dict.get("passed", result_dict.get("is_passed", True))
            feedback = result_dict.get("feedback", result_dict.get("reason", "无反馈"))

            if is_passed:
                writer({"type": "log", "content": f"✅ [{attempt_label}] 审查通过！图文一致性良好。"})
            else:
                writer({"type": "log", "content": f"❌ [{attempt_label}] 审查未通过。反馈：{feedback}"})
            result = ReviewerSchema.model_validate({"passed": is_passed, "feedback": feedback})
        except Exception as e:
            writer({"type": "log", "content": "⚠️ 审查模型异常，触发兜底放行。"})
            return {"review_passed": True, "review_feedback": "", "retry_count": state.get("retry_count", 0) + 1}

        current_retry = state.get("retry_count", 0) + 1
        logger.info(f"✅ 审查通过: {result.passed}" if result.passed else f"❌ 审查未通过: {result.feedback}")
        writer({"type": "log", "content": f"视觉审核结果: {'通过' if result.passed else '未通过'}"})

        current_visual_fails = state.get("visual_fail_count", 0)
        if not result.passed:
            current_visual_fails += 1
            if current_visual_fails >= 2:
                cross_feedback = f"视觉模型连续{current_visual_fails}次无法根据文案生成合适图片。反馈：{result.feedback}。请修改正文，使其更容易被转化为画面。"
                writer({"type": "log", "content": "⚠️ [VLM] 连续画图失败，触发跨模态反思，准备修改文案！"})
                return {"review_passed": False, "review_feedback": result.feedback, "retry_count": current_retry,
                        "visual_fail_count": current_visual_fails, "cross_modal_feedback": cross_feedback}

        return {"review_passed": result.passed, "review_feedback": result.feedback, "retry_count": current_retry,
                "visual_fail_count": 0 if result.passed else current_visual_fails, "cross_modal_feedback": ""}
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("visual_reviewer", "🔍", _start_ts, error=_error)


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
        请结合反馈，修改画图提示词。
        """

        class PromptSchema(BaseModel):
            image_prompt: str

        result = await asyncio.to_thread(call_llm_with_schema, settings.MODEL_PROMPT_REWRITER, prompt, PromptSchema)
        logger.info(f"✨ 新的 Prompt: {result.image_prompt[:50]}...")
        return {"image_prompt": result.image_prompt}
    except Exception as e:
        _error = e
        raise
    finally:
        end_node_log("prompt_rewriter", "🔄", _start_ts, error=_error)


@asynccontextmanager
async def build_graph():
    workflow = StateGraph(CampaignState)
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("copywriter", copywriter_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("channel_adapter", channel_adapter_node)
    workflow.add_node("image_generator", image_generator_node)
    workflow.add_node("visual_reviewer", visual_reviewer_node)
    workflow.add_node("prompt_rewriter", prompt_rewriter_node)

    workflow.set_entry_point("researcher")
    workflow.add_edge("researcher", "copywriter")
    workflow.add_edge("copywriter", "critic")

    async def route_after_critic(state: CampaignState):
        if state.get("critic_passed") or state.get("copy_revision_count", 0) >= 3:
            return "adapt"
        return "rewrite"

    workflow.add_conditional_edges("critic", route_after_critic, {"rewrite": "copywriter", "adapt": "channel_adapter"})
    workflow.add_edge("channel_adapter", "image_generator")
    workflow.add_edge("image_generator", "visual_reviewer")

    async def route_after_review(state: CampaignState):
        if state.get("review_passed") or state.get("retry_count", 0) >= 4:
            return "end"
        if state.get("cross_modal_feedback"):
            return "cross_modal_rewrite"
        return "rewrite_prompt"

    workflow.add_conditional_edges("visual_reviewer", route_after_review,
                                   {"rewrite_prompt": "prompt_rewriter", "cross_modal_rewrite": "copywriter",
                                    "end": END})
    workflow.add_edge("prompt_rewriter", "image_generator")

    DB_URI = settings.POSTGRES_URI
    async with AsyncPostgresSaver.from_conn_string(DB_URI) as checkpointer:
        await checkpointer.setup()
        app = workflow.compile(interrupt_before=["image_generator"], checkpointer=checkpointer)
        yield app


if __name__ == '__main__':
    print("🚀 启动 V4 带人工审核的智能工作流...")
    app = build_graph()
    config = {"configurable": {"thread_id": "thread-1"}}
    initial_state = {"product_name": "极光智能保温杯", "key_features": "温显、超长保温、轻量化",
                     "target_audience": "户外爱好者、上班族"}

    print("\n--- 阶段1: 生成文案 ---")
    for event in app.astream(initial_state, config=config):
        for node_name, state_update in event.items():
            if "title" in state_update:
                print(f"📝 生成标题: {state_update['title']}")
                print(f"📝 生成正文: {state_update['body']}")

    current_state = app.get_state(config)
    print("\n" + "=" * 50)
    user_input = input("👨‍💻 请审查文案。是否满意并继续生成配图？ [y/n]\n")
    print("=" * 50 + "\n")

    if user_input.strip().lower() == 'y':
        print("👍 人工审核通过，继续执行图生视频/图片流...")
        final_image_url = None
        for event in app.astream(None, config=config):
            for node_name, state_update in event.items():
                print(f"⏳ 处理节点: {node_name}")
                if isinstance(state_update, dict) and "image_url" in state_update and state_update["image_url"]:
                    final_image_url = state_update["image_url"]

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