#!/usr/bin/env bash
# 演练 deploy.yml 里 resolve 步骤的 mode=latest 判定，5 个用例。
# 脚本正文由 extract_resolve_step.py 从 deploy.yml 现抽，用 `bash -e` 跑
#（GitHub Actions 在 Linux 上的 run: 默认 shell 就是 `bash -e {0}`）。
#
# 用法:
#   bash docs/specs/artifacts/deploy-pin-build/run-cases.sh | tee .../resolve-cases.txt
#
# 用例 4 的「build 未完成」用假 gh 注入 —— 真实的 in_progress 是一瞬间的状态，
# 不可复现。真实的一次性观察（2026-09-29，main 的 run 36538122819 / sha bcd535fa，
# 当时 build job 还在跑）同样报「尚未完成（status=in_progress）」，此处不入库。
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python}"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

# 真 gh 用来查真实 run；本机缺 jq，用 shim 顶替解释器。
export PATH="$DIR/shim:$PATH"

emit() { # emit <sha> [synthetic_status]
  local sha="$1" status="${2:-}"
  if [ -n "$status" ]; then
    "$PY" "$DIR/extract_resolve_step.py" --sha "$sha" --out "$SCRATCH/step.sh" \
      --synthetic-gh "$SCRATCH/bin" --synthetic-status "$status" > /dev/null
    case_path="$SCRATCH/bin:$DIR/shim:$PATH"
  else
    "$PY" "$DIR/extract_resolve_step.py" --sha "$sha" --out "$SCRATCH/step.sh" > /dev/null
    case_path="$DIR/shim:$PATH"
  fi
}

run_case() { # run_case <说明> <sha> [synthetic_status]
  local label="$1" sha="$2" status="${3:-}"
  echo "### $label"
  emit "$sha" "$status"
  : > "$SCRATCH/out.txt"
  PATH="$case_path" GITHUB_OUTPUT="$SCRATCH/out.txt" bash -e "$SCRATCH/step.sh"
  local rc=$?
  echo "exit=$rc  GITHUB_OUTPUT=$(tr '\n' '|' < "$SCRATCH/out.txt")"
  echo "---"
}

echo "被测脚本指纹（deploy.yml 现抽，表达式替换后；占位符固定为 <SHA>）:"
"$PY" "$DIR/extract_resolve_step.py" --sha '<SHA>' --out "$SCRATCH/step.sh"
echo

run_case "用例1 真实 main、completed + success（be4ad239，run 36531737760）" \
  be4ad2398ae0f5dbd1d4d058cb35b0b65bd79dc5
run_case "用例2 真实 main、completed + failure（f92b074f，run 36290945704）" \
  f92b074f4d31143ee4e53163f767e6163cc2370a
run_case "用例3 真实 该 commit 在 main 上无 build 记录（全 0 sha）" \
  0000000000000000000000000000000000000000
run_case "用例4 合成 status=in_progress（假 gh 注入，真实态不可复现）" \
  deadbeefdeadbeefdeadbeefdeadbeefdeadbeef in_progress
run_case "用例5 真实 功能分支 HEAD（7a61db4b，曾有分支 run 但非 main）" \
  7a61db4b86b3dab3222c57237ce0c05a77162043
