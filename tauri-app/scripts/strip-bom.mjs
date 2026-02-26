#!/usr/bin/env node
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

const ignoreDirs = new Set([
  "node_modules",
  "dist",
  "target",
  ".git"
]);

const configNamePatterns = [
  "postcss.config.",
  "vite.config.",
  "tailwind.config."
];

const exactNames = new Set([
  "package.json"
]);

function shouldIgnoreDir(rel) {
  const parts = rel.split(path.sep);
  for (const p of parts) {
    if (ignoreDirs.has(p)) return true;
  }
  // Only ignore src-tauri/target, but not the whole src-tauri directory.
  for (let i = 0; i < parts.length - 1; i++) {
    if (parts[i] === "src-tauri" && parts[i + 1] === "target") {
      return true;
    }
  }
  return false;
}

function isCandidate(rel, name) {
  if (exactNames.has(name)) return true;
  if (name.endsWith(".json")) return true;
  if (name.startsWith(".") && name.endsWith("rc")) return true;
  for (const prefix of configNamePatterns) {
    if (name.startsWith(prefix)) return true;
  }
  if (rel.startsWith(`src${path.sep}`) && name.endsWith(".json")) return true;
  return false;
}

function stripBom(buf) {
  if (buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf) {
    return buf.subarray(3);
  }
  return buf;
}

function hasBomChar(s) {
  return s.length > 0 && s.charCodeAt(0) === 0xfeff;
}

let fixedCount = 0;

function walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const ent of entries) {
    const full = path.join(dir, ent.name);
    const rel = path.relative(root, full);
    if (ent.isDirectory()) {
      if (shouldIgnoreDir(rel)) continue;
      walk(full);
      continue;
    }
    if (!ent.isFile()) continue;

    if (!isCandidate(rel, ent.name)) continue;

    const buf = fs.readFileSync(full);
    const stripped = stripBom(buf);
    let changed = stripped !== buf;

    let text = stripped.toString("utf8");
    if (hasBomChar(text)) {
      text = text.replace(/^\uFEFF/, "");
      changed = true;
    }

    if (changed) {
      fs.writeFileSync(full, text, { encoding: "utf8" });
      console.log(`Fixed BOM: ${rel}`);
      fixedCount += 1;
    }
  }
}

if (!fs.existsSync(root)) {
  console.error(`tauri-app not found at: ${root}`);
  process.exit(1);
}

walk(root);
console.log(`Total fixed: ${fixedCount}`);
