-- 重构文档系统：简化文档结构，支持Markdown编辑

-- 1. 修改 documents 表
ALTER TABLE documents DROP COLUMN IF EXISTS template_version_id;
ALTER TABLE documents DROP COLUMN IF EXISTS purpose;
ALTER TABLE documents DROP COLUMN IF EXISTS validity;
ALTER TABLE documents DROP COLUMN IF EXISTS state;

-- 2. 添加新字段
ALTER TABLE documents ADD COLUMN source_type TEXT NOT NULL DEFAULT 'template'
  CHECK(source_type IN ('template', 'upload'));
ALTER TABLE documents ADD COLUMN template_document_id UUID REFERENCES template_documents(id);
ALTER TABLE documents ADD COLUMN original_docx_file_id UUID REFERENCES files(id);
ALTER TABLE documents ADD COLUMN format_template JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE documents ADD COLUMN markdown_content TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN outline JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'
  CHECK(status IN ('draft', 'in_progress', 'completed'));

-- 3. 移除旧的唯一约束，添加新的
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_project_id_name_purpose_key;
ALTER TABLE documents ADD CONSTRAINT documents_project_id_name_key UNIQUE(project_id, name);

-- 4. 创建索引
CREATE INDEX idx_documents_project_status ON documents(project_id, status);
CREATE INDEX idx_documents_template_doc ON documents(template_document_id);
