-- 删除不再使用的表

-- 文档块相关（不再按块管理）
DROP TABLE IF EXISTS block_revisions CASCADE;
DROP TABLE IF EXISTS document_blocks CASCADE;

-- 模板版本（不再有版本概念）
DROP TABLE IF EXISTS template_versions CASCADE;

-- 文档计划（简化后不需要）
DROP TABLE IF EXISTS document_plans CASCADE;

-- 事实和资料管理（去除）
DROP TABLE IF EXISTS fact_sources CASCADE;
DROP TABLE IF EXISTS fact_revisions CASCADE;
DROP TABLE IF EXISTS facts CASCADE;

-- 项目知识库绑定（去除）
DROP TABLE IF EXISTS project_knowledge_bindings CASCADE;

-- 项目成员（去除）
DROP TABLE IF EXISTS project_members CASCADE;

-- 审核（去除）
DROP TABLE IF EXISTS reviews CASCADE;
