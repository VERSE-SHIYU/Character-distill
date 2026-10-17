#!/usr/bin/env bash
# 唯一的检查入口：本地 pre-push 钩子与 CI 都只调它。
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# 预检的元锁清单：只在这里维护；测试不重抄文件名。
META_LOCKS=(
  tests/test_lock_coverage.py
  tests/test_mutation_anchors.py
  tests/test_mutation_framework.py
  tests/test_evidence_integrity.py
  tests/test_evidence_integrity_mutations.py
  tests/test_ledger_substitution_markers.py
)

# 解释器解析顺序：PYTHON → 主仓库根的 .venv → python。
# 不依赖 PATH 上的 pytest：本机 PATH 上可能是别的 venv，它的绿不是本仓的证据。
resolve_python() {
  if [ -n "${PYTHON:-}" ]; then
    printf '%s' "$PYTHON"
    return
  fi
  local common root
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || common=""
  if [ -n "$common" ]; then
    root=$(dirname "$common")
    if [ -x "$root/.venv/Scripts/python.exe" ]; then
      printf '%s' "$root/.venv/Scripts/python.exe"
      return
    fi
    if [ -x "$root/.venv/bin/python" ]; then
      printf '%s' "$root/.venv/bin/python"
      return
    fi
  fi
  printf '%s' python
}

emit_failure() {
  local log=$1
  if [ -z "${GITHUB_STEP_SUMMARY:-}" ]; then
    return
  fi
  # -a：pytest 输出可能含 NUL，grep 默认会把日志当二进制、只回一句 "Binary file ... matches"。
  grep -aE '^(FAILED|ERROR)' "$log" >> "$GITHUB_STEP_SUMMARY" || true
  local ids
  ids=$(grep -aoE '^(FAILED|ERROR) [^ ]+' "$log" | awk '{print $2}' | head -10 | paste -sd, -)
  echo "::error title=失败的测试::${ids}"
}

run_backend() {
  export REQUIRE_PG_TESTS=1
  export REQUIRE_COMPOSE_TESTS=1
  local python rc=0
  # 可覆盖：本仓全量跑测试时，测试内部也会调 check.sh，默认路径会互相截断。
  local log=${PYTEST_LOG:-pytest.log}
  python=$(resolve_python)
  "$python" -m pytest tests/ -v --tb=short 2>&1 | tee "$log" || rc=$?
  if [ "$rc" -ne 0 ]; then
    emit_failure "$log"
  fi
  return "$rc"
}

run_eslint() {
  ( cd web/frontend && npx eslint . -c eslint.ci.config.js --quiet )
}

run_frontend() {
  run_eslint || return $?
  ( cd web/frontend && npm test )
}

run_preflight() {
  local python rc=0
  local log=${PYTEST_LOG:-pytest.log}
  python=$(resolve_python)
  "$python" -m pytest -q "${META_LOCKS[@]}" 2>&1 | tee "$log" || rc=$?
  if [ "$rc" -ne 0 ]; then
    emit_failure "$log"
    return "$rc"
  fi
  run_eslint
}

if [ "$#" -eq 0 ]; then
  rc=0
  run_backend || rc=$?
  run_frontend || rc=$?
  exit "$rc"
fi

rc=0
for target in "$@"; do
  case "$target" in
    backend) run_backend || rc=$? ;;
    frontend) run_frontend || rc=$? ;;
    preflight) run_preflight || rc=$? ;;
    *)
      echo "用法：$0 [backend|frontend|preflight]..." >&2
      exit 2
      ;;
  esac
done
exit "$rc"
