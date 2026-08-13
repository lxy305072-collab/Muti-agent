# db_repo.py
import asyncio

import asyncpg
import os
import json
from typing import List, Dict, Optional

import dashscope


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


    # 🔥 新增：调用 DashScope Re-rank 模型的独立方法
    async def rerank_documents(self, query: str, documents: list, top_n: int = 1) -> list:
        """
        使用 DashScope gte-rerank 模型对召回的文档进行重排序
        :param query: 用户查询词
        :param documents: 召回的文档列表 (字典列表)
        :param top_n: 重排后返回前几个
        :return: 重排后的 top_n 文档
        """
        if not documents:
            return []

        # 1. 构造 Re-rank 输入数据
        # 将字典内容拼接成纯文本，模型才能理解
        doc_texts = []
        for doc in documents:
            if 'content' in doc:  # 知识库文档
                text = f"标题: {doc.get('title', '')}\n内容: {doc.get('content', '')}"
            else:  # 历史物料
                text = f"标题: {doc.get('title', '')}\n正文: {doc.get('body', '')}"
            doc_texts.append(text)

        try:
            # 2. 调用 DashScope Re-rank API (同步转异步)
            rsp = await asyncio.to_thread(
                dashscope.TextReRank.call,
                model="gte-rerank",  # 阿里云通用重排模型
                query=query,
                documents=doc_texts,
                top_n=top_n,
                return_documents=False  # 只返回 index 和 score，不返回原文，省 token
            )

            if rsp.status_code == 200 and rsp.output:
                reranked_results = []
                # 3. 根据 rerank 返回的 index，重新组织原始数据
                for item in rsp.output.results:
                    original_doc_index = item.index
                    relevance_score = item.relevance_score
                    # 取出原始完整字典，并附加重排序得分
                    doc_with_score = documents[original_doc_index]
                    doc_with_score['rerank_score'] = relevance_score
                    reranked_results.append(doc_with_score)
                return reranked_results
            else:
                print(f"⚠️ Rerank 调用失败，降级使用原始向量检索结果: {rsp.code} - {rsp.message}")
                # 降级策略：如果 rerank 挂了，直接返回原始召回的前 top_n 个
                return documents[:top_n]
        except Exception as e:
            print(f"⚠️ Rerank 异常，降级使用原始向量检索结果: {e}")
            return documents[:top_n]









    async def get_material_for_embedding(self, thread_id: str) -> dict:
        row = await self.pool.fetchrow("""
                                       SELECT title, body
                                       FROM materials
                                       WHERE thread_id = $1
                                       """, thread_id)
        return dict(row) if row else None










