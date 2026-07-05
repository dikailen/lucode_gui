import { type DragEvent, type FormEvent, useEffect, useState } from "react";

import type { Translator } from "../i18n";
import type {
  ComfyUiStateResponse,
  ExternalMcpPayload,
  PluginMcpRow,
  PluginRuntimeCapability,
  PluginSkill,
  PluginStateResponse,
} from "../../shared/types";

type RuntimeCapabilityCardModel = {
  id: string;
  description: string;
  status: string;
  abilities: string[];
  risk: string;
};

export type PluginsPanelProps = {
  t: Translator;
  pluginState: PluginStateResponse | null;
  pluginError: string;
  pluginInstallingTarget: "skills" | "mcp" | "";
  comfyUiState: ComfyUiStateResponse | null;
  comfyUiError: string;
  comfyUiBusy: boolean;
  refreshPluginState: () => void;
  deleteSkill: (skillId: string) => void;
  installSkill: (path: string) => void;
  installMcp: (path: string) => void;
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
  refreshComfyUiState: () => void;
  saveComfyUiUrl: (baseUrl: string) => Promise<boolean>;
  checkComfyUi: (baseUrl?: string) => void;
  openComfyUiInBrowser: (baseUrl?: string) => void;
};

export function PluginsPanel({
  t,
  pluginState,
  pluginError,
  pluginInstallingTarget,
  comfyUiState,
  comfyUiError,
  comfyUiBusy,
  refreshPluginState,
  deleteSkill,
  installSkill,
  installMcp,
  registerExternalMcp,
  refreshComfyUiState,
  saveComfyUiUrl,
  checkComfyUi,
  openComfyUiInBrowser,
}: PluginsPanelProps) {
  const runtimeCapabilities = runtimeCapabilitiesForPlugins(t, pluginState?.runtime_capabilities || []);
  const [comfyUiUrl, setComfyUiUrl] = useState(comfyUiState?.base_url || "http://127.0.0.1:8188");

  useEffect(() => {
    if (comfyUiState?.base_url) {
      setComfyUiUrl(comfyUiState.base_url);
    }
  }, [comfyUiState?.base_url]);

  return (
    <section className="plugins-pane" aria-label={t("plugins.aria")}>
      <header className="workspace-header">
        <div>
          <h1 className="workspace-title">{t("plugins.title")}</h1>
          <p className="workspace-subtitle">{t("plugins.subtitle")}</p>
        </div>
        <button className="secondary-button" type="button" onClick={refreshPluginState}>
          {t("common.refresh")}
        </button>
      </header>

      {pluginError ? <div className="runtime-error plugin-error">{pluginError}</div> : null}

      {!pluginState ? (
        <div className="workspace-empty">{t("plugins.loading")}</div>
      ) : (
        <div className="plugins-content">
          {runtimeCapabilities.length ? (
            <section className="plugin-column runtime-capability-column" aria-label={t("plugins.runtimeCapabilities")}>
              <div className="plugin-section-header">
                <div className="runtime-capability-heading">
                  <span>{t("plugins.runtimeCapabilities")}</span>
                  <small>{t("plugins.runtimeCapabilitiesDescription")}</small>
                </div>
                <strong>{runtimeCapabilities.length}</strong>
              </div>
              <div className="runtime-capability-list">
                {runtimeCapabilities.map((capability) => (
                  <RuntimeCapabilityCard key={capability.id} t={t} capability={capability} />
                ))}
              </div>
            </section>
          ) : null}

          <ComfyUiConnectionCard
            t={t}
            url={comfyUiUrl}
            state={comfyUiState}
            error={comfyUiError}
            busy={comfyUiBusy}
            onUrlChange={setComfyUiUrl}
            refresh={refreshComfyUiState}
            save={saveComfyUiUrl}
            check={checkComfyUi}
            openBrowser={openComfyUiInBrowser}
          />

          <section className="plugin-column" aria-label={t("plugins.skillList")}>
            <div className="plugin-section-header">
              <span>{t("plugins.skillList")}</span>
              <strong>{pluginState.skills.length}</strong>
            </div>
            <PluginDropZone
              t={t}
              title={t("plugins.dropSkill")}
              description={t("plugins.dropSkillDescription")}
              installing={pluginInstallingTarget === "skills"}
              onInstall={installSkill}
            />
            <div className="plugin-list">
              {pluginState.skills.map((skill) => (
                <SkillRow key={skill.id} t={t} skill={skill} deleteSkill={deleteSkill} />
              ))}
            </div>
          </section>

          <section className="plugin-column mcp-plugin-column" aria-label={t("plugins.mcpList")}>
            <div className="plugin-section-header">
              <span>{t("plugins.mcpList")}</span>
              <strong>{pluginState.mcp.length}</strong>
            </div>
            <PluginDropZone
              t={t}
              title={t("plugins.dropMcp")}
              description={t("plugins.dropMcpDescription")}
              installing={pluginInstallingTarget === "mcp"}
              onInstall={installMcp}
            />
            <ExternalMcpForm t={t} disabled={pluginInstallingTarget === "mcp"} registerExternalMcp={registerExternalMcp} />
            <div className="plugin-list">
              {pluginState.mcp.map((row) => (
                <McpRowView key={row.id} t={t} row={row} />
              ))}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

function ComfyUiConnectionCard({
  t,
  url,
  state,
  error,
  busy,
  onUrlChange,
  refresh,
  save,
  check,
  openBrowser,
}: {
  t: Translator;
  url: string;
  state: ComfyUiStateResponse | null;
  error: string;
  busy: boolean;
  onUrlChange: (value: string) => void;
  refresh: () => void;
  save: (baseUrl: string) => Promise<boolean>;
  check: (baseUrl?: string) => void;
  openBrowser: (baseUrl?: string) => void;
}) {
  async function submit(event: FormEvent) {
    event.preventDefault();
    await save(url);
  }

  const cleanUrl = url.trim();
  const status = state?.status || "unknown";
  const lastError = error || state?.last_error || "";

  return (
    <section className="plugin-column comfyui-column" aria-label={t("plugins.comfyUiTitle")}>
      <div className="plugin-section-header">
        <div className="runtime-capability-heading">
          <span>{t("plugins.comfyUiTitle")}</span>
          <small>{t("plugins.comfyUiDescription")}</small>
        </div>
        <span className={`status-chip ${status === "online" ? "good" : status === "offline" ? "danger" : "muted"}`}>
          {comfyUiStatusLabel(t, status)}
        </span>
      </div>
      <form className="comfyui-connection-form" onSubmit={submit}>
        <label className="comfyui-url-field">
          <span>{t("plugins.comfyUiUrl")}</span>
          <input
            value={url}
            onChange={(event) => onUrlChange(event.target.value)}
            placeholder="http://127.0.0.1:8188"
            disabled={busy}
          />
        </label>
        <div className="comfyui-actions">
          <button className="secondary-button" type="submit" disabled={busy || !cleanUrl}>
            {busy ? t("common.saving") : t("common.save")}
          </button>
          <button className="secondary-button" type="button" disabled={busy || !cleanUrl} onClick={() => check(cleanUrl)}>
            {t("plugins.comfyUiCheck")}
          </button>
          <button className="secondary-button" type="button" disabled={busy || !cleanUrl} onClick={() => openBrowser(cleanUrl)}>
            {t("plugins.comfyUiOpen")}
          </button>
          <button className="secondary-button subtle" type="button" disabled={busy} onClick={refresh}>
            {t("common.refresh")}
          </button>
        </div>
      </form>
      <div className="comfyui-meta">
        <span>{state?.configured ? t("plugins.comfyUiConfigured") : t("plugins.comfyUiNotConfigured")}</span>
        {state?.checked_at ? <span>{t("plugins.comfyUiChecked")}: {state.checked_at}</span> : null}
        {state?.endpoints?.system_stats ? <span>/system_stats</span> : null}
        {state?.endpoints?.queue ? <span>/queue</span> : null}
      </div>
      {lastError ? <div className="comfyui-error">{lastError}</div> : null}
    </section>
  );
}

function RuntimeCapabilityCard({
  t,
  capability,
}: {
  t: Translator;
  capability: RuntimeCapabilityCardModel;
}) {
  return (
    <article className="runtime-capability-card">
      <div className="runtime-capability-card-header">
        <div className="runtime-capability-card-title">{capability.id}</div>
        <div className="runtime-capability-card-description">{capability.description}</div>
      </div>
      <div className="runtime-capability-grid">
        <div className="runtime-capability-row">
          <span className="runtime-capability-label">{t("plugins.runtimeCapabilityStatus")}</span>
          <span className="runtime-capability-value">{capability.status}</span>
        </div>
        <div className="runtime-capability-row">
          <span className="runtime-capability-label">{t("plugins.runtimeCapabilityAbilities")}</span>
          <div className="runtime-capability-chip-row">
            {capability.abilities.map((ability) => (
              <span className="runtime-capability-chip" key={ability}>
                {ability}
              </span>
            ))}
          </div>
        </div>
        <div className="runtime-capability-row">
          <span className="runtime-capability-label">{t("plugins.runtimeCapabilityRisk")}</span>
          <span className="runtime-capability-value runtime-capability-risk">{capability.risk}</span>
        </div>
      </div>
    </article>
  );
}

function ExternalMcpForm({
  t,
  disabled,
  registerExternalMcp,
}: {
  t: Translator;
  disabled: boolean;
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
}) {
  const [transport, setTransport] = useState<ExternalMcpPayload["transport"]>("stdio");
  const [id, setId] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [url, setUrl] = useState("");
  const [localError, setLocalError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const cleanId = id.trim();
    if (!cleanId) {
      setLocalError(t("plugins.idRequired"));
      return;
    }
    const payload: ExternalMcpPayload =
      transport === "stdio"
        ? {
            id: cleanId,
            transport,
            command: command.trim(),
            args: splitArgs(argsText),
          }
        : {
            id: cleanId,
            transport,
            url: url.trim(),
          };
    if (transport === "stdio" && !payload.command) {
      setLocalError(t("plugins.commandRequired"));
      return;
    }
    if (transport !== "stdio" && !payload.url) {
      setLocalError(t("plugins.urlRequired"));
      return;
    }
    setLocalError("");
    const ok = await registerExternalMcp(payload);
    if (ok) {
      setId("");
      setCommand("");
      setArgsText("");
      setUrl("");
    }
  }

  return (
    <form className="external-mcp-form" onSubmit={submit}>
      <div className="external-mcp-form-header">
        <strong>{t("plugins.externalMcp")}</strong>
        <select
          value={transport}
          onChange={(event) => setTransport(event.target.value as ExternalMcpPayload["transport"])}
          disabled={disabled}
        >
          <option value="stdio">stdio</option>
          <option value="http">http</option>
          <option value="sse">sse</option>
        </select>
      </div>
      <input value={id} onChange={(event) => setId(event.target.value)} placeholder={t("plugins.mcpIdPlaceholder")} disabled={disabled} />
      {transport === "stdio" ? (
        <div className="external-mcp-grid">
          <input
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            placeholder={t("plugins.commandPlaceholder")}
            disabled={disabled}
          />
          <input
            value={argsText}
            onChange={(event) => setArgsText(event.target.value)}
            placeholder={t("plugins.argsPlaceholder")}
            disabled={disabled}
          />
        </div>
      ) : (
        <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={t("plugins.urlPlaceholder")} disabled={disabled} />
      )}
      <div className="external-mcp-form-footer">
        <span className={localError ? "external-mcp-error" : ""}>{localError || t("plugins.saveOnly")}</span>
        <button className="secondary-button" type="submit" disabled={disabled}>
          {t("common.add")}
        </button>
      </div>
    </form>
  );
}

function PluginDropZone({
  t,
  title,
  description,
  installing,
  onInstall,
}: {
  t: Translator;
  title: string;
  description: string;
  installing: boolean;
  onInstall: (path: string) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState("");

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDragging(true);
    setLocalError("");
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setDragging(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const firstPath = droppedFilePaths(event.dataTransfer.files)[0] || "";
    if (!firstPath) {
      setLocalError(t("plugins.missingDropPath"));
      return;
    }
    onInstall(firstPath);
  }

  return (
    <div
      className={dragging ? "plugin-drop-zone dragging" : "plugin-drop-zone"}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      role="group"
      aria-label={title}
    >
      <div>
        <strong>{installing ? t("plugins.installing") : title}</strong>
        <span>{description}</span>
      </div>
      <small>{installing ? t("common.installing") : t("common.drop")}</small>
      {localError ? <em>{localError}</em> : null}
    </div>
  );
}

function SkillRow({
  t,
  skill,
  deleteSkill,
}: {
  t: Translator;
  skill: PluginSkill;
  deleteSkill: (skillId: string) => void;
}) {
  return (
    <article className="plugin-row">
      <div className="plugin-row-main">
        <div className="plugin-row-title">{skill.title}</div>
        <div className="plugin-row-description">{skill.description || skill.id}</div>
        <div className="plugin-row-footer">
          <div className="plugin-chip-row">
            {skill.chips.map((chip) => (
              <span className={chip === t("plugins.coreChip") ? "plugin-chip core" : "plugin-chip"} key={chip}>
                {chip}
              </span>
            ))}
          </div>
          <button
            className="plugin-delete-pill"
            type="button"
            disabled={!skill.deletable}
            title={skill.deletable ? t("plugins.deleteSkill") : t("plugins.coreSkillLocked")}
            onClick={() => deleteSkill(skill.id)}
          >
            {t("common.delete")}
          </button>
        </div>
      </div>
    </article>
  );
}

function McpRowView({ t, row }: { t: Translator; row: PluginMcpRow }) {
  return (
    <article className="plugin-row mcp-row">
      <div className="plugin-row-main">
        <div className="plugin-row-title">{row.title}</div>
        <div className="plugin-row-description">{row.detail || row.id}</div>
        <div className="plugin-row-footer">
          <span className={row.status === t("plugins.connected") ? "status-chip good" : "status-chip muted"}>{row.status}</span>
        </div>
      </div>
    </article>
  );
}

function droppedFilePaths(files: FileList): string[] {
  const desktopPaths = window.lucodeDesktop?.droppedFilePaths?.(files) || [];
  if (desktopPaths.length > 0) {
    return desktopPaths;
  }
  return Array.from(files)
    .map((file) => (file as File & { path?: string }).path || "")
    .filter(Boolean);
}

function splitArgs(value: string): string[] {
  return value
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function runtimeCapabilitiesForPlugins(
  t: Translator,
  capabilities: PluginRuntimeCapability[],
): RuntimeCapabilityCardModel[] {
  return capabilities.map((capability) => ({
    id: capability.id,
    description: runtimeCapabilityDescription(t, capability),
    status: runtimeCapabilityStatus(t, capability.status_key),
    abilities: capability.ability_keys.map((key) => runtimeCapabilityAbility(t, key)).filter(Boolean),
    risk: runtimeCapabilityRisk(t, capability.risk_key),
  }));
}

function runtimeCapabilityDescription(t: Translator, capability: PluginRuntimeCapability): string {
  if (capability.id === "desktop_browser") {
    return t("plugins.runtimeDesktopBrowserDescription");
  }
  return capability.summary_zh || capability.summary || capability.display_name || capability.id;
}

function runtimeCapabilityStatus(t: Translator, statusKey: string): string {
  if (statusKey === "desktop_runtime") {
    return t("plugins.runtimeDesktop");
  }
  return statusKey;
}

function runtimeCapabilityAbility(t: Translator, abilityKey: string): string {
  switch (abilityKey) {
    case "navigate":
      return t("plugins.runtimeNavigate");
    case "page_summary":
      return t("plugins.runtimePageSummary");
    case "controlled_click":
      return t("plugins.runtimeControlledClick");
    case "form_input":
      return t("plugins.runtimeFormInput");
    case "form_submit":
      return t("plugins.runtimeFormSubmit");
    default:
      return abilityKey;
  }
}

function runtimeCapabilityRisk(t: Translator, riskKey: string): string {
  if (riskKey === "approval_required") {
    return t("plugins.runtimeApprovalRequired");
  }
  return riskKey;
}

function comfyUiStatusLabel(t: Translator, status: ComfyUiStateResponse["status"]): string {
  switch (status) {
    case "online":
      return t("plugins.comfyUiOnline");
    case "offline":
      return t("plugins.comfyUiOffline");
    default:
      return t("plugins.comfyUiUnknown");
  }
}
