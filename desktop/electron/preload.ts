import { contextBridge, webUtils } from "electron";

function argValue(name: string): string {
  const prefix = `--${name}=`;
  const match = process.argv.find((item) => item.startsWith(prefix));
  return match ? match.slice(prefix.length) : "";
}

contextBridge.exposeInMainWorld("lucodeRuntime", {
  baseUrl: argValue("lucode-runtime-base-url"),
  token: argValue("lucode-runtime-token"),
  rendererSource: argValue("lucode-renderer-source") || "dist",
  buildTime: argValue("lucode-build-time"),
});

contextBridge.exposeInMainWorld("lucodeDesktop", {
  droppedFilePaths(files: File[] | FileList): string[] {
    return Array.from(files || [])
      .map((file) => {
        try {
          return webUtils.getPathForFile(file);
        } catch {
          return "";
        }
      })
      .filter((item) => item.length > 0);
  },
});
