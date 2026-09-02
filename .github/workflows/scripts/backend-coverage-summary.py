"""Publishes the back-end coverage report to the GitHub Actions job summary.

Expects to be run from the `backend/` working directory, with:
  - coverage.json produced by `pytest --cov --cov-report=json:coverage.json`
  - COVERAGE_THRESHOLD and GITHUB_STEP_SUMMARY set in the environment

Used by: .github/workflows/back-end-ci.yml ("Publish coverage to the job summary")
"""
import contextlib
import json
import os

threshold = float(os.environ["COVERAGE_THRESHOLD"])
with open("coverage.json") as fh:
    data = json.load(fh)

total = data["totals"]["percent_covered"]
ok = total >= threshold

summary = open(os.environ["GITHUB_STEP_SUMMARY"], "a")
with summary, contextlib.redirect_stdout(summary):
    print(f"## Back-end coverage {'✅' if ok else '⚠️'} {total:.2f}%")
    print(f"_Target: {threshold:.0f}% (informational, does not fail the build)_\n")

    rows = sorted(
        data["files"].items(),
        key=lambda kv: kv[1]["summary"]["percent_covered"],
    )
    below = [r for r in rows if r[1]["summary"]["percent_covered"] < 100]

    if not below:
        print("Every file is fully covered.")
    else:
        print("| File | Statements | Missing | Coverage |")
        print("| --- | ---: | ---: | ---: |")
        for path, info in below[:30]:
            s = info["summary"]
            print(
                f"| `{path}` | {s['num_statements']} "
                f"| {s['missing_lines']} "
                f"| {s['percent_covered']:.2f}% |"
            )
        if len(below) > 30:
            print(f"\n_{len(below) - 30} more files omitted._")

if not ok:
    print(
        f"::warning title=Back-end coverage::Coverage {total:.2f}% "
        f"is below the {threshold:.0f}% target."
    )
