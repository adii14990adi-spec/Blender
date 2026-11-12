#!/usr/bin/env node
/**
 * Khronos glTF Validator wrapper.
 *
 *   npm install gltf-validator
 *   node tools/gltf_validate.js outputs/<outfit>/<outfit>_Modular.glb
 *
 * Prints a single JSON line: {"errors": [...], "warnings": [...], "numErrors": N, ...}
 * Exits non-zero when the asset has validator errors.
 */
const fs = require("fs");

let validator;
try {
  validator = require("gltf-validator");
} catch (error) {
  console.log(JSON.stringify({ skipped: true, reason: "gltf-validator is not installed" }));
  process.exit(2);
}

const file = process.argv[2];
if (!file) {
  console.log(JSON.stringify({ errors: ["usage: node tools/gltf_validate.js <file.glb>"] }));
  process.exit(1);
}

const bytes = new Uint8Array(fs.readFileSync(file));

validator
  .validateBytes(bytes, { uri: file, maxIssues: 200 })
  .then((report) => {
    const messages = report.issues.messages || [];
    const errors = messages
      .filter((message) => message.severity === 0)
      .map((message) => `${message.code} ${message.message} @ ${message.pointer}`);
    const warnings = messages
      .filter((message) => message.severity === 1)
      .map((message) => `${message.code} ${message.message} @ ${message.pointer}`);
    console.log(
      JSON.stringify({
        numErrors: report.issues.numErrors,
        numWarnings: report.issues.numWarnings,
        numInfos: report.issues.numInfos,
        errors,
        warnings,
        info: report.info,
      })
    );
    process.exit(report.issues.numErrors > 0 ? 1 : 0);
  })
  .catch((error) => {
    console.log(JSON.stringify({ errors: [String(error && error.message ? error.message : error)] }));
    process.exit(1);
  });
