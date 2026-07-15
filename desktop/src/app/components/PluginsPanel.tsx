import { type DragEvent, type FormEvent, useEffect, useState } from "react";

import type { Translator } from "../i18n";
import { resolveDroppedFilePaths } from "../localFileDrop";
import type {
  ComfyUiStateResponse,
  ExternalMcpPayload,
  PluginMcpRow,
  PluginPackage,
  PluginRuntimeCapability,
  PluginSkillMetadataProposal,
  PluginSkillLibraryCategory,
  PluginSkillLibraryEntry,
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
  applySkillMetadata: (skillId: string, payload: SkillMetadataPayload) => Promise<boolean>;
  setSkillEnabled: (skillId: string, enabled: boolean) => Promise<boolean>;
  reindexSkillLibrary: () => Promise<boolean>;
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
  applySkillMetadata,
  setSkillEnabled,
  reindexSkillLibrary,
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
  const skillLibrary = pluginState?.skill_library || [];
  const skillLibraryCategories = pluginState?.skill_library_categories || [];
  const deletableSkillIds = new Set((pluginState?.skills || []).filter((skill) => skill.deletable).map((skill) => skill.id));
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
                  <RuntimeCapabilityCard key={capability.id} capability={capability} />
                ))}
              </div>
            </section>
          ) : null}

          <SkillLibrarySection
            t={t}
            entries={skillLibrary}
            categories={skillLibraryCategories}
            installing={pluginInstallingTarget === "skills"}
            onInstall={installSkill}
            deletableSkillIds={deletableSkillIds}
            deleteSkill={deleteSkill}
            applySkillMetadata={applySkillMetadata}
            setSkillEnabled={setSkillEnabled}
            reindexSkillLibrary={reindexSkillLibrary}
          />

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
              sourceKind="package"
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
              sourceKind="mcp"
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

function SkillLibrarySection({
  t,
  entries,
  categories,
  installing,
  onInstall,
  deletableSkillIds,
  deleteSkill,
  applySkillMetadata,
  setSkillEnabled,
  reindexSkillLibrary,
}: {
  t: Translator;
  entries: PluginSkillLibraryEntry[];
  categories: PluginSkillLibraryCategory[];
  installing: boolean;
  onInstall: (path: string) => void;
  deletableSkillIds: ReadonlySet<string>;
  deleteSkill: (skillId: string) => void;
  applySkillMetadata: (skillId: string, payload: SkillMetadataPayload) => Promise<boolean>;
  setSkillEnabled: (skillId: string, enabled: boolean) => Promise<boolean>;
  reindexSkillLibrary: () => Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [reindexing, setReindexing] = useState(false);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const availableCategories = categories.filter((category) =>
    entries.some((entry) => entry.category.some((id) => id === category.id || id.startsWith(`${category.id}/`))),
  );
  const filteredEntries = entries.filter((entry) => {
    const categoryMatches = !categoryId || entry.category.some((id) => id === categoryId || id.startsWith(`${categoryId}/`));
    if (!categoryMatches) return false;
    if (!normalizedQuery) return true;
    const searchable = [entry.id, entry.name, entry.summary, ...entry.tags, ...entry.category].join(" ").toLocaleLowerCase();
    return searchable.includes(normalizedQuery);
  });
  const selectedEntry =
    filteredEntries.find((entry) => entry.id === selectedSkillId) || filteredEntries[0] || entries.find((entry) => entry.id === selectedSkillId) || entries[0];

  return (
    <section className="skill-library" aria-label={t("plugins.skillLibrary")}>
      <div className="skill-library-toolbar">
        <div className="skill-library-title">
          <div className="runtime-capability-heading">
            <span>{t("plugins.skillLibrary")}</span>
            <small>{t("plugins.skillLibraryDescription")}</small>
          </div>
          <strong>{entries.length}</strong>
        </div>
        <input
          className="skill-library-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("plugins.skillLibrarySearch")}
          aria-label={t("plugins.skillLibrarySearch")}
        />
        <div className="skill-library-actions">
          <div className="skill-library-import">
            <PluginDropZone
              t={t}
              title={t("plugins.skillLibraryImport")}
              description={t("plugins.dropSkillDescription")}
              installing={installing}
              onInstall={onInstall}
              sourceKind="skill"
              compact
              toolbar
            />
          </div>
          <button
            className="secondary-button compact"
            type="button"
            disabled={reindexing}
            onClick={() => {
              setReindexing(true);
              void reindexSkillLibrary().finally(() => setReindexing(false));
            }}
          >
            {reindexing ? t("plugins.skillLibraryReindexing") : t("plugins.skillLibraryReindex")}
          </button>
        </div>
      </div>
      <nav className="skill-library-categories skill-library-filters" aria-label={t("plugins.skillLibrary")}>
          <button className={!categoryId ? "skill-library-category active" : "skill-library-category"} type="button" onClick={() => setCategoryId("")}>
            {t("common.all")}
          </button>
          {availableCategories.map((category) => (
            <button
              className={categoryId === category.id ? "skill-library-category active" : "skill-library-category"}
              type="button"
            key={category.id}
            title={category.description}
            onClick={() => setCategoryId(category.id)}
          >
              {skillCategoryLabel(t, category)}
            </button>
          ))}
      </nav>
      <div className="skill-library-layout">
        <div className="skill-library-list-pane">
          <div className="skill-library-list">
            {filteredEntries.length ? (
              filteredEntries.map((entry) => (
                <button
                  className={entry.id === selectedEntry?.id ? "skill-library-entry active" : "skill-library-entry"}
                  type="button"
                  key={entry.id}
                  onClick={() => setSelectedSkillId(entry.id)}
                >
                  <strong>{entry.name}</strong>
                  <span>{entry.summary || entry.id}</span>
                  <small>{entry.category.join(" / ") || entry.source}</small>
                </button>
              ))
            ) : (
              <div className="plugin-empty-row">{t("plugins.skillLibraryEmpty")}</div>
            )}
          </div>
        </div>
        {selectedEntry ? (
          <SkillLibraryDetail
            t={t}
            entry={selectedEntry}
            deletable={deletableSkillIds.has(selectedEntry.id)}
            deleteSkill={deleteSkill}
            applySkillMetadata={applySkillMetadata}
            setSkillEnabled={setSkillEnabled}
          />
        ) : null}
      </div>
    </section>
  );
}

function skillCategoryLabel(t: Translator, category: PluginSkillLibraryCategory): string {
  const keyById: Record<string, Parameters<Translator>[0]> = {
    programming: "plugins.skillCategory.programming",
    "programming/frontend": "plugins.skillCategory.programming.frontend",
    "programming/backend": "plugins.skillCategory.programming.backend",
    "programming/agent_loop": "plugins.skillCategory.programming.agent_loop",
    "programming/testing": "plugins.skillCategory.programming.testing",
    design: "plugins.skillCategory.design",
    "design/ui_design": "plugins.skillCategory.design.ui_design",
    "design/image_generation": "plugins.skillCategory.design.image_generation",
    "design/comfyui": "plugins.skillCategory.design.comfyui",
    documentation: "plugins.skillCategory.documentation",
    "documentation/readme": "plugins.skillCategory.documentation.readme",
    "documentation/planning": "plugins.skillCategory.documentation.planning",
    tools: "plugins.skillCategory.tools",
    "tools/git": "plugins.skillCategory.tools.git",
    "tools/terminal": "plugins.skillCategory.tools.terminal",
    "tools/browser": "plugins.skillCategory.tools.browser",
    mcp: "plugins.skillCategory.mcp",
    "mcp/integration": "plugins.skillCategory.mcp.integration",
  };
  const key = keyById[category.id];
  return key ? t(key) : category.name;
}

function SkillLibraryDetail({
  t,
  entry,
  deletable,
  deleteSkill,
  applySkillMetadata,
  setSkillEnabled,
}: {
  t: Translator;
  entry: PluginSkillLibraryEntry;
  deletable: boolean;
  deleteSkill: (skillId: string) => void;
  applySkillMetadata: (skillId: string, payload: SkillMetadataPayload) => Promise<boolean>;
  setSkillEnabled: (skillId: string, enabled: boolean) => Promise<boolean>;
}) {
  const suggestion = entry.suggestion;
  const suggestedCategories = suggestion.categories || [];
  const hasSuggestion =
    (entry.metadata_status !== "ready" && entry.editable_metadata) ||
    suggestedCategories.length > 0 ||
    (suggestion.tags || []).length > 0 ||
    (suggestion.use_when || []).length > 0 ||
    (suggestion.do_not_use_when || []).length > 0 ||
    (suggestion.source_count > 0 && (suggestion.negative_queries.length > 0 || Object.keys(suggestion.distinguish_from).length > 0));
  const usedCount = Number(entry.usage.used_count || 0);
  const rejectedCount = Number(entry.usage.rejected_by_planner_count || 0);
  const [editingAdvice, setEditingAdvice] = useState(false);
  const [savingAdvice, setSavingAdvice] = useState(false);
  const [updatingEnabled, setUpdatingEnabled] = useState(false);
  const [categories, setCategories] = useState("");
  const [tags, setTags] = useState("");
  const [useWhen, setUseWhen] = useState("");
  const [doNotUseWhen, setDoNotUseWhen] = useState("");
  const [negativeQueries, setNegativeQueries] = useState("");
  const [distinguishFrom, setDistinguishFrom] = useState("");
  const completionReady = Boolean(categories.trim() && tags.trim() && useWhen.trim() && doNotUseWhen.trim());

  useEffect(() => {
    setEditingAdvice(false);
    setSavingAdvice(false);
    setUpdatingEnabled(false);
    setCategories(mergeLines(entry.category, suggestedCategories));
    setTags(mergeLines(entry.tags, suggestion.tags || []));
    setUseWhen(mergeLines(entry.use_when, suggestion.use_when || []));
    setDoNotUseWhen(mergeLines(entry.do_not_use_when, suggestion.do_not_use_when || []));
    setNegativeQueries(mergeLines(entry.negative_queries, suggestion.negative_queries));
    setDistinguishFrom(mergeMappings(entry.distinguish_from, suggestion.distinguish_from));
  }, [entry.id, suggestion]);

  async function applyAdvice() {
    const parsedDistinguishFrom = parseDistinguishFrom(distinguishFrom);
    setSavingAdvice(true);
    const applied = await applySkillMetadata(entry.id, {
      categories: categories.split("\n").map((value) => value.trim()).filter(Boolean),
      tags: tags.split("\n").map((value) => value.trim()).filter(Boolean),
      use_when: useWhen.split("\n").map((value) => value.trim()).filter(Boolean),
      do_not_use_when: doNotUseWhen.split("\n").map((value) => value.trim()).filter(Boolean),
      negative_queries: negativeQueries.split("\n").map((value) => value.trim()).filter(Boolean),
      distinguish_from: parsedDistinguishFrom,
    });
    setSavingAdvice(false);
    if (applied) setEditingAdvice(false);
  }

  async function updateEnabled(enabled: boolean) {
    setUpdatingEnabled(true);
    await setSkillEnabled(entry.id, enabled);
    setUpdatingEnabled(false);
  }

  return (
    <article className="skill-library-detail">
      <div className="skill-library-detail-heading">
        <div>
          <h2>{entry.name}</h2>
          <p>{entry.summary || entry.id}</p>
        </div>
        <div className="skill-library-detail-actions">
          <span className={entry.assignable ? "plugin-chip good" : "plugin-chip"}>
            {entry.assignable ? t("plugins.skillLibraryAssignable") : t("plugins.skillLibraryNotAssignable")}
          </span>
          {entry.editable_metadata ? (
            <label className="skill-library-enabled-toggle">
              <span>{entry.enabled ? t("plugins.skillLibraryEnabled") : t("plugins.skillLibraryDisabled")}</span>
              <input
                type="checkbox"
                role="switch"
                aria-label={t("plugins.skillLibraryEnabledLabel")}
                checked={entry.enabled}
                disabled={updatingEnabled}
                onChange={(event) => void updateEnabled(event.target.checked)}
              />
            </label>
          ) : null}
          {deletable ? (
            <button className="plugin-delete-pill" type="button" onClick={() => deleteSkill(entry.id)}>
              {t("common.delete")}
            </button>
          ) : null}
        </div>
      </div>
      <div className="skill-library-detail-meta">
        <span>{t("plugins.skillLibrarySource")}: {entry.source}</span>
        <span>{entry.core ? t("plugins.coreChip") : entry.metadata_status === "ready" ? t("plugins.skillLibraryMetadata") : t("plugins.skillLibraryIncomplete")}</span>
        <span>{t("plugins.skillLibraryUsage")}: {usedCount} / {rejectedCount}</span>
      </div>
      <SkillLibraryList label={t("plugins.skillLibraryUseWhen")} values={entry.use_when} />
      <SkillLibraryList label={t("plugins.skillLibraryDoNotUse")} values={entry.do_not_use_when} muted />
      <SkillLibraryList label={t("plugins.skillLibraryNegativeQueries")} values={entry.negative_queries} muted />
      <SkillLibraryMap label={t("plugins.skillLibraryDistinguish")} values={entry.distinguish_from} />
      <div className={hasSuggestion ? "skill-library-suggestion" : "skill-library-suggestion empty"}>
        {hasSuggestion ? (
          <>
            <div className="skill-library-suggestion-heading">
              <div>
                <strong>{t("plugins.skillLibraryPendingAdvice")}</strong>
                {entry.metadata_proposal ? <SkillMetadataProposalSummary t={t} proposal={entry.metadata_proposal} /> : null}
              </div>
              {entry.editable_metadata ? (
                <button
                  className="secondary-button compact skill-library-suggestion-toggle"
                  type="button"
                  onClick={() => setEditingAdvice((current) => !current)}
                >
                  {editingAdvice ? t("common.close") : t("plugins.skillLibraryViewAdvice")}
                </button>
              ) : null}
            </div>
            {editingAdvice ? (
              <div className="skill-library-advice-editor">
                <SkillMetadataInput label={t("plugins.skillLibrarySuggestedCategories")} value={categories} onChange={setCategories} />
                <SkillMetadataInput label={t("plugins.skillLibraryTags")} value={tags} onChange={setTags} />
                <SkillMetadataInput label={t("plugins.skillLibraryUseWhen")} value={useWhen} onChange={setUseWhen} />
                <SkillMetadataInput label={t("plugins.skillLibraryDoNotUse")} value={doNotUseWhen} onChange={setDoNotUseWhen} />
                <SkillMetadataInput label={t("plugins.skillLibraryNegativeQueries")} value={negativeQueries} onChange={setNegativeQueries} />
                <SkillMetadataInput label={t("plugins.skillLibraryDistinguish")} value={distinguishFrom} onChange={setDistinguishFrom} />
                {entry.metadata_status !== "ready" && !completionReady ? <small>{t("plugins.skillLibraryCompletionRequired")}</small> : null}
                <div className="skill-library-advice-actions">
                  <button className="secondary-button compact" type="button" disabled={savingAdvice} onClick={() => setEditingAdvice(false)}>{t("common.cancel")}</button>
                  <button className="primary-button compact" type="button" disabled={savingAdvice || (entry.metadata_status !== "ready" && !completionReady)} onClick={() => void applyAdvice()}>{savingAdvice ? t("common.saving") : entry.metadata_status === "ready" ? t("plugins.skillLibraryApplyAdvice") : t("plugins.skillLibraryCompleteMetadata")}</button>
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <>
            <strong>{t("plugins.skillLibraryPendingAdvice")}</strong>
            <span>{t("plugins.skillLibraryNoAdvice")}</span>
          </>
        )}
      </div>
    </article>
  );
}

function SkillMetadataProposalSummary({ t, proposal }: { t: Translator; proposal: PluginSkillMetadataProposal }) {
  return (
    <div className="skill-library-proposal" title={proposal.updated_at}>
      <span>{metadataProposalSourceLabel(t, proposal.source)}</span>
      <span className={proposal.status === "pending" ? "pending" : "muted"}>{metadataProposalStatusLabel(t, proposal.status)}</span>
      {proposal.updated_at ? <span className="muted">{shortProposalTimestamp(proposal.updated_at)}</span> : null}
      {proposal.reason ? <small>{proposal.reason}</small> : null}
    </div>
  );
}

function metadataProposalSourceLabel(t: Translator, source: string): string {
  if (source === "rules") return t("plugins.skillProposalRules");
  if (source === "ai") return t("plugins.skillProposalAi");
  return source || t("plugins.skillProposalUnknown");
}

function metadataProposalStatusLabel(t: Translator, status: string): string {
  if (status === "pending") return t("plugins.skillProposalPending");
  if (status === "stale") return t("plugins.skillProposalStale");
  if (status === "accepted") return t("plugins.skillProposalAccepted");
  if (status === "rejected") return t("plugins.skillProposalRejected");
  return status || t("plugins.skillProposalUnknown");
}

function shortProposalTimestamp(value: string): string {
  return value.replace("T", " ").replace(/\.\d{3}Z$/, "Z").slice(0, 16);
}

function parseDistinguishFrom(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of value.split("\n")) {
    const separator = line.indexOf(":");
    if (separator <= 0) continue;
    const key = line.slice(0, separator).trim();
    const description = line.slice(separator + 1).trim();
    if (key && description) result[key] = description;
  }
  return result;
}

function SkillMetadataInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label>
      <span>{label}</span>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} aria-label={label} />
    </label>
  );
}

type SkillMetadataPayload = {
  categories?: string[];
  tags?: string[];
  use_when?: string[];
  do_not_use_when?: string[];
  negative_queries: string[];
  distinguish_from: Record<string, string>;
};

function mergeLines(existing: string[], suggestions: string[]): string {
  return [...existing, ...suggestions]
    .map((value) => value.trim())
    .filter((value, index, values) => Boolean(value) && values.indexOf(value) === index)
    .join("\n");
}

function mergeMappings(existing: Record<string, string>, suggestions: Record<string, string>): string {
  return Object.entries({ ...suggestions, ...existing }).map(([key, value]) => `${key}: ${value}`).join("\n");
}

function SkillLibraryList({ label, values, muted = false }: { label?: string; values: string[]; muted?: boolean }) {
  if (!values.length) return null;
  return (
    <div className={muted ? "skill-library-detail-block muted" : "skill-library-detail-block"}>
      {label ? <strong>{label}</strong> : null}
      <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul>
    </div>
  );
}

function SkillLibraryMap({ label, values }: { label?: string; values: Record<string, string> }) {
  const rows = Object.entries(values);
  if (!rows.length) return null;
  return (
    <div className="skill-library-detail-block muted">
      {label ? <strong>{label}</strong> : null}
      <ul>{rows.map(([key, value]) => <li key={key}><b>{key}</b>: {value}</li>)}</ul>
    </div>
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
  capability,
}: {
  capability: RuntimeCapabilityCardModel;
}) {
  return (
    <article className="runtime-capability-strip">
      <div className="runtime-capability-identity">
        <div className="runtime-capability-card-title">{capability.id}</div>
        <span>{capability.status}</span>
      </div>
      <div className="runtime-capability-summary">
        <span className="runtime-capability-card-description">{capability.description}</span>
        <div className="runtime-capability-chip-row">
          {capability.abilities.map((ability) => (
            <span className="runtime-capability-chip" key={ability}>
              {ability}
            </span>
          ))}
        </div>
      </div>
      <span className="runtime-capability-risk">{capability.risk}</span>
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
  sourceKind = "skill",
  compact = false,
  toolbar = false,
}: {
  t: Translator;
  title: string;
  description: string;
  installing: boolean;
  onInstall: (path: string) => void;
  sourceKind?: "skill" | "mcp" | "package";
  compact?: boolean;
  toolbar?: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState("");
  const choosePluginSource = typeof window === "undefined" ? undefined : window.lucodeDesktop?.choosePluginSource;

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

  async function chooseSource() {
    const sourcePath = await choosePluginSource?.(sourceKind);
    if (sourcePath) onInstall(sourcePath);
  }

  return (
    <div
      className={dragging ? `plugin-drop-zone${compact ? " compact" : ""}${toolbar ? " toolbar" : ""} dragging` : `plugin-drop-zone${compact ? " compact" : ""}${toolbar ? " toolbar" : ""}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      role="group"
      aria-label={title}
    >
      {toolbar ? (
        !choosePluginSource ? <span className="plugin-drop-zone-toolbar-label">{installing ? t("plugins.installing") : title}</span> : null
      ) : (
        <>
          <div>
            <strong>{installing ? t("plugins.installing") : title}</strong>
            {compact ? null : <span>{description}</span>}
          </div>
          <small>{installing ? t("common.installing") : t("common.drop")}</small>
        </>
      )}
      {choosePluginSource ? (
        <button className={toolbar ? "secondary-button compact plugin-import-button" : "secondary-button compact"} type="button" disabled={installing} onClick={() => void chooseSource()}>
          {toolbar ? title : t("plugins.chooseSource")}
        </button>
      ) : null}
      {localError ? <em>{localError}</em> : null}
    </div>
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
  return resolveDroppedFilePaths(files, (file) => window.lucodeDesktop?.droppedFilePath?.(file) || "");
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
