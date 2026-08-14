# seed_knowledge.py
import asyncio
import asyncpg
import dashscope
from config import settings  # 🔥 引入配置中心

# 从配置中心读取
dashscope.api_key = settings.DASHSCOPE_API_KEY
DB_URI = settings.POSTGRES_URI

# 准备要存入的知识库数据 (你可以在这里多加几条通用的范例)
KNOWLEDGE_DATA = [
    {
        "doc_type": "brand_guide",
        "title": "公司品牌调性规范",
        "content": "我们公司的品牌调性是：年轻、活力、科技感。文案中多使用emoji，语气要像朋友交流一样亲切，避免使用过于官方和死板的词汇。绝对不能出现“全网最低价”等违反广告法的极限词。"
    },
    {
        "doc_type": "good_example",
        "title": "优秀文案范例-科技产品",
        "content": "标题：🚀告别线缆束缚，轻享纯净音质！正文：还在为耳机线缠绕烦恼吗？这款蓝牙耳机，轻如羽毛，戴上一整天耳朵不痛。超长续航，陪你从早嗨到晚！#无线自由 #音乐随身听"
    },
    {
        "doc_type": "visual_guide",
        "title": "配图视觉规范",
        "content": "产品配图必须明亮、清晰。背景优先选择纯色或自然户外场景。图中如果有人物，人物必须表现出开心、享受生活的状态。避免暗黑系或过于抽象的画风。"
    }
]

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
    raise Exception(f"获取向量失败: {rsp.message}")

async def main():
    pool = await asyncpg.create_pool(dsn=DB_URI)
    print("🔗 已连接数据库，开始写入知识库...")

    for doc in KNOWLEDGE_DATA:
        print(f"正在向量化: {doc['title']}...")
        embedding = await get_embedding(doc['content'])
        vec_str = "[" + ",".join(map(str, embedding)) + "]"

        async with pool.acquire() as conn:
            # 🔥 优化：使用 ON CONFLICT 防止重复运行脚本时数据重复插入
            await conn.execute("""
                INSERT INTO knowledge_docs (doc_type, title, content, embedding)
                VALUES ($1, $2, $3, $4::vector)
                ON CONFLICT (title) DO UPDATE SET 
                    content=EXCLUDED.content, 
                    embedding=EXCLUDED.embedding
            """, doc['doc_type'], doc['title'], doc['content'], vec_str)
        print(f"✅ 已写入: {doc['title']}")

    await pool.close()
    print("🎉 知识库初始化完成！")

if __name__ == "__main__":
    asyncio.run(main())