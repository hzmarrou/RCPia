// Removes absolute `tarball:` URLs from pnpm-lock.yaml.
//
// Installing behind a sharded registry mirror (e.g. Azure Artifacts, whose hosts
// rotate between ms-feed-N.pkgs.visualstudio.com) makes pnpm record absolute tarball
// URLs in the lockfile. Those URLs pin the lockfile to one mirror — and to one shard —
// so `pnpm install --frozen-lockfile` then fails elsewhere, including public CI.
//
// Stripping them leaves the integrity hashes intact and lets pnpm derive each tarball
// URL from whatever registry is configured at install time.
//
// Run after any `pnpm install` that adds or updates dependencies:  pnpm lock:normalize

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const lockfilePath = join(dirname(fileURLToPath(import.meta.url)), "..", "pnpm-lock.yaml");
const original = readFileSync(lockfilePath, "utf8");
const normalized = original.replace(/,\s*tarball:\s*https?:\/\/[^\s}]+\}/g, "}");

if (normalized === original) {
  console.log("pnpm-lock.yaml already normalized: no absolute tarball URLs.");
  process.exit(0);
}

const removed = (original.match(/tarball:/g) ?? []).length;
writeFileSync(lockfilePath, normalized);
console.log(`Removed ${removed} absolute tarball URL(s) from pnpm-lock.yaml.`);
