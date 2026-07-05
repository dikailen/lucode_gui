import type { AppState } from "../appState";
import {
  runtimeReviewSnapshot,
  type RuntimeActivityItem,
  type RuntimeApprovalRequest,
} from "../runtimeActivity";
import type { RunApprovalDecision } from "../../shared/types";

export type ReviewPanelProps = {
  state: AppState;
  openBrowser: () => void;
  resolveApproval: (runId: string, approvalId: string, decision: RunApprovalDecision) => void;
};

export function ReviewPanel({ state, openBrowser, resolveApproval }: ReviewPanelProps) {
  const snapshot = runtimeReviewSnapshot(state);
  const browserTarget = snapshot.items.find((item) => item.kind === "browser" && item.url);

  return (
    <section className="review-panel" aria-label="运行审计面板">
      <div className="review-summary-grid">
        <SummaryCard label="Run" value={shortRunId(snapshot.runId) || "暂无"} />
        <SummaryCard label="浏览器" value={String(snapshot.counts.browser)} />
        <SummaryCard label="审批" value={String(snapshot.counts.approval)} />
        <SummaryCard label="失败" value={String(snapshot.counts.failed)} tone={snapshot.counts.failed ? "danger" : "normal"} />
      </div>

      <section className="review-current-card">
        <div>
          <div className="review-current-title">当前运行状态</div>
          <p>{runStatusLabel(snapshot.runStatus)}</p>
          {snapshot.latestAt ? <small>最后事件：{formatTime(snapshot.latestAt)}</small> : null}
        </div>
        {browserTarget ? (
          <button className="secondary-button" type="button" onClick={openBrowser}>
            打开浏览器
          </button>
        ) : null}
      </section>

      {browserTarget ? (
        <section className="review-browser-card">
          <div className="review-section-label">浏览器目标</div>
          <div className="review-url-line" title={browserTarget.url}>
            {browserTarget.url}
          </div>
          {browserTarget.selector ? <div className="review-selector-line">selector: {browserTarget.selector}</div> : null}
        </section>
      ) : null}

      {snapshot.pendingApprovals.length ? (
        <section className="review-approval-list" aria-label="待审批操作">
          <div className="review-section-label">待审批操作</div>
          {snapshot.pendingApprovals.map((approval) => (
            <ApprovalCard approval={approval} key={approval.approvalId} resolveApproval={resolveApproval} />
          ))}
        </section>
      ) : null}

      <section className="review-event-list" aria-label="工具与浏览器活动">
        <div className="review-section-label">工具与浏览器活动</div>
        {snapshot.items.length ? (
          snapshot.items.map((item) => <ReviewEventRow item={item} key={item.id} />)
        ) : (
          <div className="review-empty">
            暂无运行活动。开始一次 Agent 运行后，这里会显示工具、浏览器和审批记录。
          </div>
        )}
      </section>
    </section>
  );
}

function ApprovalCard({
  approval,
  resolveApproval,
}: {
  approval: RuntimeApprovalRequest;
  resolveApproval: (runId: string, approvalId: string, decision: RunApprovalDecision) => void;
}) {
  return (
    <article className="review-approval-card">
      <div className="review-approval-header">
        <div>
          <strong>{approvalActionLabel(approval.action)}</strong>
          <p>{approval.prompt || "需要你确认这次工具操作"}</p>
        </div>
        <span>{formatTime(approval.timestamp)}</span>
      </div>
      <div className="review-approval-fields">
        <ApprovalField label="目标页面" value={approval.title || approval.url || "未提供"} />
        {approval.url ? <ApprovalField label="URL" value={approval.url} /> : null}
        <ApprovalField label="Selector" value={approval.selector || "未提供"} code />
        <ApprovalField label="风险" value={approval.risk || approvalRiskFallback(approval.action)} />
      </div>
      <div className="review-approval-actions">
        <button
          className="secondary-button"
          type="button"
          onClick={() => resolveApproval(approval.runId, approval.approvalId, "reject")}
        >
          拒绝
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={() => resolveApproval(approval.runId, approval.approvalId, "approve")}
        >
          批准
        </button>
      </div>
    </article>
  );
}

function ApprovalField({ label, value, code = false }: { label: string; value: string; code?: boolean }) {
  return (
    <div className={code ? "review-approval-field code" : "review-approval-field"}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function SummaryCard({ label, value, tone = "normal" }: { label: string; value: string; tone?: "normal" | "danger" }) {
  return (
    <div className={tone === "danger" ? "review-summary-card danger" : "review-summary-card"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReviewEventRow({ item }: { item: RuntimeActivityItem }) {
  return (
    <article className={`review-event-row ${item.kind} ${item.status}`}>
      <span className="review-event-dot" aria-hidden="true" />
      <div className="review-event-main">
        <div className="review-event-title-line">
          <strong>{item.title}</strong>
          <span>{statusLabel(item.status)}</span>
        </div>
        <div className="review-event-detail" title={item.detail}>
          {item.detail || "无详细信息"}
        </div>
        <div className="review-event-meta">
          <span>{kindLabel(item.kind)}</span>
          {item.url ? <span title={item.url}>URL</span> : null}
          {item.selector ? <span title={item.selector}>selector</span> : null}
          {item.timestamp ? <span>{formatTime(item.timestamp)}</span> : null}
        </div>
      </div>
    </article>
  );
}

function shortRunId(runId: string): string {
  if (!runId) {
    return "";
  }
  return runId.length > 12 ? `${runId.slice(0, 4)}...${runId.slice(-5)}` : runId;
}

function runStatusLabel(status: AppState["runStatus"]): string {
  if (status === "running") {
    return "Agent 正在运行";
  }
  if (status === "completed") {
    return "本轮运行已完成";
  }
  if (status === "failed") {
    return "本轮运行失败";
  }
  if (status === "cancelled") {
    return "本轮运行已停止";
  }
  return "等待新的运行";
}

function statusLabel(status: RuntimeActivityItem["status"]): string {
  if (status === "waiting") {
    return "等待审批";
  }
  if (status === "done") {
    return "完成";
  }
  if (status === "failed") {
    return "失败";
  }
  return "执行中";
}

function kindLabel(kind: RuntimeActivityItem["kind"]): string {
  if (kind === "browser") {
    return "desktop_browser";
  }
  if (kind === "approval") {
    return "approval";
  }
  if (kind === "error") {
    return "error";
  }
  return "tool";
}

function approvalActionLabel(action: string): string {
  if (action === "browser_click_element") {
    return "点击页面元素";
  }
  if (action === "browser_set_input_value") {
    return "填写页面输入";
  }
  if (action === "browser_submit_form") {
    return "提交页面表单";
  }
  return action || "工具操作";
}

function approvalRiskFallback(action: string): string {
  if (action === "browser_submit_form") {
    return "可能提交页面表单";
  }
  if (action === "browser_set_input_value") {
    return "将写入页面输入框";
  }
  if (action === "browser_click_element") {
    return "将点击当前页面元素";
  }
  return "审批后才会继续执行";
}

function formatTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp));
}
