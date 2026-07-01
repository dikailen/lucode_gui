import type { ModelSettingsModel, RuntimeModel } from "../shared/types";

type DisplayModel = Pick<RuntimeModel, "id" | "display_name" | "provider"> & Partial<Pick<ModelSettingsModel, "model_name">>;

export function displayModelNameForModel(model: DisplayModel): string {
  return displayModelNameFromParts({
    id: model.id,
    label: model.display_name,
    provider: model.provider,
    modelName: model.model_name,
  });
}

export function displayModelNameFromParts({
  id,
  label = "",
  provider = "",
  modelName = "",
}: {
  id: string;
  label?: string;
  provider?: string;
  modelName?: string;
}): string {
  const cleanModelName = modelName.trim();
  if (cleanModelName) {
    return cleanModelName;
  }

  const cleanLabel = label.trim();
  const labelTail = cleanLabel.split(/\s+/).pop() ?? "";
  if (cleanLabel && labelTail !== cleanLabel && looksLikePublicModelName(labelTail)) {
    return labelTail;
  }

  const modelNameFromId = publicModelNameFromId(id, provider);
  if (!cleanLabel || looksLikeInternalName(cleanLabel)) {
    return modelNameFromId || cleanLabel || id;
  }

  return cleanLabel || modelNameFromId || id;
}

export function compactDisplayModelName(model: DisplayModel, limit = 32): string {
  const text = displayModelNameForModel(model);
  return text.length > limit ? `${text.slice(0, Math.max(1, limit - 1))}...` : text;
}

function publicModelNameFromId(id: string, provider: string): string {
  const cleanId = id.trim();
  if (!cleanId) {
    return "";
  }
  if (cleanId.includes("/")) {
    return cleanId.split("/").pop() || cleanId;
  }
  const providerAlias = normalizeAlias(provider);
  const idAlias = normalizeAlias(cleanId);
  if (providerAlias && idAlias.startsWith(`${providerAlias}_`)) {
    return publicNameFromInternalId(cleanId.slice(providerAlias.length + 1));
  }
  if (providerAlias && idAlias.startsWith(`${providerAlias}-`)) {
    return publicNameFromInternalId(cleanId.slice(providerAlias.length + 1));
  }
  if (looksLikeInternalName(cleanId)) {
    return publicNameFromInternalId(cleanId);
  }
  return cleanId;
}

function looksLikePublicModelName(value: string): boolean {
  const text = value.trim();
  if (!text || text.toLowerCase() === "model") {
    return false;
  }
  if (text.toLowerCase().endsWith("_model")) {
    return false;
  }
  return /[-./:]/.test(text) || /\d/.test(text);
}

function looksLikeInternalName(value: string): boolean {
  const text = value.trim().toLowerCase();
  return Boolean(text) && (text.includes("custom_openai_compatible") || text.includes("_") || text.endsWith("_model"));
}

function publicNameFromInternalId(value: string): string {
  return value
    .trim()
    .replace(/_model$/i, "")
    .replace(/_+/g, "-")
    .replace(/(?<=\d)-(?=\d)/g, ".");
}

function normalizeAlias(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}
