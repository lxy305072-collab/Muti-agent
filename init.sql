-- ==========================================
-- 1. 启用必要的扩展 (必须放在最前面)
-- ==========================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ==========================================
-- 2. 创建业务表
-- ==========================================

-- 产品库表
CREATE TABLE IF NOT EXISTS products (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    key_features    TEXT,
    target_audience TEXT,
    category        TEXT,
    brand           TEXT,
    extra_meta      JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 营销物料主表
CREATE TABLE IF NOT EXISTS materials (
    thread_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id      BIGINT REFERENCES products(id) ON DELETE SET NULL,

    -- 输入快照
    product_name    TEXT NOT NULL,
    key_features    TEXT,
    target_audience TEXT,

    -- 生成结果
    title           TEXT,
    body            TEXT,
    image_prompt    TEXT,
    image_url       TEXT,

    -- 审核与迭代
    review_passed   BOOLEAN DEFAULT FALSE,
    review_feedback TEXT,
    retry_count     INT DEFAULT 0,
    final_status    TEXT DEFAULT 'finished',

    -- 用户反馈
    user_rating     SMALLINT,
    user_comment    TEXT,

    -- 质量评分（历史排序、打分接口、few-shot 检索均依赖这三列）
    quality_score   SMALLINT DEFAULT 0,
    title_score     SMALLINT DEFAULT 0,
    body_score      SMALLINT DEFAULT 0,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

-- 迭代历史表
CREATE TABLE IF NOT EXISTS material_iterations (
    id            BIGSERIAL PRIMARY KEY,
    thread_id     UUID REFERENCES materials(thread_id) ON DELETE CASCADE,
    iter_no       INT NOT NULL,
    title         TEXT,
    body          TEXT,
    image_prompt  TEXT,
    image_url     TEXT,
    feedback      TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 知识库表 (品牌规范/优秀范例)
-- ⚠️ title 上的唯一约束是 seed_knowledge.py 幂等写入的前提。
--    已有数据库不会重跑本脚本，需手动执行一次：
--    ALTER TABLE knowledge_docs ADD CONSTRAINT uq_knowledge_docs_title UNIQUE (title);
--    （若表中已存在重复 title，需先清理重复行）
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id          BIGSERIAL PRIMARY KEY,
    doc_type    TEXT NOT NULL,
    title       TEXT UNIQUE,
    content     TEXT NOT NULL,
    meta        JSONB DEFAULT '{}',
    embedding   VECTOR(1024),
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 物料向量索引表
CREATE TABLE IF NOT EXISTS material_embeddings (
    thread_id  UUID PRIMARY KEY REFERENCES materials(thread_id) ON DELETE CASCADE,
    title      TEXT,
    body       TEXT,
    audience   TEXT,
    embedding  VECTOR(1024),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ==========================================
-- 3. 创建普通索引 (加速常规查询)
-- ==========================================
CREATE INDEX IF NOT EXISTS idx_materials_created ON materials(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_materials_status  ON materials(final_status);
CREATE INDEX IF NOT EXISTS idx_materials_product ON materials(product_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_type   ON knowledge_docs(doc_type);

-- ==========================================
-- 4. 创建向量索引 (HNSW算法，加速语义检索)
-- ==========================================
CREATE INDEX IF NOT EXISTS idx_me_embedding ON material_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_ke_embedding ON knowledge_docs
    USING hnsw (embedding vector_cosine_ops);