export type OpenDialogResult = {
  canceled: boolean;
  filePaths: string[];
};

export type ShowOpenDialog = (options: {
  title: string;
  properties: Array<"openFile" | "openDirectory" | "multiSelections">;
  filters?: Array<{ name: string; extensions: string[] }>;
}) => Promise<OpenDialogResult>;

export type PluginSourceKind = "skill" | "mcp" | "package";
export type GetPathForFile = (file: File) => string;

export async function chooseAttachmentFiles(showOpenDialog: ShowOpenDialog): Promise<string[]> {
  const result = await showOpenDialog({
    title: "Select files to attach",
    properties: ["openFile", "multiSelections"],
  });
  return result.canceled
    ? []
    : result.filePaths.map((item) => String(item || "").trim()).filter(Boolean);
}

export async function choosePluginSource(showOpenDialog: ShowOpenDialog, kind: PluginSourceKind = "skill"): Promise<string> {
  const options = sourcePickerOptions(kind);
  const result = await showOpenDialog({
    ...options,
  });
  return result.canceled ? "" : String(result.filePaths[0] || "");
}

export function resolveDroppedFilePath(file: File, getPathForFile: GetPathForFile): string {
  try {
    return String(getPathForFile(file) || "").trim();
  } catch {
    return "";
  }
}

function sourcePickerOptions(kind: PluginSourceKind): Parameters<ShowOpenDialog>[0] {
  if (kind === "mcp") {
    return {
      title: "Select Lucode MCP source",
      properties: ["openFile", "openDirectory"],
      filters: [{ name: "MCP sources", extensions: ["json"] }],
    };
  }
  if (kind === "package") {
    return {
      title: "Select Lucode plugin package",
      properties: ["openFile", "openDirectory"],
      filters: [{ name: "Lucode packages", extensions: ["zip"] }],
    };
  }
  return {
    title: "Select Lucode plugin source",
    properties: ["openFile", "openDirectory"],
    filters: [{ name: "Lucode sources", extensions: ["zip", "md"] }],
  };
}
