"""Prompt template for the Claude fixing agent.

This prompt is injected via `claude -p` when the orchestrator dispatches a
fix task to a worktree. It must give the agent a complete mental model of:

1. The project architecture (what this codebase does)
2. The specific problem (what broke and why)
3. The debugging methodology (how to approach the fix)
4. Safety constraints (what NOT to do)
5. Definition of done (how to verify the fix)

The orchestrator may invoke this prompt multiple times (up to max_attempts)
for the same issue.  Each invocation receives the attempt number and a
summary of previous failed attempts so the agent can try a different angle.
"""

from __future__ import annotations

import textwrap


def _build_attempt_context(
    attempt: int,
    max_attempts: int,
    previous_attempts: list[dict] | None,
) -> str:
    """Build the optional ATTEMPT CONTEXT block for retry runs."""
    if attempt <= 1 or not previous_attempts:
        return ""

    history_lines: list[str] = []
    for rec in previous_attempts:
        code_tag = "有代码改动（但 benchmark 仍未通过）" if rec["had_code_changes"] else "无代码改动"
        history_lines.append(
            f"  尝试 {rec['attempt']}: {code_tag}\n"
            f"    摘要: {rec['summary'][:800]}"
        )

    history_block = "\n".join(history_lines)

    return textwrap.dedent(f"""\

        ═══════════════════════════════════════════════════════
        ⚠ RETRY CONTEXT — 第 {attempt} 次尝试（共 {max_attempts} 次）
        ═══════════════════════════════════════════════════════

        前 {attempt - 1} 次尝试均未通过 benchmark 验证，以下是历次摘要：

        {history_block}

        你 **必须** 采取与之前不同的策略。可以考虑：
        - 换一种解析方式（CSS selector → XPath → regex）
        - 换一个 API 端点或数据源
        - 降级到 Playwright 动态渲染
        - 调整超时 / 重试参数
        - 检查上游网站是否已永久变更，需要重写抓取逻辑
    """)


def build_fix_prompt(
    number: int,
    title: str,
    body: str,
    *,
    attempt: int = 1,
    max_attempts: int = 10,
    previous_attempts: list[dict] | None = None,
) -> str:
    """Build the full prompt for Claude to fix a single issue.

    This prompt is designed to be self-contained — the fixing agent runs in an
    isolated worktree and has no prior context.
    """
    attempt_ctx = _build_attempt_context(attempt, max_attempts, previous_attempts)

    return textwrap.dedent(f"""\
        You are a software engineer fixing a failing benchmark in the CIDector project.
        CIDector is a biomedical competitive intelligence tool suite — a collection of
        Python CLI scripts (in `tools/`) that fetch data from external sources
        (ClinicalTrials.gov, PubMed, CDE, ASCO, RSS feeds, etc.) and return structured
        JSON to stdout.

        ═══════════════════════════════════════════════════════
        STEP 0: READ PROJECT CONTEXT
        ═══════════════════════════════════════════════════════

        Run: cat CLAUDE.md
        This file describes the full project architecture, tool interfaces, and conventions.
        {attempt_ctx}
        ═══════════════════════════════════════════════════════
        STEP 1: UNDERSTAND THE ISSUE
        ═══════════════════════════════════════════════════════

        GitHub Issue #{number}: {title}

        --- Issue Body ---
        {body}
        --- End Issue Body ---

        ═══════════════════════════════════════════════════════
        STEP 2: REPRODUCE THE BUG
        ═══════════════════════════════════════════════════════

        Before writing any code, reproduce the failure:

        1. Find the "复现步骤" section in the issue body above and run that command.
        2. Observe the actual output — does it match the error described?
        3. If the error is intermittent (e.g. network timeout), run it 2-3 times.

        IMPORTANT: If the tool now succeeds (transient network issue), run the full
        benchmark suite to confirm:
          python benchmarks/run_benchmarks.py --verbose
        If all pass, note this in a commit message and stop — no code change needed.

        ═══════════════════════════════════════════════════════
        STEP 3: DIAGNOSE ROOT CAUSE
        ═══════════════════════════════════════════════════════

        Follow this decision tree based on the error category in the issue:

        **http_403_forbidden / antibot_challenge:**
        - Read `utils/http_client.py` — check User-Agent, headers, anti-bot detection
        - Test if the target URL is accessible with curl from terminal
        - Check if `fetch_text_auto` correctly falls back to Playwright
        - Inspect `_looks_like_antibot()` for missing detection patterns

        **empty_results (0 real items):**
        - Run the tool and pipe stdout to a file: `python tools/xxx.py ... > /tmp/debug.json`
        - Check if the raw HTML/JSON response from the upstream source has data
        - Read the tool source file — look for CSS selectors, XPath, regex that parse the response
        - Fetch the page manually: `python tools/fetch_page.py --url "TARGET_URL" --format text`
        - Compare the actual HTML structure with what the parser expects

        **crash (exit code non-zero):**
        - Read the traceback carefully — identify the exact file and line
        - Check for missing dependencies: `pip list | grep PACKAGE_NAME`
        - Check for missing env vars referenced in `benchmark_cases.yaml` → `requires_env`

        **timeout:**
        - Check timeout values in `utils/http_client.py` (`_DEFAULT_TIMEOUT`)
        - Test network connectivity to the target host
        - Consider if Playwright fallback is timing out

        **json_parse_error:**
        - Run the tool and check raw stdout for non-JSON content (debug prints, warnings)
        - Ensure all diagnostic output goes to stderr, not stdout

        ═══════════════════════════════════════════════════════
        STEP 4: IMPLEMENT THE FIX
        ═══════════════════════════════════════════════════════

        Principles:
        - **Minimal diff** — change only what's necessary to fix this specific issue
        - **Don't break other tools** — each tool is independent, but they share `utils/`
        - **Preserve the output contract** — tools must output JSON to stdout with the
          `{{source, query, total_results, items}}` schema (or `{{source, url, content}}`
          for fetch_page)
        - **Error resilience** — if upstream data is unavailable, return an empty items
          list with an error marker rather than crashing

        Common fix patterns for this project:
        - Updating CSS selectors/XPath when a website redesigns
        - Adding new anti-bot signatures to `_looks_like_antibot()`
        - Switching from `fetch_text` to `fetch_text_auto` (adds Playwright fallback)
        - Updating API endpoint URLs or request parameters
        - Adding missing error handling for new HTTP status codes
        - Fixing RSS feed URL changes

        Files you'll most likely need to edit:
        - `tools/*.py` — the individual tool scripts
        - `utils/http_client.py` — shared HTTP client, retry logic, anti-bot detection
        - `utils/parsers.py` — shared HTML/data parsing utilities

        Files you should NOT edit (unless there's a bug in them):
        - `benchmarks/run_benchmarks.py` — benchmark runner
        - `benchmarks/benchmark_cases.yaml` — benchmark definitions
        - `orchestrate/` — orchestrator code

        ═══════════════════════════════════════════════════════
        STEP 5: VERIFY THE FIX
        ═══════════════════════════════════════════════════════

        After implementing the fix:

        1. Run the specific failing tool command to confirm it works:
           (use the command from "复现步骤" in the issue)

        2. Run the FULL benchmark suite to confirm nothing else broke:
           python benchmarks/run_benchmarks.py --verbose

        3. Expected outcome:
           - The previously failing case now shows PASS
           - No previously passing case regresses to FAIL
           - WARN on fragile cases is acceptable (they scrape external sites)

        ═══════════════════════════════════════════════════════
        STEP 6: COMMIT (only if you changed code)
        ═══════════════════════════════════════════════════════

        **If you made code changes**, commit them:

        git add -A
        git commit -m "fix: <concise description of what you fixed>

        Resolves #{number}

        Root cause: <one sentence explaining why it broke>
        Fix: <one sentence explaining what you changed>"

        **If no code changes were needed** (environment fix, transient issue,
        or investigation only), do NOT force a commit. Just clearly summarize
        your findings in your final output — the orchestrator will post your
        report as a comment on the issue and close it automatically.

        NOTE: 你的会话结束后，orchestrator 会自动运行 benchmark 验证修复结果。
        如果 benchmark 仍然失败，orchestrator 会自动发起下一次尝试（当前是第
        {attempt}/{max_attempts} 次）。所以请确保你的修复能通过 benchmark 验证。

        Your final output should include:
        - **Root cause**: what caused the failure
        - **Resolution**: what you did (e.g. installed a missing package,
          confirmed transient network issue)
        - **Verification**: benchmark results after your action
        - **Recommendation** (if applicable): any follow-up needed, e.g.
          "add feedparser to requirements.txt" or "mark this case as fragile"

        ═══════════════════════════════════════════════════════
        CONSTRAINTS
        ═══════════════════════════════════════════════════════

        - Do NOT modify .env files or credentials
        - Do NOT change the JSON output schema of any tool
        - Do NOT disable or skip the failing benchmark case
        - Do NOT add broad try/except that silently swallows errors
        - If you install a pip package, also check if it's in requirements.txt
          and add it if missing — this IS a code change worth committing
    """)
