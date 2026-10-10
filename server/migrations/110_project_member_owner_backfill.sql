-- 阶段2 S1：项目成员补齐（01 §3.3，决策7按 v3 收窄）。
-- (a) 完全没有成员行的旧项目：补 owner_id 用户为 owner。
INSERT INTO project_members (project_id, user_id, role)
SELECT p.id, p.owner_id, 'owner' FROM projects p
WHERE NOT EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = p.id);
-- (b) 项目没有任何 owner，且 owner_id 用户已有成员行：提升为 owner。
--     已有其他 owner（owner_id 用户被有意降级）或 owner_id 用户已被移出的项目不受影响。
UPDATE project_members SET role = 'owner', revision = revision + 1
WHERE role <> 'owner'
  AND user_id = (SELECT p.owner_id FROM projects p WHERE p.id = project_members.project_id)
  AND NOT EXISTS (SELECT 1 FROM project_members o
                  WHERE o.project_id = project_members.project_id AND o.role = 'owner');
