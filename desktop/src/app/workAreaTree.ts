import type { WorkAreaTask } from "./appState";

export type WorkAreaTaskGroup = {
  parallelGroup: string;
  tasks: WorkAreaTask[];
};

export function buildWorkAreaGroups(tasks: WorkAreaTask[]): WorkAreaTaskGroup[] {
  const groups = new Map<string, WorkAreaTask[]>();
  const order: string[] = [];
  for (const task of tasks) {
    const groupId = String(task.parallelGroup || "").trim() || "1";
    if (!groups.has(groupId)) {
      groups.set(groupId, []);
      order.push(groupId);
    }
    groups.get(groupId)!.push(task);
  }
  return order.map((parallelGroup) => ({
    parallelGroup,
    tasks: groups.get(parallelGroup) ?? [],
  }));
}
