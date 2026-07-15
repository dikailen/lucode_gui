import type { ServerRun } from "../shared/types";

type ActiveRunCandidate = Pick<ServerRun, "run_id" | "session_id" | "status" | "updated_at">;

export function selectActiveRunForRenderer(
  runs: readonly ActiveRunCandidate[],
  preferredSessionId = "",
): ActiveRunCandidate | null {
  const activeRuns = runs
    .filter((run) => run.status === "running")
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at) || right.run_id.localeCompare(left.run_id));
  return activeRuns.find((run) => run.session_id === preferredSessionId) || activeRuns[0] || null;
}
