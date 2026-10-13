export interface WorkIssue {
  id: string;
  projectId: string;
  projectName: string;
  title: string;
  kind: "missing_material" | "fact_confirmation" | "review" | "impact" | "verification" | "export_error";
  status: "open" | "resolved";
  assigneeId: string | null;
  target: { kind: string; id?: string; name: string };
  updatedAt: string;
}

export interface WorkJob {
  id: string;
  projectId: string | null;
  kind: string;
  status: string;
  target: { kind: string; id?: string; name: string };
  stageName: string;
}
