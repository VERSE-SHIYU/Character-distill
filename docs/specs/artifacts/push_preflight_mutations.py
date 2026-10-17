#!/usr/bin/env python3
"""push-preflight 的变异对账：每条变异必须让指定测试变红。

字节级读写（read_bytes / write_bytes），避免 Windows 上 write_text 把 LF 改成 CRLF。
用法（测试库先起好）：
    .venv/Scripts/python.exe docs/specs/artifacts/push_preflight_mutations.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[3]
TESTS = "tests/test_push_preflight.py"

# (编号, 缺陷形态, 目标文件, 原文, 变异, 期望变红的测试)
MUTATIONS = [
    (
        "M1",
        "钩子忽略检查结果、恒为 0",
        ".githooks/pre-push",
        b'if [ "$needs_check" = true ]; then',
        b"if false; then",
        f"{TESTS}::test_t1_hook_blocks_failing_check",
    ),
    (
        "M2",
        "去掉文档放行（恒跑检查）",
        ".githooks/pre-push",
        b"      docs/*|*.md) ;;",
        b"      docs/*|*.md) needs_check=true ;;",
        f"{TESTS}::test_t2a_docs_only_push_is_allowed",
    ),
    (
        "M3",
        "放行条件写成「有任一文档文件」",
        ".githooks/pre-push",
        b"      *)\n        needs_check=true\n        break 2\n        ;;",
        b"      *)\n        continue\n        ;;",
        f"{TESTS}::test_t2b_docs_plus_code_is_not_allowed",
    ),
    (
        "M4",
        "新分支直接放行",
        ".githooks/pre-push",
        b'    base=$(git merge-base origin/main "$local_sha" 2>/dev/null) || base=""',
        b"    continue",
        f"{TESTS}::test_t2c_new_branch_uses_merge_base",
    ),
    (
        "M5",
        "去掉 REQUIRE_COMPOSE_TESTS",
        "scripts/check.sh",
        b"  export REQUIRE_COMPOSE_TESTS=1\n",
        b"",
        f"{TESTS}::test_t3_backend_uses_same_pytest_invocation_and_declares_pg",
    ),
    (
        "M6",
        "去掉 pipefail",
        "scripts/check.sh",
        b"set -euo pipefail",
        b"set -eu",
        f"{TESTS}::test_t4_backend_propagates_pytest_exit_code",
    ),
    (
        "M7",
        "不写失败摘要",
        "scripts/check.sh",
        b'  if [ -z "${GITHUB_STEP_SUMMARY:-}" ]; then',
        b"  if true; then",
        f"{TESTS}::test_t5_failure_summary_lists_failed_tests",
    ),
    (
        "M8",
        "eslint 失败被吞",
        "scripts/check.sh",
        b"  ( cd web/frontend && npx eslint . -c eslint.ci.config.js --quiet && npm test )",
        b"  ( cd web/frontend && { npx eslint . -c eslint.ci.config.js --quiet || true; } && npm test )",
        f"{TESTS}::test_t6_frontend_runs_eslint_then_npm_test",
    ),
    (
        "M9",
        "某个 job 改回直接写 pytest",
        ".github/workflows/build.yml",
        b"        run: bash scripts/check.sh backend",
        b"        run: pytest tests/ -v --tb=short",
        f"{TESTS}::test_t7_ci_calls_the_same_entrypoint",
    ),
    (
        "M10",
        "删掉 xfail_strict",
        "pytest.ini",
        b"\nxfail_strict = true",
        b"",
        f"{TESTS}::test_t8_xfail_strict_makes_xpass_fail",
    ),
    (
        "M11",
        "解释器改回裸 pytest",
        "scripts/check.sh",
        b'  "$python" -m pytest tests/ -v --tb=short 2>&1 | tee "$log" || rc=$?',
        b'  pytest tests/ -v --tb=short 2>&1 | tee "$log" || rc=$?',
        f"{TESTS}::test_t3_backend_uses_same_pytest_invocation_and_declares_pg",
    ),
    (
        "M12",
        "grep 把含 NUL 的日志当二进制（去掉 -a）",
        "scripts/check.sh",
        b"  grep -aE '^(FAILED|ERROR)' \"$log\" >> \"$GITHUB_STEP_SUMMARY\" || true\n"
        b"  local ids\n"
        b"  ids=$(grep -aoE '^(FAILED|ERROR) [^ ]+' \"$log\" | awk '{print $2}' | head -10 | paste -sd, -)",
        b"  grep -E '^(FAILED|ERROR)' \"$log\" >> \"$GITHUB_STEP_SUMMARY\" || true\n"
        b"  local ids\n"
        b"  ids=$(grep -oE '^(FAILED|ERROR) [^ ]+' \"$log\" | awk '{print $2}' | head -10 | paste -sd, -)",
        f"{TESTS}::test_t5_failure_summary_lists_failed_tests",
    ),
]


def run(test: str) -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", test],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return out.returncode


def main() -> int:
    baseline = run(TESTS)
    print(f"baseline: {'GREEN' if baseline == 0 else f'RED rc={baseline}'} ({TESTS})")
    if baseline != 0:
        print("基线不是绿的：变异表证明不了任何事。")
        return 1

    survived = []
    for name, defect, relpath, old, new, test in MUTATIONS:
        path = REPO / relpath
        original = path.read_bytes()
        hits = original.count(old)
        if hits == 0:
            print(f"{name} PRECONDITION FAILED：{relpath} 里没有命中原文")
            survived.append(name)
            continue
        try:
            path.write_bytes(original.replace(old, new, 1))
            rc = run(test)
        finally:
            path.write_bytes(original)
        state = "RED" if rc != 0 else "SURVIVED"
        print(f"{name} {state} rc={rc} — {defect} → {test.rsplit('::', 1)[-1]}")
        if rc == 0:
            survived.append(name)

    print(f"\n{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} RED")
    if survived:
        print("存活：", ", ".join(survived))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
