-- 任务与模板（30 §3.1、§3.4、§3.6；Q-1、Q-2、Q-6 默认值）。占号见 wzh_collab/migrations-占号.md（本工作流用偶数）。
-- 回滚：DROP 下列 6 张表即可，不改既有表。

-- 模板文档之间的上游关系（模板管理里可编辑，用于依赖链预填）
CREATE TABLE template_document_upstreams (
  template_document_id TEXT NOT NULL REFERENCES template_documents(id) ON DELETE CASCADE,
  upstream_id TEXT NOT NULL REFERENCES template_documents(id) ON DELETE CASCADE,
  PRIMARY KEY (template_document_id, upstream_id),
  CHECK (template_document_id <> upstream_id)
);

-- 项目内文档的上游链接（Q-1：模板预填，项目负责人可增删）；copied 记录创建时复制的章节，不做同步
CREATE TABLE document_upstreams (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  upstream_document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  source TEXT NOT NULL DEFAULT 'template' CHECK (source IN ('template','manual')),
  upstream_revision INTEGER,
  copied TEXT NOT NULL DEFAULT '[]',
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (document_id, upstream_document_id),
  CHECK (document_id <> upstream_document_id)
);
CREATE INDEX idx_document_upstreams_up ON document_upstreams(upstream_document_id);

-- 独立任务数据（Q-2）；开发计划书“任务分解”章节由此生成
CREATE TABLE project_tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  assignee_id TEXT REFERENCES users(id),
  display_order INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_project_tasks_project ON project_tasks(project_id, display_order);

-- 任务涉及的块：某文档的某个章节标题对象（A07 按标题链继承）
CREATE TABLE project_task_targets (
  task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  object_id TEXT NOT NULL,
  PRIMARY KEY (task_id, document_id, object_id)
);
CREATE INDEX idx_task_targets_doc ON project_task_targets(document_id, object_id);

-- 公共框架块：一处编辑，各文档引用（Q-6：立即生效，引用处提示有更新）
CREATE TABLE public_blocks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  framework_key TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  assignee_id TEXT REFERENCES users(id),
  revision INTEGER NOT NULL DEFAULT 0,
  updated_by TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (project_id, framework_key)
);

-- 引用处已核对到的公共块版本（用于“公共内容有更新”）
CREATE TABLE public_block_refs (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  public_block_id TEXT NOT NULL REFERENCES public_blocks(id) ON DELETE CASCADE,
  seen_revision INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (document_id, public_block_id)
);
