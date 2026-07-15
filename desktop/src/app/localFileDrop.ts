export function resolveDroppedFilePaths(
  files: FileList | File[],
  resolveDesktopPath: (file: File) => string,
): string[] {
  return Array.from(files || [])
    .map((file) => {
      const desktopPath = String(resolveDesktopPath(file) || "").trim();
      if (desktopPath) {
        return desktopPath;
      }
      return String((file as File & { path?: string }).path || "").trim();
    })
    .filter(Boolean);
}
