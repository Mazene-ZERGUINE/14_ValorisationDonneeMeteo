// Publishes the front-end coverage report to the GitHub Actions job summary.
//
// Expects to be run from the `frontend/` working directory, with:
//   - coverage/coverage-summary.json produced by `npm run test:ci:coverage`
//   - COVERAGE_THRESHOLD and GITHUB_STEP_SUMMARY set in the environment
//
// Used by: .github/workflows/frontend-ci.yml ("Publish coverage to the job summary")
const fs = require("fs");
const path = require("path");

const THRESHOLD = Number(process.env.COVERAGE_THRESHOLD);
const data = JSON.parse(
  fs.readFileSync("coverage/coverage-summary.json", "utf8")
);

const total = data.total.lines.pct;
const ok = total >= THRESHOLD;
const out = [];

out.push(`## Front-end coverage ${ok ? "✅" : "⚠️"} ${total.toFixed(2)}%`);
out.push(`_Target: ${THRESHOLD}% of lines (informational, does not fail the build)_\n`);
out.push("| Metric | Covered | Total | % |");
out.push("| --- | ---: | ---: | ---: |");
for (const metric of ["statements", "branches", "functions", "lines"]) {
  const m = data.total[metric];
  out.push(`| ${metric} | ${m.covered} | ${m.total} | ${m.pct.toFixed(2)}% |`);
}

const root = process.cwd() + path.sep;
const files = Object.entries(data)
  .filter(([key]) => key !== "total")
  .map(([key, value]) => [
    key.startsWith(root) ? key.slice(root.length) : key,
    value,
  ])
  .filter(([, value]) => value.lines.pct < 100)
  .sort((a, b) => a[1].lines.pct - b[1].lines.pct);

if (files.length === 0) {
  out.push("\nEvery file is fully covered.");
} else {
  out.push("\n<details><summary>Files below 100%</summary>\n");
  out.push("| File | Stmts | Branch | Funcs | Lines |");
  out.push("| --- | ---: | ---: | ---: | ---: |");
  for (const [name, v] of files.slice(0, 30)) {
    out.push(
      `| \`${name}\` | ${v.statements.pct.toFixed(0)}% ` +
        `| ${v.branches.pct.toFixed(0)}% ` +
        `| ${v.functions.pct.toFixed(0)}% ` +
        `| ${v.lines.pct.toFixed(2)}% |`
    );
  }
  if (files.length > 30) {
    out.push(`\n_${files.length - 30} more files omitted._`);
  }
  out.push("\n</details>");
}

fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, out.join("\n") + "\n");

if (!ok) {
  console.log(
    `::warning title=Front-end coverage::Coverage ${total.toFixed(2)}% ` +
      `is below the ${THRESHOLD}% target.`
  );
}
