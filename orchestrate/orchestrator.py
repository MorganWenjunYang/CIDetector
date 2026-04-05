#!/usr/bin/env python3
"""CIDector Orchestrator — automated benchmark → issue → fix → PR loop.

Usage:
    python scripts/orchestrator.py benchmark   # run benchmarks, create issues on failure
    python scripts/orchestrator.py fix         # pick up open issues, claude-fix in worktrees
    python scripts/orchestrator.py loop        # full cycle: benchmark then fix
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
LOGS_DIR = PROJECT_ROOT / "logs"
WORKTREES_DIR = PROJECT_ROOT / ".worktrees"
BENCHMARK_RUNNER = PROJECT_ROOT / "benchmarks" / "run_benchmarks.py"

ISSUE_LABEL = "claude-fix"
BENCHMARK_LABEL = "benchmark-failure"
CLAUDE_MAX_TURNS = 40
CLAUDE_MAX_BUDGET_USD = 5
MAX_FIX_ATTEMPTS = 10

_config: dict = {"max_issues": 5, "max_fix_attempts": MAX_FIX_ATTEMPTS}

logger = logging.getLogger("orchestrator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"orchestrator_{date_str}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def _run(cmd: list[str], *, cwd: str | Path | None = None,
         check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with logging."""
    logger.debug("$ %s (cwd=%s)", " ".join(cmd), cwd or ".")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        logger.debug("stderr: %s", (result.stderr or "").strip()[:500])
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def _gh_available() -> bool:
    try:
        _run(["gh", "auth", "status"], check=False)
        return True
    except FileNotFoundError:
        return False


def _ensure_labels_exist() -> None:
    """Create the required labels if they don't already exist in the repo."""
    labels = {
        ISSUE_LABEL: {"description": "Auto-fix target for Claude", "color": "d876e3"},
        BENCHMARK_LABEL: {"description": "Benchmark test failure", "color": "e11d48"},
    }
    for name, meta in labels.items():
        result = _run(
            ["gh", "label", "list", "--search", name, "--json", "name", "--limit", "1"],
            check=False,
        )
        already_exists = False
        if result.returncode == 0:
            try:
                found = json.loads(result.stdout)
                already_exists = any(l.get("name") == name for l in found)
            except json.JSONDecodeError:
                pass
        if not already_exists:
            _run(
                ["gh", "label", "create", name,
                 "--description", meta["description"],
                 "--color", meta["color"]],
                check=False,
            )
            logger.info("Created label: %s", name)


def _claude_available() -> bool:
    return shutil.which("claude") is not None


def _save_benchmark_report(report: dict) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = LOGS_DIR / f"benchmark_{date_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("Benchmark report saved to %s", path)
    return path


# ---------------------------------------------------------------------------
# Issue selection & formatting
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"FAIL": 0, "WARN": 1}


def _pick_most_critical(report: dict) -> dict | None:
    """Pick the single most critical non-passing result from the report.

    Priority: FAIL before WARN, then by duration (slower = more impactful).
    """
    candidates = [
        r for r in report["results"]
        if r["status"] in _SEVERITY_ORDER
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (
        _SEVERITY_ORDER.get(r["status"], 99),
        -r.get("duration_sec", 0),
    ))
    return candidates[0]


def _build_issue_title(case: dict, date_str: str) -> str:
    from orchestrate.prompts.issue_template import build_issue_title
    return build_issue_title(case, date_str)


def _build_issue_body(case: dict, report: dict) -> str:
    """Build a structured, actionable issue body for a benchmark failure."""
    from orchestrate.prompts.issue_template import build_issue_body
    return build_issue_body(case, report, _load_benchmark_cases())


def _load_benchmark_cases() -> list[dict]:
    cases_path = PROJECT_ROOT / "benchmarks" / "benchmark_cases.yaml"
    if not cases_path.exists():
        return []
    import yaml
    with open(cases_path) as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


def _find_duplicate_issue(case_name: str) -> int | None:
    """Check if an open issue already covers this specific case."""
    result = _run(
        ["gh", "issue", "list", "--label", ISSUE_LABEL,
         "--state", "open", "--json", "number,title", "--limit", "50"],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for iss in issues:
        if case_name in iss.get("title", ""):
            return iss["number"]
    return None


# ---------------------------------------------------------------------------
# Fix verification helpers
# ---------------------------------------------------------------------------

def _extract_case_name(title: str) -> str | None:
    """Extract benchmark case name from issue title.

    Title format produced by issue_template:
        [Benchmark][FAIL] ClinicalTrials.gov — http_403_forbidden (2026-04-04)
    """
    m = re.search(r'\]\s*(.+?)\s*—', title)
    return m.group(1).strip() if m else None


def _verify_fix(worktree_path: Path, case_name: str | None) -> bool:
    """Run benchmarks in the worktree and check whether the target case passes.

    If *case_name* is provided, runs only that case (via --filter) and checks
    its individual status.  Falls back to the full suite when case_name is
    None — passes only when there are zero FAILs.
    """
    runner = worktree_path / "benchmarks" / "run_benchmarks.py"
    cmd = [sys.executable, str(runner), "--verbose"]
    if case_name:
        cmd += ["--filter", case_name]

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=180,
        cwd=str(worktree_path),
    )
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse benchmark output during verification")
        return False

    if case_name:
        for r in report.get("results", []):
            if r["name"] == case_name:
                return r["status"] == "PASS"
        logger.warning("Case '%s' not found in benchmark results", case_name)
        return False

    return report.get("failed", 1) == 0


def _extract_attempt_summary(claude_output: str, max_len: int = 1500) -> str:
    """Return a truncated summary from Claude's output (tail, since conclusions
    are usually at the end)."""
    if not claude_output:
        return "(no output)"
    if len(claude_output) <= max_len:
        return claude_output
    return "…" + claude_output[-max_len:]


# ---------------------------------------------------------------------------
# Sub-command: benchmark
# ---------------------------------------------------------------------------

def cmd_benchmark() -> dict:
    """Run benchmarks; pick the most critical failure and create a focused GitHub issue."""
    logger.info("=== Running benchmarks ===")

    result = subprocess.run(
        [sys.executable, str(BENCHMARK_RUNNER), "--verbose"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.error("Failed to parse benchmark output as JSON")
        logger.error("stdout: %s", result.stdout[:500])
        logger.error("stderr: %s", result.stderr[:500])
        sys.exit(1)

    _save_benchmark_report(report)

    passed = report.get("passed", 0)
    failed = report.get("failed", 0)
    warned = report.get("warned", 0)
    skipped = report.get("skipped", 0)
    logger.info("Results: %d passed, %d failed, %d warned, %d skipped",
                passed, failed, warned, skipped)

    worst = _pick_most_critical(report)
    if worst is None:
        logger.info("All benchmarks passed — no issue needed.")
        return report

    logger.info("Most critical issue: %s (%s)", worst["name"], worst["status"])

    if not _gh_available():
        logger.warning("gh CLI not available — skipping issue creation")
        return report

    _ensure_labels_exist()

    dup = _find_duplicate_issue(worst["name"])
    if dup:
        logger.info("Open issue #%d already covers '%s' — skipping creation",
                     dup, worst["name"])
        return report

    date_str = report.get("timestamp", "")[:10]
    title = _build_issue_title(worst, date_str)
    body = _build_issue_body(worst, report)

    try:
        _run([
            "gh", "issue", "create",
            "--title", title,
            "--body", body,
            "--label", f"{ISSUE_LABEL},{BENCHMARK_LABEL}",
        ])
        logger.info("Created issue: %s", title)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to create issue: %s", e.stderr)

    return report


# ---------------------------------------------------------------------------
# Sub-command: fix
# ---------------------------------------------------------------------------

def _get_open_issues() -> list[dict]:
    result = _run(
        ["gh", "issue", "list",
         "--label", ISSUE_LABEL,
         "--state", "open",
         "--json", "number,title,body",
         "--limit", str(_config["max_issues"])],
        check=False,
    )
    if result.returncode != 0:
        logger.error("Failed to list issues: %s", result.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.error("Invalid JSON from gh issue list")
        return []


def _branch_exists_remote(branch: str) -> bool:
    result = _run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=str(PROJECT_ROOT), check=False,
    )
    return bool(result.stdout.strip())


def _fix_issue(issue: dict) -> bool:
    """Attempt to fix a single issue with up to *max_fix_attempts* retries.

    Possible outcomes
    -----------------
    1. **Resolved, no code changes** — benchmark passes without any diff.
       → Comment findings on issue, close it.
    2. **Resolved, has code changes** — benchmark passes after Claude edits.
       → Commit, push branch, create PR (which auto-closes the issue).
    3. **Unresolved after N attempts** — benchmark still fails.
       → Comment failure summary on issue, leave it open for human review.

    Returns True when the issue was resolved (scenarios 1 or 2).
    """
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body", "")
    branch = f"fix/issue-{number}"
    worktree_path = WORKTREES_DIR / branch.replace("/", "-")
    max_attempts = _config["max_fix_attempts"]

    logger.info("--- Fixing issue #%d: %s ---", number, title)

    if _branch_exists_remote(branch):
        logger.info("Branch %s already exists on remote — skipping (PR may already exist)", branch)
        return False

    # Clean up stale worktree if present
    if worktree_path.exists():
        logger.info("Removing stale worktree at %s", worktree_path)
        _run(["git", "worktree", "remove", "--force", str(worktree_path)],
             cwd=str(PROJECT_ROOT), check=False)

    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)

    try:
        _run(["git", "worktree", "add", str(worktree_path), "-b", branch],
             cwd=str(PROJECT_ROOT))
    except subprocess.CalledProcessError as e:
        logger.error("Failed to create worktree: %s", e.stderr)
        try:
            _run(["git", "branch", "-D", branch], cwd=str(PROJECT_ROOT), check=False)
            _run(["git", "worktree", "add", str(worktree_path), "-b", branch],
                 cwd=str(PROJECT_ROOT))
        except subprocess.CalledProcessError:
            logger.error("Could not create worktree for branch %s", branch)
            return False

    # Record base commit — used to reset the worktree between retry attempts
    base_result = _run(["git", "rev-parse", "HEAD"], cwd=str(worktree_path), check=False)
    base_ref = base_result.stdout.strip() if base_result.returncode == 0 else "main"

    case_name = _extract_case_name(title)
    attempt_history: list[dict] = []

    try:
        for attempt in range(1, max_attempts + 1):
            logger.info("Attempt %d/%d for issue #%d", attempt, max_attempts, number)

            # Reset worktree to pristine state before each retry
            if attempt > 1:
                _run(["git", "reset", "--hard", base_ref],
                     cwd=str(worktree_path), check=False)
                _run(["git", "clean", "-fd"],
                     cwd=str(worktree_path), check=False)

            # ----- Run Claude -----
            from orchestrate.prompts.fix_prompt import build_fix_prompt
            prompt = build_fix_prompt(
                number, title, body,
                attempt=attempt,
                max_attempts=max_attempts,
                previous_attempts=attempt_history if attempt > 1 else None,
            )

            logger.info("Launching Claude CLI (attempt %d) in worktree %s",
                         attempt, worktree_path)
            claude_result = _run(
                [
                    "claude", "-p", prompt,
                    "--allowedTools", "Read,Edit,Write,Bash",
                    "--max-turns", str(CLAUDE_MAX_TURNS),
                ],
                cwd=str(worktree_path),
                check=False,
            )
            claude_output = (claude_result.stdout or "").strip()

            if claude_result.returncode != 0:
                logger.warning("Claude exited with code %d (attempt %d)",
                               claude_result.returncode, attempt)

            # ----- Stage changes (but don't commit yet) -----
            _run(["git", "add", "-A"], cwd=str(worktree_path), check=False)
            diff_result = _run(["git", "diff", "--cached", "--quiet"],
                               cwd=str(worktree_path), check=False)
            has_staged = diff_result.returncode != 0
            has_claude_commits = _branch_has_new_commits(worktree_path)
            has_code_changes = has_staged or has_claude_commits

            # ----- Verify BEFORE committing -----
            logger.info("Verifying fix with benchmark (case=%s) …", case_name or "ALL")
            benchmark_passed = _verify_fix(worktree_path, case_name)

            # ----- Scenario 2: resolved with code → commit now -----
            if benchmark_passed and has_code_changes:
                if has_staged:
                    _run(
                        ["git", "commit", "-m",
                         f"fix: resolve issue #{number} — {title}"],
                        cwd=str(worktree_path), check=False,
                    )
                logger.info("Issue #%d resolved with code changes (attempt %d)",
                             number, attempt)
                return _push_and_create_pr(worktree_path, branch, number, title)

            # ----- Scenario 1: resolved without code -----
            if benchmark_passed and not has_code_changes:
                logger.info("Issue #%d resolved without code changes (attempt %d)",
                             number, attempt)
                _comment_and_close_issue(number, claude_output)
                return True

            # ----- Not resolved — record and retry -----
            attempt_history.append({
                "attempt": attempt,
                "had_code_changes": has_code_changes,
                "summary": _extract_attempt_summary(claude_output),
            })
            logger.warning("Attempt %d/%d failed for issue #%d — benchmark still failing",
                           attempt, max_attempts, number)

        # ----- Scenario 3: exhausted all attempts -----
        logger.warning("Issue #%d unresolved after %d attempts", number, max_attempts)
        _comment_unresolved(number, attempt_history)
        return False

    finally:
        _cleanup_worktree(worktree_path, branch)


def _branch_has_new_commits(worktree_path: Path) -> bool:
    """Check if the worktree branch has any commits ahead of main."""
    log_result = _run(
        ["git", "log", "main..HEAD", "--oneline"],
        cwd=str(worktree_path), check=False,
    )
    return bool(log_result.returncode == 0 and log_result.stdout.strip())


def _push_and_create_pr(
    worktree_path: Path, branch: str, number: int, title: str,
) -> bool:
    """Push the branch and create a PR. Returns True on success.

    NOTE: Worktree cleanup is handled by the caller's ``finally`` block —
    this function must NOT call ``_cleanup_worktree`` itself.
    """
    log_for_pr = _run(
        ["git", "log", "main..HEAD", "--pretty=format:- %s"],
        cwd=str(worktree_path), check=False,
    )
    commit_summary = log_for_pr.stdout.strip() if log_for_pr.returncode == 0 else ""
    commit_count = len(commit_summary.splitlines()) if commit_summary else 0
    logger.info("Branch has %d new commit(s) for issue #%d", commit_count, number)

    push_result = _run(
        ["git", "push", "-u", "origin", branch],
        cwd=str(worktree_path), check=False,
    )
    if push_result.returncode != 0:
        logger.error("Failed to push branch %s: %s", branch, push_result.stderr)
        return False

    pr_body = f"Closes #{number}\n\n"
    if commit_summary:
        pr_body += f"## Changes\n\n{commit_summary}\n\n"
    pr_body += "*Auto-generated fix by Claude Code via `orchestrate/orchestrator.py`.*"

    pr_result = _run(
        [
            "gh", "pr", "create",
            "--title", f"Fix #{number}: {title}",
            "--body", pr_body,
            "--head", branch,
        ],
        cwd=str(worktree_path), check=False,
    )
    if pr_result.returncode == 0:
        logger.info("PR created for issue #%d", number)
    else:
        logger.error("Failed to create PR: %s", pr_result.stderr)

    return pr_result.returncode == 0


def _comment_and_close_issue(number: int, claude_output: str) -> None:
    """Scenario 1 — resolved without code changes.

    Post Claude's findings as a comment and close the issue.
    """
    if claude_output:
        max_len = 60000
        if len(claude_output) > max_len:
            claude_output = claude_output[:max_len] + "\n\n… (truncated)"
        comment = (
            "## ✅ Auto-fix 结果：已解决（无需代码变更）\n\n"
            "Claude 已完成调查，benchmark 已通过。问题无需代码变更即可解决。\n\n"
            "### 诊断报告\n\n"
            f"{claude_output}\n\n"
            "---\n"
            "*Auto-resolved by `orchestrate/orchestrator.py fix`*"
        )
    else:
        comment = (
            "## ✅ Auto-fix 结果：已解决（无需代码变更）\n\n"
            "Claude 已完成调查，benchmark 已通过。未产生代码变更。\n\n"
            "---\n"
            "*Auto-resolved by `orchestrate/orchestrator.py fix`*"
        )

    _run(
        ["gh", "issue", "comment", str(number), "--body", comment],
        check=False,
    )
    _run(
        ["gh", "issue", "close", str(number)],
        check=False,
    )
    logger.info("Commented and closed issue #%d (resolved without code)", number)


def _comment_unresolved(number: int, attempts: list[dict]) -> None:
    """Scenario 3 — unresolved after all attempts.

    Post a summary of every failed attempt as a comment but keep the issue
    open so a human can pick it up.
    """
    attempt_sections: list[str] = []
    for rec in attempts:
        code_tag = "有代码改动（benchmark 仍未通过）" if rec["had_code_changes"] else "无代码改动"
        summary = rec["summary"][:1000]
        attempt_sections.append(
            f"### 尝试 {rec['attempt']}\n"
            f"- **状态**: {code_tag}\n"
            f"- **摘要**:\n\n"
            f"```\n{summary}\n```"
        )

    body_parts = "\n\n".join(attempt_sections)

    comment = (
        f"## ❌ Auto-fix 失败：经过 {len(attempts)} 次尝试未能解决\n\n"
        f"{body_parts}\n\n"
        "---\n"
        "此 issue 保持 **打开** 状态，需要人工介入排查。\n\n"
        "*Auto-generated by `orchestrate/orchestrator.py fix`*"
    )

    max_len = 60000
    if len(comment) > max_len:
        comment = comment[:max_len] + "\n\n… (truncated)"

    _run(
        ["gh", "issue", "comment", str(number), "--body", comment],
        check=False,
    )
    logger.info("Posted failure report on issue #%d — keeping open for human review", number)


def _cleanup_worktree(worktree_path: Path, branch: str) -> None:
    """Remove the worktree directory and, if the branch was never pushed,
    delete the local branch as well."""
    _run(["git", "worktree", "remove", "--force", str(worktree_path)],
         cwd=str(PROJECT_ROOT), check=False)
    if worktree_path.exists():
        logger.warning("Worktree directory still exists, removing manually: %s", worktree_path)
        shutil.rmtree(worktree_path, ignore_errors=True)
    if not _branch_exists_remote(branch):
        _run(["git", "branch", "-D", branch], cwd=str(PROJECT_ROOT), check=False)


def cmd_fix() -> None:
    """Find open issues labeled claude-fix and attempt to resolve them."""
    logger.info("=== Fixing open issues ===")

    if not _gh_available():
        logger.error("gh CLI not available or not authenticated — cannot list issues")
        sys.exit(1)

    if not _claude_available():
        logger.error("claude CLI not found in PATH — cannot fix issues")
        sys.exit(1)

    issues = _get_open_issues()
    if not issues:
        logger.info("No open issues with label '%s' — nothing to fix", ISSUE_LABEL)
        return

    logger.info("Found %d open issue(s) to process", len(issues))

    resolved_count = 0
    unresolved_count = 0
    for issue in issues:
        try:
            ok = _fix_issue(issue)
            if ok:
                resolved_count += 1
            else:
                unresolved_count += 1
        except Exception:
            logger.exception("Unexpected error fixing issue #%d", issue["number"])
            unresolved_count += 1

    logger.info(
        "Fix phase complete: %d resolved, %d unresolved out of %d issues",
        resolved_count, unresolved_count, len(issues),
    )


# ---------------------------------------------------------------------------
# Sub-command: loop
# ---------------------------------------------------------------------------

def cmd_loop() -> None:
    """Full cycle: run benchmarks then fix open issues."""
    import time

    logger.info("========== Orchestrator loop started ==========")
    start = datetime.now(timezone.utc)

    report = cmd_benchmark()

    has_actionable = any(
        r["status"] in ("FAIL", "WARN")
        for r in report.get("results", [])
    )
    if has_actionable:
        logger.info("Waiting for GitHub API to index the new issue …")
        time.sleep(5)

    cmd_fix()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("========== Loop complete (%.0fs) ==========", elapsed)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CIDector orchestrator — benchmark, fix, and loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            sub-commands:
              benchmark   Run benchmark suite; create GitHub issue on failure
              fix         Pick up open claude-fix issues; fix with Claude CLI in worktrees
              loop        Full cycle: benchmark then fix
        """),
    )
    parser.add_argument(
        "command",
        choices=["benchmark", "fix", "loop"],
        help="Sub-command to run",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Increase log verbosity",
    )
    parser.add_argument(
        "--max-issues", type=int, default=_config["max_issues"],
        help=f"Max issues to process in one run (default: {_config['max_issues']})",
    )
    parser.add_argument(
        "--max-attempts", type=int, default=_config["max_fix_attempts"],
        help=f"Max fix attempts per issue before giving up (default: {_config['max_fix_attempts']})",
    )
    args = parser.parse_args()

    _config["max_issues"] = args.max_issues
    _config["max_fix_attempts"] = args.max_attempts

    _setup_logging(args.verbose)

    dispatch = {
        "benchmark": cmd_benchmark,
        "fix": cmd_fix,
        "loop": cmd_loop,
    }
    dispatch[args.command]()


if __name__ == "__main__":
    main()
