import { describe, expect, it } from "vitest";

import type { ServerSession } from "../shared/types";
import { mergeSessionPages, shouldLoadNextSessionPage } from "./sessionPagination";

function session(id: string, title = id): ServerSession {
  return {
    schema_version: "session.v1",
    session_id: id,
    title,
    display_title: title,
    created_at: "2026-07-10T00:00:00Z",
    updated_at: "2026-07-10T00:00:00Z",
  };
}

describe("session pagination", () => {
  it("merges later pages without duplicating sessions", () => {
    expect(
      mergeSessionPages(
        [session("one"), session("two", "old title")],
        [session("two", "new title"), session("three")],
      ),
    ).toEqual([session("one"), session("two", "new title"), session("three")]);
  });

  it("loads only near the bottom when another page exists and no load is active", () => {
    expect(
      shouldLoadNextSessionPage({
        scrollTop: 720,
        clientHeight: 240,
        scrollHeight: 1000,
        hasMore: true,
        loading: false,
      }),
    ).toBe(true);
    expect(
      shouldLoadNextSessionPage({
        scrollTop: 200,
        clientHeight: 240,
        scrollHeight: 1000,
        hasMore: true,
        loading: false,
      }),
    ).toBe(false);
    expect(
      shouldLoadNextSessionPage({
        scrollTop: 720,
        clientHeight: 240,
        scrollHeight: 1000,
        hasMore: false,
        loading: false,
      }),
    ).toBe(false);
  });
});
