-- 重构模板系统：模板改为文档组

-- 1. 修改 templates 表，使其成为模板组
ALTER TABLE templates ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE templates DROP COLUMN IF EXISTS source_file_id;
ALTER TABLE templates DROP COLUMN IF EXISTS current_version_id;
ALTER TABLE templates RENAME COLUMN scenario TO category;

-- 2. 创建模板文档表（模板组内的具体文档）
CREATE TABLE template_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  template_id UUID NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK(char_length(name) BETWEEN 1 AND 120),
  display_order INTEGER NOT NULL DEFAULT 0,
  word_file_id UUID NOT NULL REFERENCES files(id),
  format_template JSONB NOT NULL DEFAULT '{}'::jsonb,
  description TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(template_id, name)
);

CREATE INDEX idx_template_documents_template ON template_documents(template_id, display_order);
