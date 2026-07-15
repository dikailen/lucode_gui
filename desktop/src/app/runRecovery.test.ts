import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { selectActiveRunForRenderer } from "./runRecovery";

describe("run recovery discovery", () => {
  it("prefers the active run for the current session and never returns terminal runs", () => {
    const selected = selectActiveRunForRenderer([
      { run_id: "run_done", session_id: "session_1", status: "completed", updated_at: "2026-07-14T09:00:00Z" },
      { run_id: "run_other", session_id: "session_2", status: "running", updated_at: "2026-07-14T10:00:00Z" },
      { run_id: "run_current", session_id: "session_1", status: "running", updated_at: "2026-07-14T09:30:00Z" },
    ], "session_1");

    expect(selected?.run_id).toBe("run_current");
  });

  it("keeps interrupted-run bookkeeping out of the ordinary chat composer", () => {
    const chatPane = readFileSync(resolve(__dirname, "components/ChatPane.tsx"), "utf8");
    const workspacePanel = readFileSync(resolve(__dirname, "components/WorkspacePanel.tsx"), "utf8");
    const app = readFileSync(resolve(__dirname, "App.tsx"), "utf8");
    const controller = readFileSync(resolve(__dirname, "useLucodeApp.ts"), "utf8");
    const i18n = readFileSync(resolve(__dirname, "i18n.ts"), "utf8");
    const styles = readFileSync(resolve(__dirname, "../styles.css"), "utf8");

    expect(chatPane).not.toContain("run-recovery-bar");
    expect(chatPane).not.toContain("recoveryRuns");
    expect(chatPane).not.toContain("recoveryContinue");
    expect(chatPane).not.toContain("recoveryReplan");
    expect(chatPane).not.toContain("recoveryAbandon");
    expect(workspacePanel).not.toContain("recoveryRuns");
    expect(app).not.toContain("recoveryRuns");
    expect(controller).not.toContain("recoveryRuns");
    expect(controller).not.toMatch(/listRecoveryRuns\s*\(/);
    expect(i18n).not.toContain("chat.recovery");
    expect(styles).not.toContain(".run-recovery-bar");
  });

  it("falls back to the most recently updated running run", () => {
    const selected = selectActiveRunForRenderer([
      { run_id: "run_old", session_id: "session_1", status: "running", updated_at: "2026-07-14T09:00:00Z" },
      { run_id: "run_new", session_id: "session_2", status: "running", updated_at: "2026-07-14T10:00:00Z" },
    ]);

    expect(selected?.run_id).toBe("run_new");
  });

});
