import { type DragEvent, type FormEvent, useEffect, useState } from "react";

import type { Translator } from "../i18n";
import type {
  ComfyUiStateResponse,
  ExternalMcpPayload,
  PluginMcpRow,
  PluginPackage,
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
  pluginInstallingTarget: "skills" | "mcp" | "packages" | "";
  comfyUiState: ComfyUiStateResponse | null;
  comfyUiError: string;
  comfyUiBusy: boolean;
  refreshPluginState: () => void;
  deleteSkill: (skillId: string) => void;
  installSkill: (path: string) => void;
  installMcp: (path: string) => void;
  installPluginPackage: (path: string) => void;
  deletePluginPackage: (pluginId: string) => void;
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
  refreshComfyUiState: () => void;
  saveComfyUiUrl: (baseUrl: string, installPath?: string, launchScript?: string) => Promise<boolean>;
  detectComfyUiInstall: (installPath: string, launchScript?: string) => Promise<boolean>;
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
  installPluginPackage,
  deletePluginPackage,
  registerExternalMcp,
  refreshComfyUiState,
  saveComfyUiUrl,
  detectComfyUiInstall,
  checkComfyUi,
  openComfyUiInBrowser,
}: PluginsPanelProps) {
  const runtimeCapabilities = runtimeCapabilitiesForPlugins(t, pluginState?.runtime_capabilities || []);
  const installedPlugins = pluginState?.installed_plugins || [];
  const [comfyUiUrl, setComfyUiUrl] = useState(comfyUiState?.base_url || "http://127.0.0.1:8188");
  const [managedPluginId, setManagedPluginId] = useState("");

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

          <section className="plugin-column plugin-package-column" aria-label={t("plugins.packageList")}>
            <div className="plugin-section-header">
              <span>{t("plugins.packageList")}</span>
              <strong>{installedPlugins.length}</strong>
            </div>
            <PluginDropZone
              t={t}
              title={t("plugins.dropPackage")}
              description={t("plugins.dropPackageDescription")}
              installing={pluginInstallingTarget === "packages"}
              onInstall={installPluginPackage}
            />
            <div className="plugin-list">
              {installedPlugins.length ? (
                installedPlugins.map((plugin) => (
                  <InstalledPluginRow
                    key={plugin.id}
                    t={t}
                    plugin={plugin}
                    busy={pluginInstallingTarget === "packages"}
                    deletePluginPackage={deletePluginPackage}
                  />
                ))
              ) : (
                <div className="plugin-empty-row">{t("plugins.noInstalledPackages")}</div>
              )}
            </div>
          </section>

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
              <strong>{pluginState.mcp.length + 1}</strong>
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
              <ComfyUiMcpRow
                t={t}
                url={comfyUiUrl}
                state={comfyUiState}
                error={comfyUiError}
                busy={comfyUiBusy}
                managed={managedPluginId === "comfyui"}
                onManage={() => setManagedPluginId((current) => (current === "comfyui" ? "" : "comfyui"))}
                onUrlChange={setComfyUiUrl}
                refresh={refreshComfyUiState}
                save={saveComfyUiUrl}
                detectInstall={detectComfyUiInstall}
                check={checkComfyUi}
                openBrowser={openComfyUiInBrowser}
              />
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

export function ComfyUiMcpRow({
  t,
  url,
  state,
  error,
  busy,
  managed,
  onManage,
  onUrlChange,
  refresh,
  save,
  detectInstall,
  check,
  openBrowser,
}: {
  t: Translator;
  url: string;
  state: ComfyUiStateResponse | null;
  error: string;
  busy: boolean;
  managed: boolean;
  onManage: () => void;
  onUrlChange: (value: string) => void;
  refresh: () => void;
  save: (baseUrl: string, installPath?: string, launchScript?: string) => Promise<boolean>;
  detectInstall: (installPath: string, launchScript?: string) => Promise<boolean>;
  check: (baseUrl?: string) => void;
  openBrowser: (baseUrl?: string) => void;
}) {
  const status = state?.status || "unknown";
  const installStatus = state?.installation?.status || "unconfigured";
  const lastError = error || state?.last_error || "";

  return (
    <article className="plugin-row mcp-row comfyui-mcp-row">
      <div className="plugin-row-main">
        <div className="comfyui-mcp-title-row">
          <div className="plugin-row-title">{t("plugins.comfyUiMcpTitle")}</div>
          <span className="plugin-chip">{t("plugins.mcpTemplateChip")}</span>
        </div>
        <div className="plugin-row-description">{t("plugins.comfyUiMcpDescription")}</div>
        <div className="plugin-row-footer">
          <div className="plugin-chip-row">
            <span className={`status-chip ${status === "online" ? "good" : status === "offline" ? "danger" : "muted"}`}>
              {comfyUiStatusLabel(t, status)}
            </span>
            <span className={installStatus === "launchable" ? "plugin-chip good" : "plugin-chip"}>
              {comfyUiInstallStatusLabel(t, installStatus)}
            </span>
            {lastError ? <span className="plugin-chip danger">{lastError}</span> : null}
          </div>
          <button className="secondary-button compact" type="button" aria-expanded={managed} onClick={onManage}>
            {managed ? t("common.close") : t("plugins.managePlugin")}
          </button>
        </div>
        {managed ? (
          <div className="comfyui-mcp-detail">
            <ComfyUiMcpCompactForm
              t={t}
              url={url}
              state={state}
              error={error}
              busy={busy}
              onUrlChange={onUrlChange}
              refresh={refresh}
              save={save}
              detectInstall={detectInstall}
              check={check}
              openBrowser={openBrowser}
            />
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ComfyUiMcpCompactForm({
  t,
  url,
  state,
  error,
  busy,
  onUrlChange,
  refresh,
  save,
  detectInstall,
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
  save: (baseUrl: string, installPath?: string, launchScript?: string) => Promise<boolean>;
  detectInstall: (installPath: string, launchScript?: string) => Promise<boolean>;
  check: (baseUrl?: string) => void;
  openBrowser: (baseUrl?: string) => void;
}) {
  const installation = state?.installation;
  const [installPath, setInstallPath] = useState(installation?.install_path || "");
  const [launchScript, setLaunchScript] = useState(installation?.launch_script || "");

  useEffect(() => {
    setInstallPath(installation?.install_path || "");
    setLaunchScript(installation?.launch_script || "");
  }, [installation?.install_path, installation?.launch_script]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await save(url, installPath, launchScript);
  }

  const cleanUrl = url.trim();
  const cleanInstallPath = installPath.trim();
  const lastError = error || state?.last_error || "";
  const launchOptions = installation?.available_launch_scripts || [];
  const installStatus = installation?.status || "unconfigured";

  return (
    <div className="comfyui-mcp-compact-form" aria-label={t("plugins.comfyUiMcpTitle")}>
      <form className="comfyui-mcp-form" onSubmit={submit}>
        <div className="comfyui-mcp-fields">
          <label className="comfyui-mcp-field">
            <span>{t("plugins.comfyUiUrl")}</span>
            <input
              value={url}
              onChange={(event) => onUrlChange(event.target.value)}
              placeholder="http://127.0.0.1:8188"
              disabled={busy}
            />
          </label>
          <label className="comfyui-mcp-field">
            <span>{t("plugins.comfyUiInstallPath")}</span>
            <input
              value={installPath}
              onChange={(event) => setInstallPath(event.target.value)}
              placeholder="D:\\develop\\ComfyUI_windows_portable_nvidia"
              disabled={busy}
            />
          </label>
          <label className="comfyui-mcp-field">
            <span>{t("plugins.comfyUiLaunchScript")}</span>
            {launchOptions.length ? (
              <select value={launchScript} onChange={(event) => setLaunchScript(event.target.value)} disabled={busy}>
                {launchOptions.map((script) => (
                  <option value={script} key={script}>
                    {script}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={launchScript}
                onChange={(event) => setLaunchScript(event.target.value)}
                placeholder="run_nvidia_gpu.bat"
                disabled={busy}
              />
            )}
          </label>
        </div>
        <div className="comfyui-mcp-actions">
          <button className="secondary-button compact" type="submit" disabled={busy || !cleanUrl}>
            {busy ? t("common.saving") : t("common.save")}
          </button>
          <button
            className="secondary-button compact"
            type="button"
            disabled={busy || !cleanInstallPath}
            onClick={() => void detectInstall(cleanInstallPath, launchScript)}
          >
            {t("plugins.comfyUiDetect")}
          </button>
          <button className="secondary-button compact" type="button" disabled={busy || !cleanUrl} onClick={() => check(cleanUrl)}>
            {t("plugins.comfyUiCheck")}
          </button>
          <button className="secondary-button compact" type="button" disabled={busy || !cleanUrl} onClick={() => openBrowser(cleanUrl)}>
            {t("plugins.comfyUiOpen")}
          </button>
          <button className="secondary-button compact subtle" type="button" disabled={busy} onClick={refresh}>
            {t("common.refresh")}
          </button>
        </div>
      </form>
      <div className="comfyui-mcp-meta">
        <span>{state?.configured ? t("plugins.comfyUiConfigured") : t("plugins.comfyUiNotConfigured")}</span>
        <span>{comfyUiInstallStatusLabel(t, installStatus)}</span>
        {installation?.resolved_root ? <span title={installation.resolved_root}>{installation.resolved_root}</span> : null}
        {installation?.launch_script ? <span>{installation.launch_script}</span> : null}
        {state?.checked_at ? <span>{t("plugins.comfyUiChecked")}: {state.checked_at}</span> : null}
        {state?.endpoints?.system_stats ? <span>/system_stats</span> : null}
        {state?.endpoints?.queue ? <span>/queue</span> : null}
      </div>
      {installation?.validation_errors?.length ? (
        <div className="comfyui-error">{installation.validation_errors.join("; ")}</div>
      ) : null}
      {lastError ? <div className="comfyui-error">{lastError}</div> : null}
    </div>
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

function InstalledPluginRow({
  t,
  plugin,
  busy,
  deletePluginPackage,
}: {
  t: Translator;
  plugin: PluginPackage;
  busy: boolean;
  deletePluginPackage: (pluginId: string) => void;
}) {
  return (
    <article className="plugin-row installed-plugin-row">
      <div className="plugin-row-main">
        <div className="plugin-row-title">{plugin.title || plugin.id}</div>
        <div className="plugin-row-description">{plugin.description || plugin.id}</div>
        <div className="plugin-row-footer">
          <div className="plugin-chip-row">
            <span className="plugin-chip">{t("plugins.packageSkillCount", { count: plugin.skill_ids.length })}</span>
            <span className="plugin-chip">{t("plugins.packageMcpCount", { count: plugin.mcp_ids.length })}</span>
            {plugin.launch_profiles.length ? (
              <span className="plugin-chip">{t("plugins.packageLaunchCount", { count: plugin.launch_profiles.length })}</span>
            ) : null}
          </div>
          <button
            className="plugin-delete-pill"
            type="button"
            disabled={busy || !plugin.deletable}
            title={t("plugins.uninstallPlugin")}
            onClick={() => deletePluginPackage(plugin.id)}
          >
            {t("plugins.uninstallPlugin")}
          </button>
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

function comfyUiInstallStatusLabel(t: Translator, status: string): string {
  switch (status) {
    case "launchable":
      return t("plugins.comfyUiLaunchable");
    case "invalid_path":
      return t("plugins.comfyUiInvalidPath");
    case "invalid_launch_script":
      return t("plugins.comfyUiInvalidLaunchScript");
    default:
      return t("plugins.comfyUiInstallUnconfigured");
  }
}
