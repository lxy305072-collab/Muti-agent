# db_repo.py
import asyncio

import asyncpg
import os
import json
from typing import List, Dict, Optional

import dashscope

from config import settings


class DatabaseRepo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def upsert_material(self, thread_id: str, product_name: str, key_features: str,
                              target_audience: str, title: str, body: str, image_prompt: str):
        """新增或更新物料基础信息"""
        await self.pool.execute("""
            INSERT INTO materials (thread_id, product_name, key_features, target_audience, title, body, image_prompt)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (thread_id) DO UPDATE SET
                title=EXCLUDED.title, body=EXCLUDED.body, image_prompt=EXCLUDED.image_prompt, updated_at=now()
        """, thread_id, product_name, key_features, target_audience, title, body, image_prompt)


    async def finalize_material(self, thread_id: str, title: str, body: str, image_prompt: str,
                                image_url: str, review_passed: bool, review_feedback: str, retry_count: int):
        """最终完成时，更新物料状态和图片URL"""
        await self.pool.execute("""
            UPDATE materials SET
                title=$2, body=$3, image_prompt=$4, image_url=$5,
                review_passed=$6, review_feedback=$7, retry_count=$8,
                final_status='finished', finished_at=now(), updated_at=now()
            WHERE thread_id=$1
        """, thread_id, title, body, image_prompt, image_url, review_passed, review_feedback, retry_count)

    async def list_recent(self, limit: int = 6) -> List[Dict]:
        """获取最近的历史物料，按评分降序优先展示"""
        rows = await self.pool.fetch("""
            SELECT thread_id::text, title, body, image_url, image_prompt, retry_count, quality_score
            FROM materials
            WHERE final_status='finished' AND image_url IS NOT NULL
            ORDER BY quality_score DESC, finished_at DESC LIMIT $1
        """, limit)
        return [dict(r) for r in rows]

    async def delete_material(self, thread_id: str):
        """删除物料（会级联删除迭代记录和向量记录）"""
        await self.pool.execute("DELETE FROM materials WHERE thread_id=$1", thread_id)

    async def save_embedding(self, thread_id: str, title: str, body: str, audience: str, embedding: List[float]):
        """保存向量数据"""
        # 注意：asyncpg 原生不支持 pgvector，需要把 list 转成字符串
        vec_str = "[" + ",".join(map(str, embedding)) + "]"
        await self.pool.execute("""
            INSERT INTO material_embeddings (thread_id, title, body, audience, embedding)
            VALUES ($1, $2, $3, $4, $5::vector)
            ON CONFLICT (thread_id) DO UPDATE SET
                title=EXCLUDED.title, body=EXCLUDED.body, audience=EXCLUDED.audience, embedding=EXCLUDED.embedding
        """, thread_id, title, body, audience, vec_str)


    # 🔥 新增：更新用户评分
    async def update_quality_score(self, thread_id: str, score: int):
        """更新用户打分"""
        await self.pool.execute("""
            UPDATE materials SET
                quality_score=$2,
                title_score=$2,
                body_score=$2,
                updated_at=now()
            WHERE thread_id=$1
        """, thread_id, score)


    # 🔥 新增：检查是否已经生成过向量
    async def check_embedding_exists(self, thread_id: str) -> bool:
        """检查该物料是否已存入向量表"""
        row = await self.pool.fetchval("""
                                       SELECT EXISTS(SELECT 1 FROM material_embeddings WHERE thread_id = $1)
                                       """, thread_id)
        return row


    async def search_knowledge(self, query: str, doc_types: list, k: int = 3) -> list:
        """从知识库检索相关文档（品牌规范/优秀范例等）"""
        # 1. 生成查询向量
        rsp = await asyncio.to_thread(
            dashscope.TextEmbedding.call,
            model="text-embedding-v3",
            input=query,
            dimension=1024,
            output_type="dense"
        )
        if rsp.status_code != 200:
            return []
        qvec = rsp.output["embeddings"][0]["embedding"]
        vec_str = "[" + ",".join(map(str, qvec)) + "]"

        # 2. 执行向量检索
        # noinspection SqlResolve
        rows = await self.pool.fetch("""
                                     SELECT id,
                                            doc_type,
                                            title,
                                            content,
                                            1 - (embedding <=> $1::vector) AS score
                                     FROM knowledge_docs
                                     WHERE doc_type = ANY ($2)
                                     ORDER BY embedding <=> $1::vector
                                     LIMIT $3
                                     """, vec_str, doc_types, k)
        return [dict(r) for r in rows]

    async def search_similar_materials(self, query: str, k: int = 2) -> list:
        """检索历史相似优秀物料，作为 few-shot 范例 (仅搜索评分>=90的)"""
        rsp = await asyncio.to_thread(
            dashscope.TextEmbedding.call,
            model="text-embedding-v3",
            input=query,
            dimension=1024,
            output_type="dense"
        )
        if rsp.status_code != 200:
            return []
        qvec = rsp.output["embeddings"][0]["embedding"]
        vec_str = "[" + ",".join(map(str, qvec)) + "]"

        # 🔥 修改点：将 LIMIT $2 改大，召回 k*3 条
        recall_k = k * 3
        rows = await self.pool.fetch("""
                                     SELECT m.thread_id,
                                            m.title,
                                            m.body,
                                            m.image_prompt,
                                            1 - (e.embedding <=> $1::vector) AS score
                                     FROM material_embeddings e
                                              JOIN materials m ON m.thread_id = e.thread_id
                                     WHERE m.final_status = 'finished'
                                       AND m.quality_score >= 90
                                     ORDER BY e.embedding <=> $1::vector
                                     LIMIT $2
                                     """, vec_str, recall_k)
        return [dict(r) for r in rows]


    # 🔥 新增：调用 LLM 进行重排序的独立方法 (替代 gte-rerank)
    async def rerank_documents(self, query: str, documents: list, top_n: int = 1) -> list:
        """
        使用 LLM (大模型) 对召回的文档进行重排序
        :param query: 用户查询词
        :param documents: 召回的文档列表 (字典列表)
        :param top_n: 重排后返回前几个
        :return: 重排后的 top_n 文档
        """
        if not documents:
            return []

        # 1. 构造 Re-rank 输入数据
        doc_texts = []
        for i, doc in enumerate(documents):
            if 'content' in doc:  # 知识库文档
                text = f"标题: {doc.get('title', '')}\n内容: {doc.get('content', '')}"
            else:  # 历史物料
                text = f"标题: {doc.get('title', '')}\n正文: {doc.get('body', '')}"
            doc_texts.append(f"[{i}] {text}")

        try:
            # 2. 构造 Prompt，让大模型选出最相关的索引
            prompt = f"""
            你是一个智能分析助手。请根据用户的【查询词】，从下方的【候选信息列表】中选出最相关的 {top_n} 条信息。
            你只需要返回一个 JSON，格式为：{{"best_indices": [序号1, 序号2]}}。绝对不要输出其他任何解释性文字。

            【查询词】：{query}

            【候选信息列表】：
            {chr(10).join(doc_texts)}
            """

            # 3. 调用轻量级大模型 (qwen-turbo 速度快、成本低)
            response = await asyncio.to_thread(
                dashscope.Generation.call,
                model="qwen-turbo",
                prompt=prompt,
                response_format={"type": "json_object"}
            )

            if response.status_code == 200:
                # 兼容不同版本 SDK 的返回结构
                text_content = None
                if hasattr(response.output, 'choices') and response.output.choices:
                    if response.output.choices[0] and hasattr(response.output.choices[0], 'message'):
                        text_content = response.output.choices[0].message.content
                if text_content is None and hasattr(response.output, 'text'):
                    text_content = response.output.text

                if text_content:
                    result_dict = json.loads(text_content)
                    best_indices = result_dict.get("best_indices", [])[:top_n]

                    reranked_results = []
                    for idx in best_indices:
                        if isinstance(idx, int) and 0 <= idx < len(documents):
                            doc = documents[idx]
                            doc['rerank_score'] = 1.0  # 模拟一个满分得分，保持数据结构一致
                            reranked_results.append(doc)

                    if reranked_results:
                        return reranked_results
                    else:
                        print("⚠️ LLM Rerank 未返回有效索引，降级使用原始向量检索结果")
                        return documents[:top_n]
            else:
                print(f"⚠️ LLM Rerank 调用失败，降级使用原始结果: {response.code} - {response.message}")
                return documents[:top_n]

        except Exception as e:
            print(f"⚠️ LLM Rerank 异常，降级使用原始结果: {e}")
            return documents[:top_n]






    async def get_material_for_embedding(self, thread_id: str) -> dict:
        row = await self.pool.fetchrow("""
                                       SELECT title, body
                                       FROM materials
                                       WHERE thread_id = $1
                                       """, thread_id)
        return dict(row) if row else None










