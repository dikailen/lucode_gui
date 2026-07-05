import { describe, expect, it } from "vitest";

import {
  SAVED_PROVIDER_KEY_MASK,
  providerApiKeyInputValue,
  providerIdForNewConnection,
  toggleProviderModelSelection,
} from "./SettingsPanel";

describe("Provider API key field", () => {
  it("shows a saved provider key as a password-style mask while idle", () => {
    expect(providerApiKeyInputValue("", true, false)).toBe(SAVED_PROVIDER_KEY_MASK);
  });

  it("clears the saved key mask while the user is editing", () => {
    expect(providerApiKeyInputValue("", true, true)).toBe("");
  });

  it("shows the typed replacement key instead of the saved key mask", () => {
    expect(providerApiKeyInputValue("sk-new-secret", true, true)).toBe("sk-new-secret");
  });
});

describe("Provider connection id", () => {
  it("generates a hidden custom provider id from the provider name", () => {
    expect(providerIdForNewConnection("DeepSeek", "custom_openai_compatible", true, [])).toBe("deepseek");
  });

  it("keeps preset provider ids and avoids collisions for custom providers", () => {
    expect(providerIdForNewConnection("OpenAI", "openai", false, ["openai"])).toBe("openai");
    expect(providerIdForNewConnection("DeepSeek", "custom_openai_compatible", true, ["deepseek"])).toBe("deepseek_2");
  });

  it("uses a stable fallback for non-latin custom provider names", () => {
    expect(providerIdForNewConnection("Company Account", "custom_openai_compatible", true, [])).toBe("company_account");
    expect(providerIdForNewConnection("", "custom_openai_compatible", true, [])).toBe("custom_provider");
  });
});

describe("Provider model selection", () => {
  it("removes a selected fetched model from the saved model list", () => {
    const next = toggleProviderModelSelection("gpt-4o\ncodex-auto-review\ngpt-4.1", "codex-auto-review");

    expect(next).toBe("gpt-4o\ngpt-4.1");
  });

  it("adds an unselected fetched model and keeps model ids unique", () => {
    const next = toggleProviderModelSelection("gpt-4o\ngpt-4o", "codex-auto-review");

    expect(next).toBe("gpt-4o\ncodex-auto-review");
  });
});
