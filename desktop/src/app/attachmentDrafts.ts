export const MAX_ATTACHMENT_DRAFTS = 8;

export type AttachmentDraft = {
  id: string;
  path: string;
  name: string;
};

export type AttachmentDraftMergeResult = {
  drafts: AttachmentDraft[];
  rejectedCount: number;
};

export function mergeAttachmentDrafts(
  current: AttachmentDraft[],
  paths: string[],
): AttachmentDraftMergeResult {
  const drafts = [...current];
  const seen = new Set(current.map((item) => normalizedPath(item.path)));
  let rejectedCount = 0;
  for (const rawPath of paths) {
    const path = String(rawPath || "").trim();
    const key = normalizedPath(path);
    if (!path || seen.has(key)) {
      continue;
    }
    if (drafts.length >= MAX_ATTACHMENT_DRAFTS) {
      rejectedCount += 1;
      continue;
    }
    seen.add(key);
    drafts.push({
      id: key,
      path,
      name: attachmentName(path),
    });
  }
  return { drafts, rejectedCount };
}

export function removeAttachmentDraft(drafts: AttachmentDraft[], id: string): AttachmentDraft[] {
  return drafts.filter((item) => item.id !== id);
}

function attachmentName(path: string): string {
  const parts = String(path || "").split(/[\\/]+/);
  return parts[parts.length - 1] || "attachment";
}

function normalizedPath(path: string): string {
  return String(path || "").trim().replace(/\\/g, "/").toLocaleLowerCase();
}
