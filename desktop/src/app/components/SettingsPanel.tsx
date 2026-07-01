import { useMemo, useState, type ReactNode } from "react";

import type { Translator } from "../i18n";
import { compactDisplayModelName, displayModelNameForModel } from "../modelDisplay";
import type {
  ModelSettingsModel,
  ModelSettingsProvider,
  ModelSettingsResponse,
  ProviderCatalogItem,
  ProviderCatalogResponse,
  ProviderModelsFetchPayload,
  ProviderModelsFetchResponse,
  ProviderSettingsPayload,
} from "../../shared/types";

export type SettingsPanelProps = {
  t: Translator;
  modelSettings: ModelSettingsResponse | null;
  providerCatalog: ProviderCatalogResponse | null;
  settingsError: string;
  settingsSavingRole: string;
  closeSettings: () => void;
  refreshModelSettings: () => void;
  refreshProviderCatalog: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
  updateQueryRefiner: (enabled: boolean) => void;
  updatePrivacyMode: (mode: string) => void;
  updateWorkerPool: (modelIds: string[]) => void;
  updateLanguage: (language: string) => void;
  saveProvider: (payload: ProviderSettingsPayload, providerId?: string) => Promise<boolean>;
  deleteProvider: (providerId: string) => Promise<boolean>;
  fetchProviderModels: (payload: ProviderModelsFetchPayload) => Promise<ProviderModelsFetchResponse | null>;
};

type SettingsTab = "models" | "privacy" | "providers" | "language" | "shortcuts" | "about";

type ProviderFormState = {
  editingProviderId: string;
  presetId: string;
  providerId: string;
  displayName: string;
  homepage: string;
  baseUrl: string;
  compatibleType: string;
  apiKey: string;
  keyHint: string;
  keyConfigured: boolean;
  modelsText: string;
  local: boolean;
  supportsTools: boolean;
  custom: boolean;
};

export const SAVED_PROVIDER_KEY_MASK = "************************************************";

export function providerApiKeyInputValue(apiKey: string, keyConfigured: boolean, fieldActive: boolean): string {
  if (apiKey) {
    return apiKey;
  }
  return keyConfigured && !fieldActive ? SAVED_PROVIDER_KEY_MASK : "";
}

export function providerIdForNewConnection(
  displayName: string,
  presetId: string,
  custom: boolean,
  existingProviderIds: string[],
): string {
  if (!custom && presetId) {
    return presetId;
  }
  const base = slugProviderName(displayName) || "custom_provider";
  const existing = new Set(existingProviderIds.map((item) => item.trim()).filter(Boolean));
  if (!existing.has(base)) {
    return base;
  }
  let suffix = 2;
  while (existing.has(`${base}_${suffix}`)) {
    suffix += 1;
  }
  return `${base}_${suffix}`;
}

const TABS: Array<{ id: SettingsTab; labelKey: Parameters<Translator>[0] }> = [
  { id: "models", labelKey: "settings.models" },
  { id: "privacy", labelKey: "settings.privacy" },
  { id: "providers", labelKey: "settings.providers" },
  { id: "language", labelKey: "settings.language" },
  { id: "shortcuts", labelKey: "settings.shortcuts" },
  { id: "about", labelKey: "settings.about" },
];

export function SettingsPanel({
  t,
  modelSettings,
  providerCatalog,
  settingsError,
  settingsSavingRole,
  closeSettings,
  refreshModelSettings,
  refreshProviderCatalog,
  updateRoleModel,
  updateQueryRefiner,
  updatePrivacyMode,
  updateWorkerPool,
  updateLanguage,
  saveProvider,
  deleteProvider,
  fetchProviderModels,
}: SettingsPanelProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>("models");
  const configuredModels = modelSettings?.models.filter((model) => model.configured) ?? [];

  return (
    <section className="settings-workspace" aria-label={t("settings.title")}>
      <header className="settings-workspace-header">
        <div>
          <h1>{t("settings.title")}</h1>
          <p>{t("settings.description")}</p>
        </div>
        <button className="settings-close-x" type="button" title={t("settings.closeTitle")} onClick={closeSettings}>
          x
        </button>
      </header>

      <nav className="settings-tabs" aria-label={t("settings.tabsAria")}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? "settings-tab active" : "settings-tab"}
            type="button"
            onClick={() => setActiveTab(tab.id)}
          >
            {t(tab.labelKey)}
          </button>
        ))}
      </nav>

      {settingsError ? <div className="runtime-error settings-workspace-error">{settingsError}</div> : null}

      {!modelSettings ? (
        <div className="settings-empty">{t("settings.loading")}</div>
      ) : activeTab === "models" ? (
        <ModelsSettingsPage
          t={t}
          modelSettings={modelSettings}
          configuredModels={configuredModels}
          settingsSavingRole={settingsSavingRole}
          refreshModelSettings={refreshModelSettings}
          updateRoleModel={updateRoleModel}
          updateQueryRefiner={updateQueryRefiner}
          updateWorkerPool={updateWorkerPool}
        />
      ) : activeTab === "privacy" ? (
        <PrivacySettingsPage t={t} modelSettings={modelSettings} settingsSavingRole={settingsSavingRole} updatePrivacyMode={updatePrivacyMode} />
      ) : activeTab === "providers" ? (
        <ProvidersSettingsPage
          t={t}
          modelSettings={modelSettings}
          providerCatalog={providerCatalog}
          settingsSavingRole={settingsSavingRole}
          refreshModelSettings={refreshModelSettings}
          refreshProviderCatalog={refreshProviderCatalog}
          saveProvider={saveProvider}
          deleteProvider={deleteProvider}
          fetchProviderModels={fetchProviderModels}
        />
      ) : activeTab === "language" ? (
        <LanguageSettingsPage t={t} modelSettings={modelSettings} settingsSavingRole={settingsSavingRole} updateLanguage={updateLanguage} />
      ) : activeTab === "shortcuts" ? (
        <ShortcutsSettingsPage t={t} />
      ) : (
        <AboutSettingsPage t={t} modelSettings={modelSettings} />
      )}
    </section>
  );
}

function ModelsSettingsPage({
  t,
  modelSettings,
  configuredModels,
  settingsSavingRole,
  refreshModelSettings,
  updateRoleModel,
  updateQueryRefiner,
  updateWorkerPool,
}: {
  t: Translator;
  modelSettings: ModelSettingsResponse;
  configuredModels: ModelSettingsModel[];
  settingsSavingRole: string;
  refreshModelSettings: () => void;
  updateRoleModel: (role: string, modelId: string) => void;
  updateQueryRefiner: (enabled: boolean) => void;
  updateWorkerPool: (modelIds: string[]) => void;
}) {
  const preferences = runtimePreferences(modelSettings);
  const selectedWorkerPool = new Set(preferences.allowed_worker_models);
  const role = (roleId: string) => modelSettings.roles.find((item) => item.role === roleId)?.selected_model_id || "";

  function toggleWorkerModel(modelId: string) {
    const next = new Set(selectedWorkerPool);
    if (next.has(modelId)) {
      next.delete(modelId);
    } else {
      next.add(modelId);
    }
    updateWorkerPool([...next]);
  }

  return (
    <div className="settings-page-scroll">
      <div className="settings-page-title-row">
        <div>
          <h2>{t("settings.models")}</h2>
          <p>{t("settings.modelsDescription")}</p>
        </div>
        <button className="secondary-button" type="button" onClick={refreshModelSettings}>
          {t("common.refresh")}
        </button>
      </div>
      <section className="settings-large-card">
        <SettingsRow
          title={t("settings.queryRefiner")}
          description={t("settings.queryRefinerDescription")}
          control={
            <button
              className={preferences.query_refiner_enabled ? "settings-toggle-on" : "settings-toggle-off"}
              type="button"
              disabled={settingsSavingRole === "query_refiner_enabled"}
              onClick={() => updateQueryRefiner(!preferences.query_refiner_enabled)}
            >
              {preferences.query_refiner_enabled ? t("common.on") : t("common.off")}
            </button>
          }
        />
        <SettingsDivider />
        <RoleSelectRow
          t={t}
          label={t("settings.leadModel")}
          role="orchestrator"
          selectedModelId={role("orchestrator")}
          configuredModels={configuredModels}
          settingsSavingRole={settingsSavingRole}
          updateRoleModel={updateRoleModel}
        />
        <SettingsDivider />
        <RoleSelectRow
          t={t}
          label={t("settings.executorModel")}
          role="executor"
          selectedModelId={role("executor")}
          configuredModels={configuredModels}
          settingsSavingRole={settingsSavingRole}
          updateRoleModel={updateRoleModel}
        />
        <SettingsDivider />
        <RoleSelectRow
          t={t}
          label={t("settings.finalSynthesizer")}
          role="final_synthesizer"
          selectedModelId={role("final_synthesizer")}
          configuredModels={configuredModels}
          settingsSavingRole={settingsSavingRole}
          updateRoleModel={updateRoleModel}
        />
        <SettingsDivider />
        <div className="settings-config-block">
          <div className="settings-config-block-header">
            <div>
              <div className="settings-config-title">{t("settings.workerPool")}</div>
              <div className="settings-config-description">{t("settings.workerPoolDescription")}</div>
            </div>
            <span className="settings-inline-status">{selectedWorkerPool.size || t("common.all")}</span>
          </div>
          <div className="executor-pool-grid">
            {configuredModels.length ? (
              configuredModels.map((model) => (
                <button
                  key={model.id}
                  className={selectedWorkerPool.has(model.id) ? "executor-pool-chip active" : "executor-pool-chip"}
                  type="button"
                  onClick={() => toggleWorkerModel(model.id)}
                  disabled={settingsSavingRole === "worker_pool"}
                  title={displayModelNameForModel(model)}
                >
                  {compactDisplayModelName(model, 24)}
                </button>
              ))
            ) : (
              <div className="settings-muted-line">{t("settings.noConfiguredModels")}</div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function PrivacySettingsPage({
  t,
  modelSettings,
  settingsSavingRole,
  updatePrivacyMode,
}: {
  t: Translator;
  modelSettings: ModelSettingsResponse;
  settingsSavingRole: string;
  updatePrivacyMode: (mode: string) => void;
}) {
  const activeMode = runtimePreferences(modelSettings).privacy_mode || "local_first";
  const options = [
    ["offline", t("settings.privacyOffline"), t("settings.privacyOfflineDescription")],
    ["local_first", t("settings.privacyLocalFirst"), t("settings.privacyLocalFirstDescription")],
    ["cloud_allowed", t("settings.privacyCloudAllowed"), t("settings.privacyCloudAllowedDescription")],
  ];
  return (
    <InfoShell title={t("settings.privacy")} description={t("settings.privacyDescription")}>
      <div className="privacy-option-grid">
        {options.map(([mode, label, description]) => (
          <button
            key={mode}
            className={activeMode === mode ? "privacy-option active" : "privacy-option"}
            type="button"
            disabled={settingsSavingRole === "privacy_mode"}
            onClick={() => updatePrivacyMode(mode)}
          >
            <span>{label}</span>
            <small>{description}</small>
          </button>
        ))}
      </div>
    </InfoShell>
  );
}

function ProvidersSettingsPage({
  t,
  modelSettings,
  providerCatalog,
  settingsSavingRole,
  refreshModelSettings,
  refreshProviderCatalog,
  saveProvider,
  deleteProvider,
  fetchProviderModels,
}: {
  t: Translator;
  modelSettings: ModelSettingsResponse;
  providerCatalog: ProviderCatalogResponse | null;
  settingsSavingRole: string;
  refreshModelSettings: () => void;
  refreshProviderCatalog: () => void;
  saveProvider: (payload: ProviderSettingsPayload, providerId?: string) => Promise<boolean>;
  deleteProvider: (providerId: string) => Promise<boolean>;
  fetchProviderModels: (payload: ProviderModelsFetchPayload) => Promise<ProviderModelsFetchResponse | null>;
}) {
  const catalog = providerCatalog?.providers ?? [];
  const [form, setForm] = useState<ProviderFormState>(() => createProviderForm(catalog[0]));
  const [formError, setFormError] = useState("");
  const [formNote, setFormNote] = useState("");
  const [pendingDeleteProvider, setPendingDeleteProvider] = useState("");
  const [apiKeyFieldActive, setApiKeyFieldActive] = useState(false);

  const configuredModelsByProvider = useMemo(() => {
    const grouped = new Map<string, string[]>();
    for (const model of modelSettings.models) {
      const values = grouped.get(model.provider) ?? [];
      values.push(model.model_name || model.display_name || model.id);
      grouped.set(model.provider, values);
    }
    return grouped;
  }, [modelSettings.models]);

  function refreshProviders() {
    refreshModelSettings();
    refreshProviderCatalog();
  }

  function patchForm(patch: Partial<ProviderFormState>) {
    setForm((current) => ({ ...current, ...patch }));
    setFormError("");
    setFormNote("");
  }

  function selectPreset(providerId: string) {
    const preset = catalog.find((item) => item.provider === providerId);
    setForm(createProviderForm(preset));
    setApiKeyFieldActive(false);
    setFormError("");
    setFormNote("");
  }

  function editProvider(provider: ModelSettingsProvider) {
    const fallbackModels = configuredModelsByProvider.get(provider.provider) ?? [];
    setForm(createProviderFormFromProvider(provider, fallbackModels));
    setApiKeyFieldActive(false);
    setFormError("");
    setFormNote("");
  }

  async function submitProvider() {
    const existingProviderIds = modelSettings.providers
      .map((provider) => provider.provider)
      .filter((providerId) => providerId !== form.editingProviderId);
    const cleanProviderId = form.editingProviderId
      ? form.providerId.trim()
      : providerIdForNewConnection(form.displayName, form.presetId, form.custom, existingProviderIds);
    const cleanBaseUrl = form.baseUrl.trim();
    if (!cleanProviderId) {
      setFormError(t("settings.providerMissingId"));
      return;
    }
    if (!cleanBaseUrl && !form.local) {
      setFormError(t("settings.providerMissingBaseUrl"));
      return;
    }
    const payload: ProviderSettingsPayload = {
      provider_id: cleanProviderId,
      display_name: form.displayName.trim() || cleanProviderId,
      homepage: form.homepage.trim(),
      base_url: cleanBaseUrl,
      api_key: form.apiKey,
      models: splitModels(form.modelsText),
      compatible_type: form.compatibleType.trim() || "openai_compatible",
      local: form.local,
      supports_tools: form.supportsTools,
      custom: form.custom,
    };
    const ok = await saveProvider(payload, form.editingProviderId);
    if (ok) {
      setForm(createProviderForm(catalog.find((item) => item.provider === form.presetId) ?? catalog[0]));
      setApiKeyFieldActive(false);
      setFormNote("");
      setFormError("");
    }
  }

  async function fetchModels() {
    const payload: ProviderModelsFetchPayload = {
      provider_id: form.providerId.trim() || undefined,
      base_url: form.baseUrl.trim(),
      api_key: form.apiKey,
      compatible_type: form.compatibleType.trim() || "openai_compatible",
      local: form.local,
    };
    const result = await fetchProviderModels(payload);
    if (!result) {
      return;
    }
    if (!result.ok) {
      setFormNote("");
      setFormError(t("settings.providerFetchFailed", { error: result.error || result.source || "unknown" }));
      return;
    }
    setForm((current) => ({ ...current, modelsText: result.models.join("\n") }));
    setFormError("");
    setFormNote(t("settings.providerFetchSuccess", { count: result.models.length }));
  }

  async function requestDelete(providerId: string) {
    if (pendingDeleteProvider !== providerId) {
      setPendingDeleteProvider(providerId);
      return;
    }
    const ok = await deleteProvider(providerId);
    if (ok) {
      setPendingDeleteProvider("");
      if (form.editingProviderId === providerId) {
        setForm(createProviderForm(catalog[0]));
        setApiKeyFieldActive(false);
      }
    }
  }

  return (
    <div className="settings-page-scroll">
      <div className="settings-page-title-row">
        <div>
          <h2>{t("settings.providers")}</h2>
          <p>{t("settings.providersDescription")}</p>
        </div>
        <button className="secondary-button" type="button" onClick={refreshProviders}>
          {t("common.refresh")}
        </button>
      </div>

      <section className="settings-large-card provider-form-card">
        <div className="provider-form-heading">
          <div>
            <div className="settings-card-heading">
              {form.editingProviderId
                ? t("settings.providerFormEditing", { provider: form.displayName || form.providerId })
                : t("settings.providerFormTitle")}
            </div>
            <div className="settings-config-description">{t("settings.providerFormDescription")}</div>
          </div>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              setForm(createProviderForm(catalog[0]));
              setApiKeyFieldActive(false);
            }}
          >
            {t("settings.providerCreateNew")}
          </button>
        </div>
        <div className="provider-form">
          <ProviderField label={t("settings.providerPreset")}>
            <select value={form.presetId} disabled={!catalog.length} onChange={(event) => selectPreset(event.target.value)}>
              {catalog.length ? (
                catalog.map((provider) => (
                  <option key={provider.provider} value={provider.provider}>
                    {provider.display_name}
                  </option>
                ))
              ) : (
                <option value="custom">{t("settings.providerNoCatalog")}</option>
              )}
            </select>
          </ProviderField>
          <ProviderField label={t("settings.providerName")}>
            <input
              value={form.displayName}
              placeholder={t("settings.providerNamePlaceholder")}
              onChange={(event) => patchForm({ displayName: event.target.value })}
            />
          </ProviderField>
          <ProviderField label={t("settings.providerType")}>
            <input value={form.compatibleType} onChange={(event) => patchForm({ compatibleType: event.target.value })} />
          </ProviderField>
          <ProviderField label={t("settings.providerHomepage")}>
            <input value={form.homepage} onChange={(event) => patchForm({ homepage: event.target.value })} />
          </ProviderField>
          <ProviderField label={t("settings.providerBaseUrl")}>
            <input value={form.baseUrl} onChange={(event) => patchForm({ baseUrl: event.target.value })} />
          </ProviderField>
          <ProviderField label={t("settings.providerApiKey")}>
            <input
              type="password"
              value={providerApiKeyInputValue(form.apiKey, form.keyConfigured, apiKeyFieldActive)}
              placeholder={form.keyConfigured ? "" : t("settings.providerApiKeyPlaceholderNew")}
              title={
                form.keyConfigured
                  ? t("settings.providerApiKeyPlaceholderExisting", { key: form.keyHint || "****" })
                  : t("settings.providerApiKeyPlaceholderNew")
              }
              autoComplete="new-password"
              onFocus={(event) => {
                if (form.keyConfigured && !form.apiKey) {
                  event.currentTarget.select();
                }
              }}
              onBlur={() => {
                if (!form.apiKey) {
                  setApiKeyFieldActive(false);
                }
              }}
              onChange={(event) => {
                setApiKeyFieldActive(true);
                patchForm({ apiKey: event.target.value });
              }}
            />
          </ProviderField>
          <ProviderField label={t("settings.providerModels")} wide>
            <div className="provider-field-title-row">
              <span>{t("settings.providerModelsHint")}</span>
              <button
                className="secondary-button"
                type="button"
                disabled={settingsSavingRole === "provider_fetch_models"}
                onClick={fetchModels}
              >
                {t("settings.providerFetchModels")}
              </button>
            </div>
            <textarea value={form.modelsText} onChange={(event) => patchForm({ modelsText: event.target.value })} />
          </ProviderField>
          <div className="provider-checkbox-row">
            <label>
              <input type="checkbox" checked={form.local} onChange={(event) => patchForm({ local: event.target.checked })} />
              <span>{t("settings.providerLocal")}</span>
            </label>
            <label>
              <input type="checkbox" checked={form.supportsTools} onChange={(event) => patchForm({ supportsTools: event.target.checked })} />
              <span>{t("settings.providerSupportsTools")}</span>
            </label>
            <label>
              <input type="checkbox" checked={form.custom} onChange={(event) => patchForm({ custom: event.target.checked })} />
              <span>{t("settings.providerCustom")}</span>
            </label>
          </div>
          {formError ? <div className="provider-form-error">{formError}</div> : null}
          {formNote ? <div className="provider-form-note good">{formNote}</div> : null}
          <div className="provider-form-actions">
            <button
              className="primary-button"
              type="button"
              disabled={settingsSavingRole.startsWith("provider")}
              onClick={submitProvider}
            >
              {settingsSavingRole.startsWith("provider") ? t("common.saving") : t("common.save")}
            </button>
          </div>
        </div>
      </section>

      <section className="settings-large-card compact">
        <div className="settings-card-heading">{t("settings.providerConfiguredList")}</div>
        <div className="settings-provider-list">
          {modelSettings.providers.length ? (
            modelSettings.providers.map((provider) => (
              <div className="settings-provider-row" key={provider.provider}>
                <div>
                  <div className="settings-config-title">{provider.display_name}</div>
                  {provider.base_url ? <div className="settings-config-description">{provider.base_url}</div> : null}
                  <div className="provider-meta-line">
                    <span>{provider.compatible_type || "openai_compatible"}</span>
                    {provider.base_url ? <span>{provider.base_url}</span> : null}
                    {provider.local ? <span>{t("settings.providerLocal")}</span> : null}
                    {!provider.local ? (
                      <span>
                        {provider.key_configured
                          ? t("settings.providerKeySavedHint", { key: provider.key_hint || "****" })
                          : t("settings.providerKeyMissing")}
                      </span>
                    ) : null}
                    <span>
                      {t("settings.providerModelCount", {
                        count: provider.configured_model_count || provider.models?.length || 0,
                      })}
                    </span>
                  </div>
                </div>
                <div className="provider-row-actions">
                  <span className={provider.configured ? "status-chip good" : "status-chip muted"}>
                    {provider.configured_model_count}/{provider.model_count}
                  </span>
                  <button
                    className="secondary-button"
                    type="button"
                    title={t("settings.providerManageModels")}
                    onClick={() => editProvider(provider)}
                  >
                    {t("settings.providerManageModels")}
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    title={t("settings.providerUpdateKey")}
                    onClick={() => editProvider(provider)}
                  >
                    {t("settings.providerUpdateKey")}
                  </button>
                  <button
                    className={pendingDeleteProvider === provider.provider ? "danger-pill confirming" : "danger-pill"}
                    type="button"
                    title={pendingDeleteProvider === provider.provider ? t("settings.providerConfirmDelete") : t("settings.providerDelete")}
                    disabled={settingsSavingRole === `provider_delete:${provider.provider}`}
                    onClick={() => void requestDelete(provider.provider)}
                  >
                    {pendingDeleteProvider === provider.provider ? t("common.confirm") : t("common.delete")}
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="settings-muted-line">{t("settings.providerNoProviders")}</div>
          )}
        </div>
      </section>

      <section className="settings-large-card compact">
        <div className="settings-card-heading">{t("settings.providerCatalog")}</div>
        <div className="settings-provider-list">
          {catalog.length ? (
            catalog.map((provider) => (
              <ReadOnlyInfoRow key={provider.provider} label={provider.display_name} value={provider.base_url || provider.provider} />
            ))
          ) : (
            <div className="settings-muted-line">{t("settings.providerNoCatalog")}</div>
          )}
        </div>
      </section>
    </div>
  );
}

function LanguageSettingsPage({
  t,
  modelSettings,
  settingsSavingRole,
  updateLanguage,
}: {
  t: Translator;
  modelSettings: ModelSettingsResponse;
  settingsSavingRole: string;
  updateLanguage: (language: string) => void;
}) {
  const currentLanguage = modelSettings.ui_preferences?.language || "zh";
  return (
    <InfoShell title={t("settings.languageTitle")} description={t("settings.languageDescription")}>
      <div className="language-option-row">
        {[
          ["zh", t("settings.languageChinese")],
          ["en", t("settings.languageEnglish")],
        ].map(([language, label]) => (
          <button
            key={language}
            className={currentLanguage === language ? "language-option active" : "language-option"}
            type="button"
            disabled={settingsSavingRole === "language"}
            onClick={() => updateLanguage(language)}
          >
            {label}
          </button>
        ))}
      </div>
      <SettingsDivider />
      <div className="settings-provider-list">
        <ReadOnlyInfoRow label={t("settings.currentLanguage")} value={currentLanguage === "en" ? t("settings.languageEnglish") : t("settings.languageChinese")} />
        <ReadOnlyInfoRow label={t("settings.languageConfigPath")} value=".lucode/config.toml [ui].language" />
      </div>
    </InfoShell>
  );
}

function ShortcutsSettingsPage({ t }: { t: Translator }) {
  return (
    <InfoSettingsPage
      title={t("settings.shortcuts")}
      description={t("settings.shortcutsDescription")}
      rows={[
        [t("settings.shortcutSend"), "Enter"],
        [t("settings.shortcutNewLine"), "Shift + Enter"],
        [t("settings.shortcutStop"), t("settings.shortcutStopValue")],
        [t("settings.shortcutSettings"), t("settings.shortcutSettingsValue")],
      ]}
    />
  );
}

function AboutSettingsPage({ t, modelSettings }: { t: Translator; modelSettings: ModelSettingsResponse }) {
  return (
    <InfoSettingsPage
      title={t("settings.about")}
      description={t("settings.aboutDescription")}
      rows={[
        [t("settings.version"), "0.1.0-experimental"],
        [t("settings.runtime"), "Electron + React + Python Runtime"],
        [t("settings.provider"), `${modelSettings.summary.configured_provider_count}/${modelSettings.summary.provider_count}`],
        [t("settings.models"), `${modelSettings.summary.configured_model_count}/${modelSettings.summary.model_count}`],
      ]}
    />
  );
}

function RoleSelectRow({
  t,
  label,
  role,
  selectedModelId,
  configuredModels,
  settingsSavingRole,
  updateRoleModel,
}: {
  t: Translator;
  label: string;
  role: string;
  selectedModelId: string;
  configuredModels: ModelSettingsModel[];
  settingsSavingRole: string;
  updateRoleModel: (role: string, modelId: string) => void;
}) {
  return (
    <SettingsRow
      title={label}
      control={
        <select
          className="settings-wide-select"
          value={selectedModelId}
          disabled={settingsSavingRole === role || configuredModels.length === 0}
          aria-label={label}
          onChange={(event) => updateRoleModel(role, event.target.value)}
        >
          <option value="" disabled>
            {t("common.notConfigured")}
          </option>
          {configuredModels.map((model) => (
            <option key={model.id} value={model.id}>
              {displayModelNameForModel(model)}
            </option>
          ))}
        </select>
      }
    />
  );
}

function SettingsRow({ title, description, control }: { title: string; description?: string; control: ReactNode }) {
  return (
    <div className="settings-config-row">
      <div>
        <div className="settings-config-title">{title}</div>
        {description ? <div className="settings-config-description">{description}</div> : null}
      </div>
      {control}
    </div>
  );
}

function ProviderField({ label, wide, children }: { label: string; wide?: boolean; children: ReactNode }) {
  return (
    <label className={wide ? "provider-field provider-field-wide" : "provider-field"}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function InfoShell({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <div className="settings-page-scroll">
      <div className="settings-page-title-row">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      <section className="settings-large-card compact">{children}</section>
    </div>
  );
}

function InfoSettingsPage({ title, description, rows }: { title: string; description: string; rows: Array<[string, string]> }) {
  return (
    <InfoShell title={title} description={description}>
      <div className="settings-provider-list">
        {rows.map(([label, value]) => (
          <ReadOnlyInfoRow key={label} label={label} value={value} />
        ))}
      </div>
    </InfoShell>
  );
}

function ReadOnlyInfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="settings-provider-row">
      <div className="settings-config-title">{label}</div>
      <div className="settings-config-description align-right">{value}</div>
    </div>
  );
}

function SettingsDivider() {
  return <div className="settings-divider" />;
}

function createProviderForm(preset?: ProviderCatalogItem): ProviderFormState {
  const providerId = preset?.provider || "custom_openai_compatible";
  const customRelay = Boolean(preset?.custom) || providerId === "custom_openai_compatible";
  return {
    editingProviderId: "",
    presetId: providerId,
    providerId,
    displayName: customRelay ? "" : preset?.display_name || "",
    homepage: preset?.homepage || "",
    baseUrl: preset?.base_url || "",
    compatibleType: preset?.compatible_type || "openai_compatible",
    apiKey: "",
    keyHint: "",
    keyConfigured: false,
    modelsText: (preset?.models ?? []).join("\n"),
    local: Boolean(preset?.local),
    supportsTools: preset?.supports_tools === true,
    custom: preset?.custom ?? true,
  };
}

function createProviderFormFromProvider(provider: ModelSettingsProvider, fallbackModels: string[]): ProviderFormState {
  return {
    editingProviderId: provider.provider,
    presetId: provider.provider,
    providerId: provider.provider,
    displayName: provider.display_name || provider.provider,
    homepage: provider.homepage || "",
    baseUrl: provider.base_url || "",
    compatibleType: provider.compatible_type || "openai_compatible",
    apiKey: "",
    keyHint: provider.key_hint || "",
    keyConfigured: Boolean(provider.key_configured),
    modelsText: (provider.models?.length ? provider.models : fallbackModels).join("\n"),
    local: Boolean(provider.local),
    supportsTools: provider.supports_tools === true,
    custom: Boolean(provider.custom),
  };
}

function splitModels(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function runtimePreferences(modelSettings: ModelSettingsResponse) {
  return (
    modelSettings.runtime_preferences ?? {
      execution_mode: "auto",
      privacy_mode: "local_first",
      query_refiner_enabled: false,
      allowed_worker_models: [],
      worker_pool_available: true,
    }
  );
}

function slugProviderName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
}
