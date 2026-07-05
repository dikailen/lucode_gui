import { randomBytes } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

import type {
  BrowserAutomationApi,
  BrowserPageSummaryOptions,
  BrowserWorkspaceState,
} from "./browserManager.js";

export type DesktopBrowserBridgeHandle = {
  baseUrl: string;
  token: string;
};

export type DesktopBrowserBridgeOptions = {
  host?: string;
  port?: number;
  token?: string;
};

type JsonObject = Record<string, unknown>;

export class DesktopBrowserBridge {
  private readonly browser: BrowserAutomationApi;
  private readonly host: string;
  private readonly port: number;
  private readonly token: string;
  private server: ReturnType<typeof createServer> | null = null;
  private handle: DesktopBrowserBridgeHandle | null = null;

  constructor(browser: BrowserAutomationApi, options: DesktopBrowserBridgeOptions = {}) {
    this.browser = browser;
    this.host = options.host || "127.0.0.1";
    this.port = Number.isFinite(options.port) ? Number(options.port) : 0;
    this.token = String(options.token || randomBytes(24).toString("hex"));
  }

  async start(): Promise<DesktopBrowserBridgeHandle> {
    if (this.handle) {
      return this.handle;
    }

    this.server = createServer((request, response) => {
      void this.handleRequest(request, response);
    });

    await new Promise<void>((resolve, reject) => {
      this.server?.once("error", reject);
      this.server?.listen(this.port, this.host, () => resolve());
    });

    const address = this.server.address();
    if (!address || typeof address === "string") {
      throw new Error("Desktop browser bridge failed to bind a local address.");
    }

    this.handle = {
      baseUrl: `http://${this.host}:${(address as AddressInfo).port}`,
      token: this.token,
    };
    return this.handle;
  }

  async dispose(): Promise<void> {
    this.handle = null;
    if (!this.server) {
      return;
    }
    const server = this.server;
    this.server = null;
    await new Promise<void>((resolve) => {
      server.close(() => resolve());
    });
  }

  private async handleRequest(request: IncomingMessage, response: ServerResponse): Promise<void> {
    try {
      const path = this.requestPath(request);
      if (request.method === "GET" && path === "/health") {
        this.sendJson(response, 200, { ok: true });
        return;
      }
      if (!this.authorized(request)) {
        this.sendJson(response, 401, { error: "Unauthorized desktop browser bridge request." });
        return;
      }

      if (request.method === "GET" && path === "/tabs") {
        this.sendJson(response, 200, this.browser.listTabsForAutomation());
        return;
      }

      const body = await this.readJsonBody(request);

      if (request.method === "POST" && path === "/navigate") {
        this.sendJson(response, 200, await this.browser.navigateForAutomation(this.requireString(body.url, "url"), {
          tabId: this.optionalString(body.tab_id),
        }));
        return;
      }

      if (request.method === "POST" && path === "/page-summary") {
        const options: BrowserPageSummaryOptions & { tabId?: string } = {
          tabId: this.optionalString(body.tab_id),
          maxTextLength: this.optionalNumber(body.max_text_length),
          maxElements: this.optionalNumber(body.max_elements),
        };
        this.sendJson(response, 200, await this.browser.getPageSummaryForAutomation(options));
        return;
      }

      if (request.method === "POST" && path === "/automation-permission") {
        this.sendJson(
          response,
          200,
          this.browser.setAutomationPermissionForAutomation(body.enabled === true, {
            tabId: this.optionalString(body.tab_id),
          }),
        );
        return;
      }

      if (request.method === "POST" && path === "/click") {
        this.sendJson(
          response,
          200,
          await this.browser.clickElementForAutomation(this.requireString(body.selector, "selector"), {
            tabId: this.optionalString(body.tab_id),
          }),
        );
        return;
      }

      if (request.method === "POST" && path === "/set-input-value") {
        this.sendJson(
          response,
          200,
          await this.browser.setInputValueForAutomation(
            this.requireString(body.selector, "selector"),
            this.requireString(body.value, "value"),
            { tabId: this.optionalString(body.tab_id) },
          ),
        );
        return;
      }

      if (request.method === "POST" && path === "/submit-form") {
        this.sendJson(
          response,
          200,
          await this.browser.submitFormForAutomation(this.requireString(body.selector, "selector"), {
            tabId: this.optionalString(body.tab_id),
          }),
        );
        return;
      }

      this.sendJson(response, 404, { error: `Unsupported desktop browser bridge route: ${request.method} ${path}` });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.sendJson(response, 400, { error: message });
    }
  }

  private requestPath(request: IncomingMessage): string {
    const raw = request.url || "/";
    return raw.split("?")[0] || "/";
  }

  private authorized(request: IncomingMessage): boolean {
    const auth = String(request.headers.authorization || "");
    return auth === `Bearer ${this.token}`;
  }

  private async readJsonBody(request: IncomingMessage): Promise<JsonObject> {
    const chunks: Buffer[] = [];
    for await (const chunk of request) {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    const raw = Buffer.concat(chunks).toString("utf-8").trim();
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Desktop browser bridge request body must be a JSON object.");
    }
    return parsed as JsonObject;
  }

  private requireString(value: unknown, field: string): string {
    const normalized = String(value ?? "").trim();
    if (!normalized) {
      throw new Error(`Desktop browser bridge requires "${field}".`);
    }
    return normalized;
  }

  private optionalString(value: unknown): string | undefined {
    const normalized = String(value ?? "").trim();
    return normalized || undefined;
  }

  private optionalNumber(value: unknown): number | undefined {
    if (value === undefined || value === null || value === "") {
      return undefined;
    }
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : undefined;
  }

  private sendJson(response: ServerResponse, status: number, payload: JsonObject | BrowserWorkspaceState | unknown): void {
    const body = JSON.stringify(payload ?? {});
    response.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": Buffer.byteLength(body),
      "Cache-Control": "no-store",
    });
    response.end(body);
  }
}
