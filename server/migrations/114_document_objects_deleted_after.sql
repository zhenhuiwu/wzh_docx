-- 阶段2 复审 M3：删除对象时记下“删除那一刻紧挨在它前面、仍然存在的对象”。
-- 恢复为新段落时按它定位；position 在删除后会随存活对象重排，不能再拿来比较。
-- '^' 表示删除时它就在文档最前面；NULL 表示本迁移之前删除的对象（恢复时回退到章节路径 + 序号）。
ALTER TABLE document_objects ADD COLUMN deleted_after_ref TEXT;
