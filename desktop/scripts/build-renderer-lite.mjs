import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const srcRoot = path.join(desktopRoot, "src");
const distRoot = path.join(desktopRoot, "dist");
const assetsRoot = path.join(distRoot, "assets");
const outFile = path.join(assetsRoot, "index-lite.js");
const cssOutFile = path.join(assetsRoot, "index-lite.css");
const viteDepsRoot = path.join(desktopRoot, "node_modules", ".vite", "deps");

const moduleBySource = new Map();
const modules = [];

function normalizeId(filePath) {
  return path.relative(srcRoot, filePath).replace(/\\/g, "/").replace(/\.(tsx?|jsx?)$/, "");
}

function resolveSource(fromFile, specifier) {
  if (!specifier.startsWith(".")) {
    return null;
  }
  const base = path.resolve(path.dirname(fromFile), specifier);
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    path.join(base, "index.tsx"),
    path.join(base, "index.ts"),
    path.join(base, "index.jsx"),
    path.join(base, "index.js"),
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
  if (!found) {
    throw new Error(`Cannot resolve ${specifier} from ${fromFile}`);
  }
  return found;
}

function transformSource(filePath) {
  const source = fs.readFileSync(filePath, "utf8");
  return ts.transpileModule(source, {
    fileName: filePath,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
      importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
      preserveValueImports: false,
      sourceMap: false,
    },
  }).outputText;
}

function rewriteImports(filePath, code) {
  return code
    .replace(/import\s+([^'"]+)\s+from\s+["']([^"']+)["'];?/g, (_match, bindings, specifier) => {
      if (specifier === "react") {
        return importReplacement(bindings, "react");
      }
      if (specifier === "react-dom/client") {
        return importReplacement(bindings, "react-dom/client");
      }
      if (specifier === "react/jsx-runtime") {
        return importReplacement(bindings, "react/jsx-runtime");
      }
      if (specifier === "@xterm/xterm") {
        return importReplacement(bindings, "@xterm/xterm");
      }
      if (specifier === "@xterm/addon-fit") {
        return importReplacement(bindings, "@xterm/addon-fit");
      }
      if (specifier.endsWith(".css")) {
        return "";
      }
      const resolved = resolveSource(filePath, specifier);
      if (resolved) {
        const moduleId = collectModule(resolved);
        return importReplacement(bindings, moduleId);
      }
      throw new Error(`Unsupported import ${specifier} in ${filePath}`);
    })
    .replace(/import\s+["']([^"']+\.css)["'];?/g, "")
    .replace(/export\s+function\s+([A-Za-z0-9_]+)/g, "exports.$1 = function $1")
    .replace(/export\s+class\s+([A-Za-z0-9_]+)/g, "exports.$1 = class $1")
    .replace(/export\s+const\s+([A-Za-z0-9_]+)\s*=/g, "exports.$1 =")
    .replace(/export\s+let\s+([A-Za-z0-9_]+)\s*=/g, "exports.$1 =")
    .replace(/export\s+var\s+([A-Za-z0-9_]+)\s*=/g, "exports.$1 =")
    .replace(/export\s*\{([^}]+)\};?/g, (_match, names) =>
      names
        .split(",")
        .map((raw) => raw.trim())
        .filter(Boolean)
        .map((raw) => {
          const [local, exported = local] = raw.split(/\s+as\s+/).map((part) => part.trim());
          return `exports.${exported} = ${local};`;
        })
        .join("\n"),
    );
}

function importReplacement(bindings, moduleId) {
  const clean = bindings.trim();
  if (clean.startsWith("{") && clean.endsWith("}")) {
    return `const ${namedImportBindings(clean)} = __require(${JSON.stringify(moduleId)});`;
  }
  if (clean.startsWith("* as ")) {
    return `const ${clean.slice(5).trim()} = __require(${JSON.stringify(moduleId)});`;
  }
  return `const ${clean} = __require(${JSON.stringify(moduleId)});`;
}

function namedImportBindings(bindings) {
  const inner = bindings.slice(1, -1).trim();
  if (!inner) {
    return "{}";
  }
  const rewritten = inner
    .split(",")
    .map((raw) => raw.trim())
    .filter(Boolean)
    .map((raw) => raw.replace(/\s+as\s+/g, ": "))
    .join(", ");
  return `{ ${rewritten} }`;
}

function collectModule(filePath) {
  const normalizedPath = path.resolve(filePath);
  const existing = moduleBySource.get(normalizedPath);
  if (existing) {
    return existing;
  }
  const id = normalizeId(normalizedPath);
  moduleBySource.set(normalizedPath, id);
  const transformed = rewriteImports(normalizedPath, transformSource(normalizedPath));
  modules.push({ id, code: transformed });
  return id;
}

function runtimeBundle() {
  const modulesText = modules
    .map(
      (module) => `${JSON.stringify(module.id)}: function(exports, __require) {\n${module.code}\n}`,
    )
    .join(",\n");
  return `import React from "./react.js";
import * as ReactDOMClient from "./react-dom_client.js";
import * as ReactJSXRuntime from "./react_jsx-runtime.js";
import * as Xterm from "./xterm.mjs";
import * as XtermFit from "./addon-fit.mjs";

(() => {
const __external = {
  "react": () => React,
  "react-dom/client": () => ReactDOMClient,
  "react/jsx-runtime": () => ReactJSXRuntime,
  "@xterm/xterm": () => Xterm,
  "@xterm/addon-fit": () => XtermFit
};
const __modules = {
${modulesText}
};
const __cache = {};
function __require(id) {
  if (__external[id]) return __external[id]();
  if (__cache[id]) return __cache[id].exports;
  const factory = __modules[id];
  if (!factory) throw new Error("Module not found: " + id);
  const module = { exports: {} };
  __cache[id] = module;
  factory(module.exports, __require);
  return module.exports;
}
__require("main");
})();`;
}

function copyVendor() {
  const vendor = [
    ["react.js", "react.js"],
    ["react-dom_client.js", "react-dom_client.js"],
    ["react_jsx-runtime.js", "react_jsx-runtime.js"],
    ["chunk-HLV3KO6Q.js", "chunk-HLV3KO6Q.js"],
    ["chunk-VO5SRJFT.js", "chunk-VO5SRJFT.js"],
  ];
  for (const [source, target] of vendor) {
    const sourcePath = path.join(viteDepsRoot, source);
    if (!fs.existsSync(sourcePath)) {
      throw new Error(`Missing Vite prebuilt dependency: ${sourcePath}`);
    }
    fs.copyFileSync(sourcePath, path.join(assetsRoot, target));
  }
  const packageVendor = [
    [path.join(desktopRoot, "node_modules", "@xterm", "xterm", "lib", "xterm.mjs"), "xterm.mjs"],
    [path.join(desktopRoot, "node_modules", "@xterm", "addon-fit", "lib", "addon-fit.mjs"), "addon-fit.mjs"],
  ];
  for (const [sourcePath, target] of packageVendor) {
    if (!fs.existsSync(sourcePath)) {
      throw new Error(`Missing package dependency: ${sourcePath}`);
    }
    fs.copyFileSync(sourcePath, path.join(assetsRoot, target));
  }
}

fs.mkdirSync(assetsRoot, { recursive: true });
copyVendor();
collectModule(path.join(srcRoot, "main.tsx"));
fs.writeFileSync(outFile, runtimeBundle(), "utf8");
fs.writeFileSync(
  cssOutFile,
  `${fs.readFileSync(path.join(srcRoot, "styles.css"), "utf8")}\n\n${fs.readFileSync(
    path.join(desktopRoot, "node_modules", "@xterm", "xterm", "css", "xterm.css"),
    "utf8",
  )}`,
  "utf8",
);
fs.writeFileSync(
  path.join(distRoot, "index.html"),
  `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Lucode</title>
    <link rel="stylesheet" href="./assets/index-lite.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="./assets/index-lite.js"></script>
  </body>
</html>
`,
  "utf8",
);

console.log(`Renderer lite build written to ${distRoot}`);
