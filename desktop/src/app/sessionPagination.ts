import type { ServerSession } from "../shared/types";

export function mergeSessionPages(current: ServerSession[], incoming: ServerSession[]): ServerSession[] {
  const merged = [...current];
  const indexById = new Map(current.map((session, index) => [session.session_id, index]));
  for (const session of incoming) {
    const existingIndex = indexById.get(session.session_id);
    if (existingIndex === undefined) {
      indexById.set(session.session_id, merged.length);
      merged.push(session);
    } else {
      merged[existingIndex] = session;
    }
  }
  return merged;
}

export function shouldLoadNextSessionPage(options: {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
  hasMore: boolean;
  loading: boolean;
  threshold?: number;
}): boolean {
  if (!options.hasMore || options.loading) {
    return false;
  }
  const threshold = Math.max(0, options.threshold ?? 64);
  return options.scrollTop + options.clientHeight >= options.scrollHeight - threshold;
}
