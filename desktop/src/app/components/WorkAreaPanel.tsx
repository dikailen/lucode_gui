import type { WorkAreaActivity, WorkAreaSnapshot, WorkAreaTask } from "../appState";
import type { Translator } from "../i18n";
import { buildWorkAreaGroups } from "../workAreaTree";

export type WorkAreaPanelProps = {
  t: Translator;
  snapshot: WorkAreaSnapshot;
};

const VISIBLE_ACTIVITY_COUNT = 3;

export function WorkAreaPanel({ t, snapshot }: WorkAreaPanelProps) {
  const groups = buildWorkAreaGroups(snapshot.tasks);
  const runningTasks = snapshot.tasks.filter((task) => task.status === "running").length;
  const taskNumbers = new Map(snapshot.tasks.map((task, index) => [task.id, index + 1]));
  const defaultOpen = snapshot.runStatus === "running";
  const processSummary = snapshot.processSummary || snapshot.collapsedSummary;
  const globalActivities = snapshot.globalActivities ?? [];

  return (
    <details className="work-area run-process compact-density" aria-label={t("workArea.aria")} open={defaultOpen}>
      <summary className="work-area-header run-process-summary-row">
        <div className="work-area-heading">
          <div className="work-area-title-row">
            <span className="work-area-caret" aria-hidden="true">
              ▾
            </span>
            <div className="work-area-title">{processSummary}</div>
            <span className="run-process-chevron" aria-hidden="true">
              &gt;
            </span>
          </div>
          <div className="work-area-subtitle">
            {t("workArea.routeSummary", {
              route: routeLabel(t, snapshot.routeType),
              groups: groups.length,
              count: snapshot.taskCount,
            })}
          </div>
        </div>
        <span className={`work-area-summary-chip ${runningTasks ? "running" : "idle"}`}>
          {runningTasks ? t("workArea.statusRunning") : snapshot.collapsedSummary}
        </span>
      </summary>
      <div className="work-area-collapsible-body">
        {snapshot.supervisorActivity ? <div className="supervisor-line">{snapshot.supervisorActivity}</div> : null}
        {globalActivities.length ? (
          <section className="global-activity-list" aria-label="global activities">
            <ActivityList t={t} activities={globalActivities} ariaLabel="global activities" />
          </section>
        ) : null}
        <div className="work-area-groups">
          {groups.map((group) => (
            <section className="work-area-group" key={group.parallelGroup}>
              <div className="work-area-group-label">
                <span>
                  {t("workArea.parallelGroup")} {group.parallelGroup}
                </span>
                <small>
                  {group.tasks.some((task) => task.status === "running")
                    ? t("workArea.groupRunning", { count: group.tasks.filter((task) => task.status === "running").length })
                    : group.tasks.some((task) => task.dependsOn.length > 0)
                      ? t("workArea.groupWaiting")
                      : t("workArea.groupCount", { count: group.tasks.length })}
                </small>
              </div>
              <div className="worker-list">
                {group.tasks.map((task, index) => (
                  <WorkerTaskRow
                    key={task.id}
                    t={t}
                    task={task}
                    index={taskNumbers.get(task.id) ?? index + 1}
                    isLast={index === group.tasks.length - 1}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </details>
  );
}

function WorkerTaskRow({
  t,
  task,
  index,
  isLast,
}: {
  t: Translator;
  task: WorkAreaTask;
  index: number;
  isLast: boolean;
}) {
  return (
    <article className={`worker-row ${task.status}`}>
      <div className="worker-row-rail" aria-hidden="true">
        <span className={isLast ? "worker-row-branch last" : "worker-row-branch"} />
        <span className={`worker-row-dot ${task.status}`} />
      </div>
      <div className="worker-row-main">
        <div className="worker-row-title">
          <span className="worker-row-title-text">
            {index}. {task.title}
          </span>
          <strong className={`worker-row-status ${task.status}`}>{statusLabel(t, task.status)}</strong>
        </div>
        <div className="worker-row-meta">
          {task.model ? <MetaChip tone="model" text={`${t("workArea.model")} ${task.model}`} /> : null}
          {task.mcp.map((name) => (
            <MetaChip key={`${task.id}_mcp_${name}`} tone="mcp" text={`MCP ${name}`} />
          ))}
          {task.dependsOn.length ? <MetaChip tone="dependency" text={`${t("workArea.dependsOn")} ${task.dependsOn.join(", ")}`} /> : null}
        </div>
        {task.latest ? <div className="worker-row-latest">{task.latest}</div> : null}
        {task.activities?.length ? (
          <ActivityList t={t} activities={task.activities} ariaLabel={`${task.title} activities`} />
        ) : null}
      </div>
    </article>
  );
}

function ActivityList({
  t,
  activities,
  ariaLabel,
}: {
  t: Translator;
  activities: WorkAreaActivity[];
  ariaLabel: string;
}) {
  const visibleActivities = activities.slice(0, VISIBLE_ACTIVITY_COUNT);
  const overflowActivities = activities.slice(VISIBLE_ACTIVITY_COUNT);

  return (
    <div className="worker-activity-list" aria-label={ariaLabel}>
      {visibleActivities.map((activity) => (
        <ActivityRow key={activity.id} activity={activity} />
      ))}
      {overflowActivities.length ? (
        <details className="worker-activity-overflow">
          <summary>{t("workArea.moreActivities", { count: overflowActivities.length })}</summary>
          <div className="worker-activity-overflow-body">
            {overflowActivities.map((activity) => (
              <ActivityRow key={activity.id} activity={activity} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function ActivityRow({ activity }: { activity: WorkAreaActivity }) {
  return (
    <div className={`worker-activity ${activity.kind} ${activity.status}`}>
      <span className="worker-activity-dot" aria-hidden="true" />
      <span className="worker-activity-title">{activity.title}</span>
      {activity.detail ? <span className="worker-activity-detail">{activity.detail}</span> : null}
    </div>
  );
}

function MetaChip({ tone, text }: { tone: "model" | "mcp" | "dependency"; text: string }) {
  return <span className={`worker-chip ${tone}`}>{text}</span>;
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

function routeLabel(t: Translator, routeType: string): string {
  if (routeType === "multi_agent") {
    return t("workArea.routeMultiAgent");
  }
  if (routeType === "single_agent") {
    return t("workArea.routeSingleAgent");
  }
  return routeType || t("workArea.routeTaskGraph");
}
