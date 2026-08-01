import { readFile, readdir } from "node:fs/promises";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)));
const packagePath = join(frontendDir, "package.json");
const distDir = join(frontendDir, "dist");
const packageMetadata = JSON.parse(await readFile(packagePath, "utf8"));
const version = packageMetadata.version;

if (typeof version !== "string" || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(version)) {
  throw new Error(`Invalid version in ${packagePath}`);
}

const expectedLabel = `v${version}`;
const assetFiles = await collectAssetFiles(distDir);
const renderedVersionFiles = [];

for (const assetPath of assetFiles) {
  const contents = await readFile(assetPath, "utf8");
  if (contents.includes(expectedLabel)) renderedVersionFiles.push(assetPath);
}

if (renderedVersionFiles.length === 0) {
  throw new Error(
    `Built frontend does not contain the package version label ${expectedLabel}`,
  );
}

console.log(
  `Verified built frontend version ${expectedLabel} in ${renderedVersionFiles.length} asset(s).`,
);

async function collectAssetFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await collectAssetFiles(path));
    } else if ([".html", ".js"].includes(extname(entry.name))) {
      files.push(path);
    }
  }

  return files;
}
