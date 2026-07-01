import { describe, expect, it } from "vitest";

import { createTranslator, normalizeLanguage } from "./i18n";

describe("desktop i18n", () => {
  it("normalizes supported languages and falls back to Chinese", () => {
    expect(normalizeLanguage("en")).toBe("en");
    expect(normalizeLanguage("zh")).toBe("zh");
    expect(normalizeLanguage("fr")).toBe("zh");
    expect(normalizeLanguage("")).toBe("zh");
  });

  it("returns common sidebar and chat labels in English", () => {
    const t = createTranslator("en");

    expect(t("sidebar.chat")).toBe("Chats");
    expect(t("sidebar.plugins")).toBe("Plugins");
    expect(t("chat.placeholder")).toBe("Type a message");
    expect(t("chat.orchestratorModel", { model: "gpt-5.5" })).toBe("gpt-5.5");
    expect(t("common.settings")).toBe("Settings");
  });

  it("keeps Chinese as the default UI language", () => {
    const t = createTranslator("zh");

    expect(t("sidebar.chat")).toBe("\u4f1a\u8bdd");
    expect(t("sidebar.plugins")).toBe("\u63d2\u4ef6");
    expect(t("chat.placeholder")).toBe("\u8f93\u5165\u6d88\u606f");
    expect(t("chat.orchestratorModel", { model: "gpt-5.5" })).toBe("gpt-5.5");
  });

  it("supports simple interpolation", () => {
    const t = createTranslator("en");

    expect(t("sidebar.sessionCount", { count: 3 })).toBe("3 chats");
  });

  it("translates Provider settings and run status labels", () => {
    const t = createTranslator("en");

    expect(t("settings.providerFormTitle")).toBe("Provider access");
    expect(t("settings.providerName")).toBe("Provider name");
    expect(t("settings.providerKeySavedHint", { key: "****cret" })).toBe("Key saved ****cret");
    expect(t("settings.providerFetchSuccess", { count: 2 })).toBe("Fetched 2 models.");
    expect(t("runStage.planningLabel")).toBe("Planning");
    expect(t("time.minutesAgo", { count: 5 })).toBe("5 min ago");
  });
});
