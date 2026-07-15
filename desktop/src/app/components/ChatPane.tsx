import { DragEvent, FormEvent, KeyboardEvent, useMemo, useState } from "react";

import {
  activeSessionTitle,
  eventDetail,
  eventLabel,
  recentRunEvents,
  renderMessageParts,
  runStageMeta,
  workAreaSnapshot,
  workAreaSnapshotFromMessage,
  type AppState,
} from "../appState";
import { buildChatTurns, type ChatTurn } from "../chatTurns";
import type { Translator } from "../i18n";
import { displayModelNameForModel } from "../modelDisplay";
import { runtimeToastItems } from "../runtimeActivity";
import type { RightDockWindowTool } from "../useLucodeApp";
import type { AttachmentDraft } from "../attachmentDrafts";
import { MarkdownContent } from "./MarkdownContent";
import { RuntimeToastStack } from "./RuntimeToastStack";
import { WorkAreaPanel } from "./WorkAreaPanel";
import type { ChatMessage, ModelSettingsModel, ModelSettingsResponse } from "../../shared/types";

export type ChatPaneProps = {
  t: Translator;
  state: AppState;
  input: string;
  attachmentDrafts: AttachmentDraft[];
  runStarting: boolean;
  runtimeError: string;
  modelSettings: ModelSettingsResponse | null;
  setInput: (value: string) => void;
  chooseAttachments: () => void;
  addDroppedAttachments: (files: FileList | File[]) => void;
  removeAttachment: (attachmentId: string) => void;
  submit: (event: FormEvent) => void;
  stopRun: () => void;
  bottomShellOpen: boolean;
  rightDockOpen: boolean;
  showRightDockHome: () => void;
  activateRightDockTool: (tool: RightDockWindowTool) => void;
  collapseRightDock: () => void;
  toggleBottomShell: () => void;
  openSettings: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
  updateModelReasoningEffort: (modelId: string, effort: string) => void;
};

export function ChatPane({
  t,
  state,
  input,
  attachmentDrafts,
  runStarting,
  runtimeError,
  modelSettings,
  setInput,
  chooseAttachments,
  addDroppedAttachments,
  removeAttachment,
  submit,
  stopRun,
  bottomShellOpen,
  rightDockOpen,
  showRightDockHome,
  activateRightDockTool,
  collapseRightDock,
  toggleBottomShell,
  openSettings,
  updateRoleModel,
  updateModelReasoningEffort,
}: ChatPaneProps) {
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [attachmentDragActive, setAttachmentDragActive] = useState(false);
  const composerBusy = state.runStatus === "running" || runStarting;
  const stage = runStageMeta(state, t);
  const visibleEvents = recentRunEvents(state);
  const toastItems = runtimeToastItems(state);
  const title = activeSessionTitle(state, t);
  const showStage = state.runStatus !== "idle";
  const snapshot = workAreaSnapshot(state, t);
  const historicalProcesses = useMemo(() => {
    return Object.fromEntries(
      state.messages.map((message) => [message.id, workAreaSnapshotFromMessage(message, t)]),
    );
  }, [state.messages, t]);
  const turns = useMemo(
    () => buildChatTurns(state.messages, snapshot, historicalProcesses),
    [state.messages, snapshot, historicalProcesses],
  );
  const activeTurnId = [...turns].reverse().find((turn) => turn.user)?.id || "";
  const activeTurn = turns.find((turn) => turn.id === activeTurnId);
  const configuredModels = useMemo(() => modelSettings?.models.filter((model) => model.configured) ?? [], [modelSettings]);
  const orchestratorRole = modelSettings?.roles.find((role) => role.role === "orchestrator");
  const selectedOrchestratorModel = configuredModels.find((model) => model.id === orchestratorRole?.selected_model_id);
  const showThinking =
    state.runStatus === "running" &&
    !snapshot &&
    Boolean(activeTurn?.user) &&
    !activeTurn?.responses.some((message) => message.role === "assistant");
  const failedMessage = state.runStatus === "failed" ? lastFailedMessage(state.messages) : "";

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  function handleComposerDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setAttachmentDragActive(false);
    if (!composerBusy && event.dataTransfer.files.length) {
      addDroppedAttachments(event.dataTransfer.files);
    }
  }

  return (
    <section className="chat-pane" aria-label={t("chat.aria")}>
      <header className="chat-header">
        <div className="chat-title-stack">
          <h1 className="chat-title">{title}</h1>
          {showStage ? (
            <div className={`chat-stage-line ${stage.tone}`}>
              <span className="stage-dot" />
              <span>{stage.label}</span>
              <span>{stage.detail}</span>
            </div>
          ) : null}
        </div>
        <div className="chat-header-actions">
          <button
            className={bottomShellOpen ? "workspace-toggle-button active" : "workspace-toggle-button"}
            type="button"
            title={t("chat.toggleShell")}
            aria-label={t("chat.toggleShell")}
            aria-pressed={bottomShellOpen}
            onClick={toggleBottomShell}
          >
            <span className="workspace-toggle-icon shell" aria-hidden="true" />
          </button>
          <button
            className={rightDockOpen ? "workspace-toggle-button active" : "workspace-toggle-button"}
            type="button"
            title={t("chat.toggleRightDock")}
            aria-label={t("chat.toggleRightDock")}
            aria-pressed={rightDockOpen}
            onClick={rightDockOpen ? collapseRightDock : showRightDockHome}
          >
            <span className="workspace-toggle-icon dock" aria-hidden="true" />
          </button>
        </div>
      </header>

      <RuntimeToastStack items={toastItems} onOpenReview={() => activateRightDockTool("review")} />

      <section className={toastItems.length ? "chat-scroll has-runtime-toast" : "chat-scroll"} aria-live="polite">
        <div className={turns.length === 0 ? "message-column empty" : "message-column"}>
          {turns.length === 0 ? (
            <div className="empty-chat-prompt">{t("chat.empty")}</div>
          ) : (
            turns.map((turn) => (
              <ChatTurnView
                key={turn.id}
                t={t}
                turn={turn}
                showThinking={showThinking && turn.id === activeTurnId}
              />
            ))
          )}
          {state.runStatus === "failed" ? (
            <ErrorRecoveryPanel t={t} reason={failedMessage || runtimeError} openSettings={openSettings} />
          ) : null}
        </div>
      </section>

      {visibleEvents.length && state.runStatus === "running" && toastItems.length === 0 ? (
        <section className="run-event-strip" aria-label={t("chat.events")}>
          {visibleEvents.map((event) => (
            <div className="run-event-item" key={`${event.run_id}_${event.seq}`}>
              <span>{eventLabel(event, t)}</span>
              {eventDetail(event, t) ? <small>{eventDetail(event, t)}</small> : null}
            </div>
          ))}
        </section>
      ) : null}

      {runtimeError ? <div className="runtime-error">{runtimeError}</div> : null}

      <form className="composer" onSubmit={submit}>
        <div
          className={[
            "composer-shell",
            attachmentDrafts.length ? "has-attachments" : "",
            attachmentDragActive ? "attachment-drag-active" : "",
          ].filter(Boolean).join(" ")}
          onDragEnter={(event) => {
            event.preventDefault();
            if (!composerBusy) setAttachmentDragActive(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setAttachmentDragActive(false)}
          onDrop={handleComposerDrop}
        >
          {attachmentDrafts.length ? (
            <div className="composer-attachment-tray" aria-label={t("chat.attachments")}>
              {attachmentDrafts.map((attachment) => (
                <span className="composer-attachment-chip" key={attachment.id} title={attachment.name}>
                  <span>{attachment.name}</span>
                  <button
                    type="button"
                    aria-label={t("chat.removeAttachment", { name: attachment.name })}
                    onClick={() => removeAttachment(attachment.id)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            readOnly={runStarting}
            placeholder={t("chat.placeholder")}
            rows={3}
          />
          <div className="composer-footer">
            <button
              className="composer-attachment-button"
              type="button"
              disabled={composerBusy}
              onClick={chooseAttachments}
              title={t("chat.attachFile")}
            >
              <span aria-hidden="true">+</span>
              {t("chat.attachFile")}
            </button>
            <div className="composer-actions">
              {state.runStatus === "running" ? (
                <button className="stop-button" type="button" onClick={stopRun}>
                  {t("chat.stop")}
                </button>
              ) : null}
              <div className="model-menu-host">
                <button
                  className="composer-model-button"
                  type="button"
                  onClick={() => setModelMenuOpen((current) => !current)}
                  title={t("chat.orchestratorModel", { model: state.modelLabel })}
                >
                  {t("chat.orchestratorModel", { model: state.modelLabel })}
                </button>
                {modelMenuOpen ? (
                  <div className="model-popover" role="menu">
                    <div className="model-popover-title">{t("chat.chooseOrchestrator")}</div>
                    {selectedOrchestratorModel?.reasoning_effort_levels.length ? (
                      <ReasoningEffortSelect
                        t={t}
                        model={selectedOrchestratorModel}
                        onChange={updateModelReasoningEffort}
                      />
                    ) : null}
                    {configuredModels.length === 0 ? (
                      <div className="model-popover-empty">{t("chat.noModels")}</div>
                    ) : (
                      configuredModels.map((model) => (
                        <button
                          key={model.id}
                          className={orchestratorRole?.selected_model_id === model.id ? "model-option active" : "model-option"}
                          type="button"
                          onClick={() => {
                            updateRoleModel("orchestrator", model.id);
                            setModelMenuOpen(false);
                          }}
                        >
                          <span>{displayModelNameForModel(model)}</span>
                          <small>{model.provider}</small>
                        </button>
                      ))
                    )}
                    <button className="model-settings-link" type="button" onClick={openSettings}>
                      {t("chat.openModelSettings")}
                    </button>
                  </div>
                ) : null}
              </div>
              <button className="send-circle-button" type="submit" disabled={!input.trim() || composerBusy} aria-label={t("chat.send")}>
                ↑
              </button>
            </div>
          </div>
        </div>
      </form>
    </section>
  );
}

function ReasoningEffortSelect({ t, model, onChange }: { t: Translator; model: ModelSettingsModel; onChange: (modelId: string, effort: string) => void }) {
  return (
    <label className="composer-reasoning-control">
      <span>{t("chat.reasoningEffort")}</span>
      <select value={model.selected_reasoning_effort || "auto"} onChange={(event) => onChange(model.id, event.target.value)}>
        {model.reasoning_effort_levels.map((effort) => <option key={effort} value={effort}>{t(`chat.reasoning.${effort}` as Parameters<Translator>[0])}</option>)}
      </select>
    </label>
  );
}

function ChatTurnView({
  t,
  turn,
  showThinking,
}: {
  t: Translator;
  turn: ChatTurn;
  showThinking: boolean;
}) {
  return (
    <section className={turn.user ? "chat-turn" : "chat-turn orphan"} data-chat-turn-id={turn.id}>
      {turn.user ? <MessageBubble t={t} message={turn.user} /> : null}
      {turn.process ? <WorkAreaPanel t={t} snapshot={turn.process} /> : null}
      {showThinking ? <ThinkingIndicator t={t} /> : null}
      {turn.responses.map((message) => (
        <MessageBubble key={message.id} t={t} message={message} />
      ))}
    </section>
  );
}

function ThinkingIndicator({ t }: { t: Translator }) {
  return (
    <div className="thinking-indicator">
      <span className="thinking-dot" />
      <span>{t("chat.thinking")}</span>
    </div>
  );
}

function ErrorRecoveryPanel({ t, reason, openSettings }: { t: Translator; reason: string; openSettings: () => void }) {
  return (
    <section className="error-recovery-panel" aria-label={t("chat.errorRecovery")}>
      <div>
        <div className="error-recovery-title">{t("chat.runFailed")}</div>
        <p>{reason || t("chat.runFailedFallback")}</p>
      </div>
      <button className="secondary-button" type="button" onClick={openSettings}>
        {t("chat.modelSettings")}
      </button>
    </section>
  );
}

function MessageBubble({ t, message }: { t: Translator; message: ChatMessage }) {
  const isAssistant = message.role === "assistant";
  const attachments = messageAttachmentItems(message.metadata);
  return (
    <article className={`message ${message.role} ${message.status || ""}`}>
      {!isAssistant || message.status === "streaming" ? (
        <div className="message-role">
          <span>{roleLabel(t, message.role)}</span>
          {message.status === "streaming" ? <small>{t("chat.generating")}</small> : null}
        </div>
      ) : null}
      <div className="message-content">
        {isAssistant ? (
          <MarkdownContent content={message.content} />
        ) : (
          renderMessageParts(message.content).map((part, index) =>
            part.type === "code" ? (
              <pre className="message-code" key={`${message.id}_code_${index}`}>
                {part.language ? <code className="code-language">{part.language}</code> : null}
                <code>{part.content}</code>
              </pre>
            ) : (
              <p key={`${message.id}_text_${index}`}>{part.content}</p>
            ),
          )
        )}
      </div>
      {attachments.length ? (
        <div className="message-attachment-list" aria-label={t("chat.attachments")}>
          {attachments.map((attachment, index) => (
            <span className="message-attachment" key={`${message.id}_attachment_${index}`} title={attachment.name}>
              <span>{attachment.name}</span>
              {attachment.sizeBytes > 0 ? <small>{formatAttachmentSize(attachment.sizeBytes)}</small> : null}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function messageAttachmentItems(metadata: ChatMessage["metadata"]): Array<{ name: string; sizeBytes: number }> {
  const value = metadata?.attachments;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const record = item as Record<string, unknown>;
    const name = typeof record.name === "string" ? record.name.trim() : "";
    if (!name) {
      return [];
    }
    return [{
      name,
      sizeBytes: typeof record.size_bytes === "number" ? record.size_bytes : 0,
    }];
  });
}

function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function roleLabel(t: Translator, role: "user" | "assistant" | "system"): string {
  if (role === "user") {
    return t("chat.roleUser");
  }
  if (role === "assistant") {
    return t("chat.roleAssistant");
  }
  return t("chat.roleSystem");
}

function lastFailedMessage(messages: ChatMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.status === "failed") {
      return message.content;
    }
  }
  return "";
}
