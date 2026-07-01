import type { WorkAreaSnapshot, WorkAreaTask } from "../appState";
import type { Translator } from "../i18n";

export type WorkAreaPanelProps = {
  t: Translator;
  snapshot: WorkAreaSnapshot;
};

export function WorkAreaPanel({ t, snapshot }: WorkAreaPanelProps) {
  return (
    <section className="work-area" aria-label={t("workArea.aria")}>
      <header className="work-area-header">
        <div>
          <div className="work-area-title">{t("workArea.title")}</div>
          <div className="work-area-subtitle">
            {snapshot.routeType} · {snapshot.collapsedSummary}
          </div>
        </div>
        <span className="status-chip muted">{t("workArea.taskCount", { count: snapshot.taskCount })}</span>
      </header>
      {snapshot.supervisorActivity ? <div className="supervisor-line">{snapshot.supervisorActivity}</div> : null}
      <div className="worker-list">
        {snapshot.tasks.map((task, index) => (
          <WorkerTaskRow key={task.id} t={t} task={task} index={index + 1} />
        ))}
      </div>
    </section>
  );
}

function WorkerTaskRow({ t, task, index }: { t: Translator; task: WorkAreaTask; index: number }) {
  return (
    <article className={`worker-row ${task.status}`}>
      <div className="worker-row-dot" aria-hidden="true" />
      <div className="worker-row-main">
        <div className="worker-row-title">
          <span>
            {index}. {task.title}
          </span>
          <strong>{statusLabel(t, task.status)}</strong>
        </div>
        <div className="worker-row-meta">
          {task.model ? (
            <span>
              {t("workArea.model")} {task.model}
            </span>
          ) : null}
          {task.mcp.length ? <span>MCP {task.mcp.join(", ")}</span> : null}
          {task.dependsOn.length ? (
            <span>
              {t("workArea.dependsOn")} {task.dependsOn.join(", ")}
            </span>
          ) : null}
          {task.parallelGroup ? (
            <span>
              {t("workArea.parallelGroup")} {task.parallelGroup}
            </span>
          ) : null}
        </div>
        {task.latest ? <div className="worker-row-latest">{task.latest}</div> : null}
      </div>
    </article>
  );
}

function statusLabel(t: Translator, status: WorkAreaTask["status"]): string {
  if (status === "running") {
    return t("workArea.statusRunning");
  }
  if (status === "completed") {
    return t("workArea.statusCompleted");
  }
  if (status === "failed") {
    return t("workArea.statusFailed");
  }
  return t("workArea.statusWaiting");
}
