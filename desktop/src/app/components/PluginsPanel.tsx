import { type DragEvent, type FormEvent, useState } from "react";

import type { Translator } from "../i18n";
import type { ExternalMcpPayload, PluginMcpRow, PluginSkill, PluginStateResponse } from "../../shared/types";

export type PluginsPanelProps = {
  t: Translator;
  pluginState: PluginStateResponse | null;
  pluginError: string;
  pluginInstallingTarget: "skills" | "mcp" | "";
  refreshPluginState: () => void;
  deleteSkill: (skillId: string) => void;
  installSkill: (path: string) => void;
  installMcp: (path: string) => void;
  registerExternalMcp: (payload: ExternalMcpPayload) => Promise<boolean>;
};

export function PluginsPanel({
  t,
  pluginState,
  pluginError,
  pluginInstallingTarget,
  refreshPluginState,
  deleteSkill,
  installSkill,
  installMcp,
  registerExternalMcp,
}: PluginsPanelProps) {
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

          <section className="plugin-column" aria-label={t("plugins.mcpList")}>
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
        <div className="plugin-chip-row">
          {skill.chips.map((chip) => (
            <span className={chip === t("plugins.coreChip") ? "plugin-chip core" : "plugin-chip"} key={chip}>
              {chip}
            </span>
          ))}
        </div>
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
    </article>
  );
}

function McpRowView({ t, row }: { t: Translator; row: PluginMcpRow }) {
  return (
    <article className="plugin-row mcp-row">
      <div className="plugin-row-main">
        <div className="plugin-row-title">{row.title}</div>
        <div className="plugin-row-description">{row.detail || row.id}</div>
      </div>
      <span className={row.status === t("plugins.connected") ? "status-chip good" : "status-chip muted"}>{row.status}</span>
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
