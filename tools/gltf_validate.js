#!/usr/bin/env node
/**
 * Khronos glTF Validator wrapper (node).
 *
 *   npm run validate:gltf -- docs/manifest.json
 *   node tools/gltf_validate.js --manifest docs/manifest.json
 *   node tools/gltf_validate.js docs/outputs/male/jacket_urban_01/*.glb
 *
 * With `--manifest`, every GLB referenced by the deployed `manifest.json` is
 * validated, so the check covers exactly what the web studio loads.
 *
 * Prints one JSON line per file plus a summary; exits non-zero when any asset
 * has validator errors (exit 2 when the validator is not installed).
 */
const fs = require("fs");
const path = require("path");

let validator;
try {
  validator = require("gltf-validator");
} catch (error) {
  console.log(JSON.stringify({ skipped: true, reason: "gltf-validator is not installed" }));
  process.exit(2);
}

function collectTargets(argv) {
  const files = [];
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--manifest") {
      const manifestPath = argv[index + 1];
      if (!manifestPath) {
        throw new Error("--manifest needs a path");
      }
      const root = path.dirname(manifestPath);
      const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
      for (const outfit of manifest.outfits || []) {
        for (const [variant, entry] of Object.entries(outfit.variants || {})) {
          for (const key of ["glb", "fbx"]) {
            const item = entry[key];
            if (key !== "glb" || !item || !item.path) {
              continue;
            }
            files.push({ label: `${outfit.id}/${variant}`, file: path.join(root, item.path) });
          }
        }
      }
      index += 1;
    } else {
      files.push({ label: null, file: arg });
    }
  }
  return files;
}

async function validateOne({ label, file }) {
  if (!fs.existsSync(file)) {
    return { file, label, missing: true, numErrors: 1, errors: ["file not found"] };
  }
  const bytes = new Uint8Array(fs.readFileSync(file));
  const report = await validator.validateBytes(bytes, { uri: file, maxIssues: 200 });
  const messages = report.issues.messages || [];
  const errors = messages
    .filter((message) => message.severity === 0)
    .map((message) => `${message.code} ${message.message} @ ${message.pointer}`);
  const warnings = messages
    .filter((message) => message.severity === 1)
    .map((message) => `${message.code} ${message.message} @ ${message.pointer}`);
  return {
    file,
    label,
    numErrors: report.issues.numErrors,
    numWarnings: report.issues.numWarnings,
    numInfos: report.issues.numInfos,
    errors,
    warnings,
    info: report.info,
  };
}

async function main() {
  const targets = collectTargets(process.argv.slice(2));
  if (targets.length === 0) {
    console.log(
      JSON.stringify({
        errors: ["usage: node tools/gltf_validate.js [--manifest manifest.json] <file.glb>..."],
      })
    );
    process.exit(1);
  }

  let failed = 0;
  let totalWarnings = 0;
  for (const target of targets) {
    let result;
    try {
      result = await validateOne(target);
    } catch (error) {
      result = {
        file: target.file,
        label: target.label,
        numErrors: 1,
        errors: [String(error && error.message ? error.message : error)],
      };
    }
    console.log(JSON.stringify(result));
    if (result.numErrors > 0) {
      failed += 1;
    }
    totalWarnings += result.numWarnings || 0;
  }

  const summary = {
    validated: targets.length,
    failed,
    warnings: totalWarnings,
  };
  // Per-file results own stdout (callers parse them one JSON object per line);
  // the roll-up goes to stderr so single-file output stays a single object.
  console.error(JSON.stringify({ summary }));
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((error) => {
  console.log(JSON.stringify({ errors: [String(error)] }));
  process.exit(1);
});
