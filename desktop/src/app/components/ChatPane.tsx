import { FormEvent, KeyboardEvent, useMemo, useState } from "react";

import {
  activeSessionTitle,
  eventDetail,
  eventLabel,
  recentRunEvents,
  renderMessageParts,
  runStageMeta,
  workAreaSnapshot,
  type AppState,
} from "../appState";
import type { Translator } from "../i18n";
import { displayModelNameForModel } from "../modelDisplay";
import type { DockToolId } from "../useLucodeApp";
import { WorkAreaPanel } from "./WorkAreaPanel";
import type { ChatMessage, ModelSettingsResponse } from "../../shared/types";

export type ChatPaneProps = {
  t: Translator;
  state: AppState;
  input: string;
  runtimeError: string;
  modelSettings: ModelSettingsResponse | null;
  setInput: (value: string) => void;
  submit: (event: FormEvent) => void;
  stopRun: () => void;
  openDock: (tool: DockToolId) => void;
  openSettings: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
};

export function ChatPane({
  t,
  state,
  input,
  runtimeError,
  modelSettings,
  setInput,
  submit,
  stopRun,
  openDock,
  openSettings,
  updateRoleModel,
}: ChatPaneProps) {
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const stage = runStageMeta(state, t);
  const visibleEvents = recentRunEvents(state);
  const title = activeSessionTitle(state, t);
  const showStage = state.runStatus !== "idle";
  const snapshot = workAreaSnapshot(state, t);
  const configuredModels = useMemo(() => modelSettings?.models.filter((model) => model.configured) ?? [], [modelSettings]);
  const orchestratorRole = modelSettings?.roles.find((role) => role.role === "orchestrator");
  const showThinking = state.runStatus === "running" && !snapshot && !state.messages.some((message) => message.role === "assistant");
  const failedMessage = state.runStatus === "failed" ? lastFailedMessage(state.messages) : "";

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
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
          <button className="tool-pill-button" type="button" onClick={() => openDock("terminal")}>
            {t("chat.terminal")}
          </button>
          <button className="tool-pill-button" type="button" onClick={() => openDock("browser")}>
            {t("chat.browser")}
          </button>
          <button className="tool-pill-button" type="button" onClick={() => openDock("files")}>
            {t("chat.files")}
          </button>
          <button className="square-icon-button" type="button" title={t("common.settings")} aria-label={t("common.settings")} onClick={openSettings}>
            S
          </button>
        </div>
      </header>

      <section className="chat-scroll" aria-live="polite">
        <div className={state.messages.length === 0 ? "message-column empty" : "message-column"}>
          {state.messages.length === 0 ? (
            <div className="empty-chat-prompt">{t("chat.empty")}</div>
          ) : (
            state.messages.map((message) => <MessageBubble key={message.id} t={t} message={message} />)
          )}
          {showThinking ? <ThinkingIndicator t={t} /> : null}
          {snapshot ? <WorkAreaPanel t={t} snapshot={snapshot} /> : null}
          {state.runStatus === "failed" ? (
            <ErrorRecoveryPanel t={t} reason={failedMessage || runtimeError} openSettings={openSettings} />
          ) : null}
        </div>
      </section>

      {visibleEvents.length && state.runStatus === "running" ? (
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
        <div className="composer-shell">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder={t("chat.placeholder")}
            rows={3}
          />
          <div className="composer-footer">
            <span />
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
              <button className="send-circle-button" type="submit" disabled={!input.trim() || state.runStatus === "running"} aria-label={t("chat.send")}>
                &gt;
              </button>
            </div>
          </div>
        </div>
      </form>
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
  return (
    <article className={`message ${message.role} ${message.status || ""}`}>
      <div className="message-role">
        <span>{roleLabel(t, message.role)}</span>
        {message.status === "streaming" ? <small>{t("chat.generating")}</small> : null}
      </div>
      <div className="message-content">
        {renderMessageParts(message.content).map((part, index) =>
          part.type === "code" ? (
            <pre className="message-code" key={`${message.id}_code_${index}`}>
              {part.language ? <code className="code-language">{part.language}</code> : null}
              <code>{part.content}</code>
            </pre>
          ) : (
            <p key={`${message.id}_text_${index}`}>{part.content}</p>
          ),
        )}
      </div>
    </article>
  );
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
