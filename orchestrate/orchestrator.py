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
CLAUDE_MAX_TURNS = 20
CLAUDE_MAX_BUDGET_USD = 5
MAX_FIX_ATTEMPTS = 3

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


def _run(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with logging."""
    logger.debug("$ %s (cwd=%s)", " ".join(cmd), cwd or ".")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        cwd=cwd,
        timeout=timeout,
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
    m = re.search(r'^\[Benchmark\]\[(?:FAIL|WARN)\]\s*(.+?)\s*—', title)
    return m.group(1).strip() if m else None


def _verify_fix_with_details(
    worktree_path: Path, case_name: str | None,
) -> tuple[bool, dict]:
    """Run benchmarks in the worktree; return (pass?, structured details for issue text)."""
    runner = worktree_path / "benchmarks" / "run_benchmarks.py"
    cmd = [sys.executable, str(runner), "--verbose"]
    if case_name:
        cmd += ["--filter", case_name]

    details: dict = {
        "command": " ".join(cmd),
        "runner_exit_code": None,
        "json_ok": False,
        "parse_error": None,
        "runner_stderr_tail": None,
        "target_case": case_name,
        "target_status": None,
        "target_error": None,
        "target_duration_sec": None,
        "suite": None,
        "failing_cases": None,
        "pass": False,
    }

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=180,
        cwd=str(worktree_path),
    )
    details["runner_exit_code"] = result.returncode
    err_tail = (result.stderr or "").strip()
    if err_tail:
        details["runner_stderr_tail"] = err_tail[-2000:]

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        details["parse_error"] = str(e)
        out_head = (result.stdout or "")[:1200]
        details["stdout_head"] = out_head
        logger.warning("Could not parse benchmark output during verification")
        details["pass"] = False
        return False, details

    details["json_ok"] = True
    details["suite"] = {
        "passed": report.get("passed"),
        "failed": report.get("failed"),
        "warned": report.get("warned"),
        "skipped": report.get("skipped"),
    }

    if "error" in report and report.get("results") is None:
        details["target_error"] = str(report.get("error") or "").strip() or None
        details["pass"] = False
        logger.warning("Benchmark verification returned error payload: %s",
                       details["target_error"])
        return False, details

    results = report.get("results", [])
    if case_name:
        for r in results:
            if r["name"] == case_name:
                details["target_status"] = r.get("status")
                details["target_error"] = (r.get("error") or "").strip() or None
                details["target_duration_sec"] = r.get("duration_sec")
                ok = r["status"] == "PASS"
                details["pass"] = ok
                return ok, details
        logger.warning("Case '%s' not found in benchmark results", case_name)
        details["target_error"] = f"case '{case_name}' 未出现在本次 benchmark 结果中"
        details["pass"] = False
        return False, details

    fail_names = [
        r["name"] for r in results
        if r.get("status") == "FAIL"
    ]
    warn_names = [
        r["name"] for r in results
        if r.get("status") == "WARN"
    ]
    details["failing_cases"] = {
        "FAIL": fail_names,
        "WARN": warn_names,
    }
    ok = report.get("failed", 1) == 0
    details["pass"] = ok
    if not ok and fail_names:
        first = next((r for r in results if r.get("name") in fail_names), None)
        if first:
            details["target_error"] = (
                f"全量模式下首个 FAIL: **{first.get('name')}** — "
                f"{(first.get('error') or '')[:500]}"
            ).strip()
    return ok, details


def _verify_fix(worktree_path: Path, case_name: str | None) -> bool:
    """Run benchmarks in the worktree and check whether the target case passes."""
    ok, _ = _verify_fix_with_details(worktree_path, case_name)
    return ok


def _collect_git_attempt_summary(worktree_path: Path, base_ref: str) -> dict:
    """Summarize repo changes vs baseline after Claude (for issue comments)."""
    stat = _run(
        ["git", "diff", "--stat", base_ref],
        cwd=str(worktree_path), check=False,
    )
    names = _run(
        ["git", "diff", "--name-only", base_ref],
        cwd=str(worktree_path), check=False,
    )
    files = [x for x in names.stdout.strip().split("\n") if x]
    log_r = _run(
        ["git", "log", f"{base_ref}..HEAD", "--oneline"],
        cwd=str(worktree_path), check=False,
    )
    commits = [ln for ln in log_r.stdout.strip().split("\n") if ln]
    stat_text = (stat.stdout or "").strip()
    return {
        "diff_stat": stat_text if stat_text else "(相对基线无文件差异)",
        "files": files,
        "commits_ahead": commits,
    }


def _format_claude_transcript_for_issue(
    stdout: str | None,
    stderr: str | None,
    *,
    head_chars: int = 3200,
    tail_chars: int = 3200,
    stderr_max: int = 2800,
) -> str:
    """Readable Claude CLI transcript: stderr + stdout head/tail (agent 'direction')."""
    blocks: list[str] = []
    out = (stdout or "").strip()
    err = (stderr or "").strip()

    if "Reached max turns" in out or "Reached max turns" in err:
        blocks.append(
            "> **失败类型**：`Reached max turns` — 本轮在 `--max-turns "
            f"{CLAUDE_MAX_TURNS}` 内未跑完；下面节选可能**没有最终总结**，"
            "但通常能看到**前期读了哪些文件、执行了哪些命令**。"
        )

    if err:
        e = err if len(err) <= stderr_max else ("…" + err[-stderr_max:])
        blocks.append("**Claude stderr**（若有）:\n```\n" + e + "\n```")

    if not out:
        blocks.append("**Claude stdout**: *(空)*")
        return "\n\n".join(blocks)

    if len(out) <= head_chars + tail_chars + 80:
        blocks.append("**Claude stdout**（全文）:\n```\n" + out + "\n```")
    else:
        h = out[:head_chars]
        t = out[-tail_chars:]
        blocks.append(
            "**Claude stdout**（**首部** — 往往含计划与早期工具调用）:\n```\n"
            + h + "\n```"
        )
        blocks.append(
            "**Claude stdout**（**尾部** — 往往含最后几步与报错）:\n```\n"
            + t + "\n```"
        )
    return "\n\n".join(blocks)


def _format_verification_markdown(v: dict) -> str:
    """Turn _verify_fix_with_details payload into issue-friendly markdown."""
    lines = [
        "##### 验证结果（benchmark）",
        f"- **命令**: `{v['command']}`",
        f"- **进程退出码**: `{v['runner_exit_code']}`",
    ]
    if not v.get("json_ok"):
        lines.append("- **JSON 解析**: 失败")
        if v.get("parse_error"):
            lines.append(f"  - 原因: `{v['parse_error']}`")
        if v.get("stdout_head"):
            lines.append(
                "- **stdout 开头**（非 JSON 时）:\n```\n"
                + v["stdout_head"][:800] + "\n```"
            )
        if v.get("runner_stderr_tail"):
            lines.append(
                "- **stderr 尾部**:\n```\n"
                + v["runner_stderr_tail"][:1200] + "\n```"
            )
        return "\n".join(lines)

    lines.append("- **JSON 解析**: 成功")
    s = v.get("suite") or {}
    lines.append(
        f"- **本 run 汇总**: passed={s.get('passed')!s}, failed={s.get('failed')!s}, "
        f"warned={s.get('warned')!s}, skipped={s.get('skipped')!s}"
    )
    if v.get("target_case"):
        st = v.get("target_status") or "?"
        lines.append(f"- **目标 case `{v['target_case']}` 状态**: **{st}**")
        if v.get("target_duration_sec") is not None:
            lines.append(f"  - 耗时: {v['target_duration_sec']}s")
        if v.get("target_error"):
            err = v["target_error"]
            if len(err) > 1500:
                err = err[:1500] + "…"
            lines.append(f"- **仍失败时的错误/判定**: ```\n{err}\n```")
    else:
        fc = v.get("failing_cases") or {}
        fails = fc.get("FAIL") or []
        warns = fc.get("WARN") or []
        if fails:
            lines.append(f"- **FAIL cases**: {', '.join(f'`{n}`' for n in fails)}")
        if warns:
            lines.append(f"- **WARN cases**: {', '.join(f'`{n}`' for n in warns)}")
        if v.get("target_error"):
            lines.append(f"- **说明**: {v['target_error']}")

    lines.append(
        f"- **判定**: {'✅ 验证通过' if v.get('pass') else '❌ 验证未通过（须 PASS / 全量零 FAIL）'}"
    )
    return "\n".join(lines)


def _format_git_attempt_markdown(g: dict) -> str:
    """Git change summary for one fix attempt."""
    lines = [
        "##### 代码与提交动向（相对本轮基线 commit）",
        f"- **涉及文件** ({len(g['files'])}): "
        + (", ".join(f"`{f}`" for f in g["files"]) if g["files"] else "*(无)*"),
        "- **diff --stat**:\n```\n" + g["diff_stat"] + "\n```",
    ]
    if g.get("commits_ahead"):
        lines.append("- **基线之后的 commit**:")
        for c in g["commits_ahead"][:15]:
            lines.append(f"  - `{c}`")
        if len(g["commits_ahead"]) > 15:
            lines.append(f"  - … 另有 {len(g['commits_ahead']) - 15} 条")
    else:
        lines.append("- **基线之后的 commit**: *(无)*")
    return "\n".join(lines)


def _compact_attempt_retry_summary(
    verification: dict,
    git_summary: dict,
    claude_exit: int,
    stdout: str,
    stderr: str,
    max_len: int = 800,
) -> str:
    """Short line for build_fix_prompt RETRY CONTEXT (must stay small)."""
    chunks: list[str] = []
    if verification.get("json_ok"):
        tc = verification.get("target_case")
        st = verification.get("target_status")
        if tc:
            chunks.append(f"验证 case={tc} → {st}")
        else:
            s = verification.get("suite") or {}
            chunks.append(
                f"验证全量 failed={s.get('failed')!s} warned={s.get('warned')!s}"
            )
        err = (verification.get("target_error") or "").strip()
        if err:
            chunks.append(f"错误摘录: {err[:220]}")
    else:
        chunks.append("验证: stdout 非 JSON 或解析失败")
    fl = git_summary.get("files") or []
    chunks.append(f"改动文件: {', '.join(fl[:6]) or '无'}")
    if claude_exit != 0:
        chunks.append(f"claude_exit={claude_exit}")
    blob = (stdout or "") + (stderr or "")
    if "Reached max turns" in blob:
        chunks.append("Claude 达 max-turns 未结束")
    text = " | ".join(chunks)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


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


def _branch_exists_remote(
    branch: str, *, on_timeout_treat_as_exists: bool = False,
) -> bool:
    """Return True if *branch* exists on origin.

    Uses a bounded wait so cleanup after Ctrl+C does not hang on slow networks.
    On timeout: when *on_timeout_treat_as_exists* is True (cleanup path), assume
    the branch exists so we do not delete a possibly-pushed branch.
    """
    try:
        result = _run(
            ["git", "ls-remote", "--heads", "origin", branch],
            cwd=str(PROJECT_ROOT),
            check=False,
            timeout=60.0,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "git ls-remote timed out for %s — treating as %s",
            branch,
            "remote exists" if on_timeout_treat_as_exists else "not on remote",
        )
        return on_timeout_treat_as_exists
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

            # ----- Preflight: if the target benchmark already passes on the
            # current baseline, do not attribute the resolution to a new agent run.
            logger.info("Preflight benchmark check before launching Claude (case=%s) …",
                        case_name or "ALL")
            preflight_passed, preflight_details = _verify_fix_with_details(
                worktree_path, case_name)
            if case_name and preflight_passed:
                logger.info("Issue #%d already passes before agent run (attempt %d)",
                            number, attempt)
                _comment_and_close_issue(
                    number,
                    case_name=case_name,
                    verification_details=preflight_details,
                    claude_output="",
                    claude_exit_code=None,
                    preflight=True,
                )
                return True

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
            benchmark_passed, verification_details = _verify_fix_with_details(
                worktree_path, case_name)

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
                _comment_and_close_issue(
                    number,
                    case_name=case_name,
                    verification_details=verification_details,
                    claude_output=claude_output,
                    claude_exit_code=claude_result.returncode,
                    preflight=False,
                )
                return True

            # ----- Not resolved — record and retry -----
            git_summary = _collect_git_attempt_summary(worktree_path, base_ref)
            raw_out = claude_result.stdout or ""
            raw_err = claude_result.stderr or ""
            attempt_history.append({
                "attempt": attempt,
                "had_code_changes": has_code_changes,
                "claude_exit_code": claude_result.returncode,
                "summary": _compact_attempt_retry_summary(
                    verification_details,
                    git_summary,
                    claude_result.returncode,
                    raw_out,
                    raw_err,
                ),
                "verification": verification_details,
                "git": git_summary,
                "claude_stdout": raw_out,
                "claude_stderr": raw_err,
            })
            logger.warning("Attempt %d/%d failed for issue #%d — benchmark still failing",
                           attempt, max_attempts, number)

        # ----- Scenario 3: exhausted all attempts -----
        logger.warning("Issue #%d unresolved after %d attempts", number, max_attempts)
        _comment_unresolved(
            number,
            attempt_history,
            case_name=case_name,
            max_attempts=max_attempts,
            branch=branch,
        )
        return False

    finally:
        try:
            _cleanup_worktree(worktree_path, branch)
        except KeyboardInterrupt:
            logger.warning(
                "Cleanup interrupted (Ctrl+C). Finish manually if needed: "
                "git worktree prune; git branch -D %s (only if never pushed).",
                branch,
            )


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


def _comment_and_close_issue(
    number: int,
    *,
    case_name: str | None,
    verification_details: dict,
    claude_output: str,
    claude_exit_code: int | None,
    preflight: bool,
) -> None:
    """Close an issue when benchmark passes without new repo code changes.

    This covers two cases:
    1) The benchmark already passed before launching the agent
    2) The benchmark passed after the run, but the worktree still has no new
       repo changes attributable to this attempt
    """
    header = "## ✅ Auto-fix 结果：当前 benchmark 已通过（未产生新的仓库代码变更）"
    if preflight:
        intro = (
            "在启动修复代理前，目标 benchmark 已经通过。更合理的解释是："
            "问题已被仓库现有代码修复、上游数据源已恢复，或该失败本身具有瞬时性。"
        )
    else:
        intro = (
            "本轮验证时 benchmark 已通过，但 worktree 相对基线未检测到新的仓库代码改动。"
            "因此不应将这次关闭解读为“本轮 agent 通过提交代码完成修复”；"
            "更可能是现有代码已覆盖该问题、上游数据源恢复，或失败具有瞬时性。"
        )

    sections = [
        header,
        "",
        intro,
        "",
        "### 验证结果",
        "",
        _format_verification_markdown(verification_details),
    ]

    if case_name:
        sections.extend([
            "",
            f"- **目标 case**: `{case_name}`",
        ])
    if claude_exit_code is not None:
        sections.extend([
            f"- **修复代理退出码**: `{claude_exit_code}`",
        ])

    trimmed_output = (claude_output or "").strip()
    if trimmed_output:
        max_len = 12000
        transcript = _format_claude_transcript_for_issue(
            trimmed_output,
            "",
            head_chars=2400,
            tail_chars=2400,
            stderr_max=2000,
        )
        if len(transcript) > max_len:
            transcript = transcript[:max_len] + "\n\n… *(代理输出过长已截断)*"
        sections.extend([
            "",
            "### 代理输出（供参考）",
            "",
            transcript,
        ])

    sections.extend([
        "",
        "---",
        "*Auto-resolved by `orchestrate/orchestrator.py fix`*",
    ])
    comment = "\n".join(sections)

    _run(
        ["gh", "issue", "comment", str(number), "--body", comment],
        check=False,
    )
    _run(
        ["gh", "issue", "close", str(number)],
        check=False,
    )
    logger.info("Commented and closed issue #%d (resolved without code)", number)


def _format_fix_methodology_markdown(
    *,
    case_name: str | None,
    max_attempts: int,
    branch: str,
) -> str:
    """Human-readable description of what `fix` actually did (for issue comments)."""
    verify_cmd = (
        f"`python benchmarks/run_benchmarks.py --verbose --filter {case_name}`"
        if case_name
        else "`python benchmarks/run_benchmarks.py --verbose`（全量，要求零 FAIL）"
    )
    case_line = (
        f"从标题解析的 benchmark case：**`{case_name}`**。"
        if case_name
        else "未能从标题解析 case 名，验证时跑 **全量** benchmark。"
    )
    return (
        "### 本次使用的方法（自动化流水线）\n\n"
        f"{case_line}\n\n"
        "| 环节 | 做法 |\n"
        "|------|------|\n"
        f"| 工作副本 | 独立 `git worktree`，分支 `{branch}`；每轮重试前 `git reset --hard` + `git clean -fd` 恢复基线 |\n"
        "| 修复代理 | **Claude Code CLI**：非交互 `-p` 提示词 + "
        f"`--allowedTools Read,Edit,Write,Bash` + `--max-turns {CLAUDE_MAX_TURNS}` |\n"
        "| 单次对话 | 每轮最多 **{CLAUDE_MAX_TURNS}** 个 agent turns；若日志出现 "
        "`Reached max turns`，表示在该轮内对话预算用尽（非 orchestrator 重试次数） |\n"
        f"| 验证 | worktree 内执行 {verify_cmd}，解析 stdout JSON；"
        "须判定目标 case 为 `PASS`（或全量时 `failed == 0`）才算修复成功 |\n"
        f"| 重试 | orchestrator 层最多 **{max_attempts}** 轮；每轮为新进程，"
        "后续轮次会把前几轮的简要结果传入 `build_fix_prompt` 作为上下文 |\n"
    )


def _comment_unresolved(
    number: int,
    attempts: list[dict],
    *,
    case_name: str | None,
    max_attempts: int,
    branch: str,
) -> None:
    """Scenario 3 — unresolved after all attempts.

    Post a summary of every failed attempt as a comment but keep the issue
    open so a human can pick it up.
    """
    methodology = _format_fix_methodology_markdown(
        case_name=case_name,
        max_attempts=max_attempts,
        branch=branch,
    )

    attempt_sections: list[str] = []
    for rec in attempts:
        code_tag = "有代码改动（benchmark 仍未通过）" if rec["had_code_changes"] else "无代码改动"
        exit_code = rec.get("claude_exit_code")
        exit_line = (
            f"- **Claude 进程退出码**: `{exit_code}`\n"
            if exit_code is not None
            else ""
        )
        if rec.get("verification") is not None and rec.get("git") is not None:
            transcript = _format_claude_transcript_for_issue(
                rec.get("claude_stdout"),
                rec.get("claude_stderr"),
                head_chars=2600,
                tail_chars=2600,
                stderr_max=2400,
            )
            if len(transcript) > 12000:
                transcript = transcript[:12000] + "\n\n… *(代理 transcript 过长已截断)*"
            attempt_sections.append(
                f"#### 尝试 {rec['attempt']}\n\n"
                f"- **工作区是否有改动（暂存区/工作树相对基线）**: {code_tag}\n"
                f"{exit_line}\n"
                f"{_format_verification_markdown(rec['verification'])}\n\n"
                f"{_format_git_attempt_markdown(rec['git'])}\n\n"
                f"##### 代理侧（Claude CLI）\n{transcript}"
            )
        else:
            summary = (rec.get("summary") or "")[:1200]
            attempt_sections.append(
                f"#### 尝试 {rec['attempt']}\n"
                f"- **代码变更**: {code_tag}\n"
                f"{exit_line}"
                f"- **摘要**（历史格式）:\n\n```\n{summary}\n```"
            )

    body_parts = "\n\n".join(attempt_sections)

    comment = (
        f"## ❌ Auto-fix 失败：经过 {len(attempts)} 次尝试未能解决\n\n"
        f"{methodology}\n"
        "### 各轮结果\n\n"
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
    if not _branch_exists_remote(branch, on_timeout_treat_as_exists=True):
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
