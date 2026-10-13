import { getAllObjects, getAssignments } from "./assignments";

/**
 * 合并说明：本分支原有一套独立分工接口（同号迁移 118），合并 phase2 时统一到 A07。
 * 这里把 A07 的对象与生效负责人整理成“编写指导”面板使用的形状；分配负责人请用 A07 的分工面板。
 */
export interface DocumentAssignment {
  objectId: string;
  kind: string;
  text: string;
  assigneeId: string | null;
  assigneeName: string | null;
  canEdit: boolean;
}

export interface DocumentAssignmentsState {
  revision: number;
  assignmentsRevision: number;
  enabled: boolean;
  canManage: boolean;
  items: DocumentAssignment[];
  members: Array<{ id: string; name: string }>;
}

export async function getDocumentAssignments(documentId: string): Promise<DocumentAssignmentsState> {
  const [objects, view] = await Promise.all([getAllObjects(documentId), getAssignments(documentId)]);
  const resolved = new Map((view.resolved ?? []).map(item => [item.objectId, item]));
  return {
    revision: objects.documentRevision,
    assignmentsRevision: view.assignmentsRevision,
    enabled: view.assignmentMode === "enforced",
    canManage: false,
    members: [],
    items: objects.objects.map(item => {
      const owner = resolved.get(item.id);
      return { objectId: item.id, kind: item.kind, text: item.markdown, assigneeId: owner?.owners[0]?.id ?? null,
        assigneeName: owner?.owners.map(person => person.name).join("、") || null, canEdit: owner ? owner.editable : item.editable !== false };
    }),
  };
}

export async function updateDocumentAssignments(_documentId: string, _input: {
  expectedRevision: number;
  expectedAssignmentsRevision: number;
  enabled: boolean;
  items: Array<{ objectId: string; assigneeId: string | null }>;
}): Promise<DocumentAssignmentsState> {
  throw new Error("请在“分工”面板中分配负责人。");
}
