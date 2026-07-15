import type { Translator } from "./i18n";

export type SettingsTab = "models" | "privacy" | "providers" | "language" | "shortcuts" | "about";

export const SETTINGS_TABS: ReadonlyArray<{ id: SettingsTab; labelKey: Parameters<Translator>[0] }> = [
  { id: "models", labelKey: "settings.models" },
  { id: "privacy", labelKey: "settings.privacy" },
  { id: "providers", labelKey: "settings.providers" },
  { id: "language", labelKey: "settings.language" },
  { id: "shortcuts", labelKey: "settings.shortcuts" },
  { id: "about", labelKey: "settings.about" },
];
