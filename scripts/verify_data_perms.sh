#!/usr/bin/env bash
# 验证 data-perms 一次性服务的调用点矩阵。
# 矩阵定义见 docs/specs/fix-upload-dir-permission.md「调用点矩阵」。
#
# 断言名与矩阵格子一一对应：
#   a_write / a_nonroot / a_owner / a_rerun     data/ 全 root 属主
#   b_write / b_nonroot / b_owner / b_rerun     混合属主
#   c_write / c_nonroot / c_rerun               已全为 1000
#   d_blocked                                    钩子命令失败 → app 不启动
#
# 另有若干 `*_pre` 前置断言：它们在**跑之前**确认前置状态真的造出来了
# （例如「uploads 确实是 root 属主」）。没有它们，前置没造出来的话后面每条断言都会
# 「符合预期」地通过 —— 那是假绿，不是证据。
#
# **机制的两半都从 docker-compose.prod.yml 真读出来**再喂给临时编排：钩子的命令与 user，
# 以及 app 对 data-perms 的依赖条件。少了后半边，「把 depends_on 删掉」这种改法会让脚本
# 继续全绿（它照样在等一个自己硬写死的依赖），判据就没了分辨力。
#
# 用一次性临时项目名 + 临时数据目录、不映射端口；跑完即删（trap EXIT）。
# 用法：scripts/verify_data_perms.sh
#   VERIFY_IMAGE  跑矩阵用的镜像（默认 python:3.12-slim，即 app 镜像的基底）
#   PYTHON        读编排文件用的解释器（默认 python3，退到 python；需要 PyYAML）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD_COMPOSE="${PROD_COMPOSE:-$REPO_ROOT/docker-compose.prod.yml}"
VERIFY_IMAGE="${VERIFY_IMAGE:-python:3.12-slim}"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi

WORK="$(mktemp -d)"
PROJECT="verify-data-perms-$$"
FAILED=0

# Docker Desktop（Windows）要原生路径；Git Bash 的 /tmp/... 它认不出来。
NATIVE() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

compose() {
  local d="$1"; shift
  docker compose -p "${PROJECT}-$(basename "$d")" \
    -f "$(NATIVE "$d/docker-compose.yml")" "$@"
}

cleanup() {
  local d
  for d in "$WORK"/a "$WORK"/b "$WORK"/c "$WORK"/d; do
    [ -f "$d/docker-compose.yml" ] || continue
    compose "$d" down -v --remove-orphans >/dev/null 2>&1 || true
  done
  # 失败路径下钩子没跑，文件还是 root 属主，宿主用户删不掉 —— 先用容器改回本用户再删。
  docker run --rm -u 0:0 -v "$(NATIVE "$WORK"):/w" "$VERIFY_IMAGE" \
    sh -c "chown -R $(id -u):$(id -g) /w" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILED=1; }
eq()   { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 — 期望 [$3] 实际 [$2]"; fi; }

# ── 从真编排文件生成临时编排（缺任何一半都硬失败，不猜、不补默认值） ──────────
gen_compose() {  # $1 = 输出路径；$2 = 钩子命令覆盖（JSON flow seq，可空）
  PROD_COMPOSE="$PROD_COMPOSE" VERIFY_IMAGE="$VERIFY_IMAGE" \
  HOOK_OVERRIDE="${2:-}" OUT="$1" "$PYTHON" - <<'PY'
import json, os, sys
import yaml

def die(msg):
    sys.exit(msg)

src = yaml.safe_load(open(os.environ["PROD_COMPOSE"], encoding="utf-8"))
services = src.get("services") or {}
dp = services.get("data-perms") or die("编排里没有 data-perms 服务")
app = services.get("app") or die("编排里没有 app 服务")

if dp.get("user") != "root":
    die(f"data-perms 的 user 不是 root（现为 {dp.get('user')!r}）—— 非 root 改不了别人的属主")

vols = dp.get("volumes") or []
if "/app/data" not in {v.split(":")[-1] for v in vols if isinstance(v, str)}:
    die(f"data-perms 没把某个卷挂到 /app/data（现为 {vols!r}）")

dep = (app.get("depends_on") or {}).get("data-perms")
if not isinstance(dep, dict) or "condition" not in dep:
    die("app.depends_on 里没有对 data-perms 的条件依赖 —— 钩子跑没跑完都没人等它")

override = os.environ.get("HOOK_OVERRIDE") or ""
command = json.loads(override) if override else dp.get("command")
if not command:
    die("data-perms 没有 command")

yaml.safe_dump({
    "services": {
        "data-perms": {
            "image": os.environ["VERIFY_IMAGE"],
            "user": "root",
            "volumes": vols,
            "command": command,
            "restart": "no",
        },
        "app": {
            # 消费者的 uid 写死 1000，与生产 appuser 对齐；本脚本验的是编排机制，
            # 不是「app 镜像里建没建 appuser」（那由 Dockerfile 的 USER 负责）。
            "image": os.environ["VERIFY_IMAGE"],
            "user": "1000:1000",
            "volumes": vols,
            "command": ["sh", "-c",
                        "id -u > /app/data/uploads/uid.txt && touch /app/data/uploads/probe.txt"],
            "depends_on": {"data-perms": {"condition": dep["condition"]}},
        },
    }
}, open(os.environ["OUT"], "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)

print(f"data-perms  user={dp['user']}  app.depends_on.condition={dep['condition']}")
print(f"data-perms  command={json.dumps(command)}")
PY
}

echo "编排：$PROD_COMPOSE"
echo "镜像：$VERIFY_IMAGE"
if ! summary="$(gen_compose "$WORK/header.yml" 2>&1)"; then
  echo "FATAL: 读不出机制（见下），矩阵没跑"
  echo "$summary"
  exit 1
fi
printf '%s\n' "$summary"
echo

# 以 root 读三个值（不受目标目录权限影响，否则「写不进去」会让读数本身 MISSING）
probe_state() {  # $1 = 场景目录；输出三行：uploads 属主 / probe 文件在否 / 消费者 uid
  docker run --rm -u 0:0 -v "$(NATIVE "$1/data"):/app/data" "$VERIFY_IMAGE" sh -c '
    stat -c %u:%g /app/data/uploads 2>/dev/null || echo MISSING
    [ -e /app/data/uploads/probe.txt ] && echo yes || echo no
    cat /app/data/uploads/uid.txt 2>/dev/null || echo none
  '
}

owner_of() {  # $1 = 宿主机目录
  docker run --rm -u 0:0 -v "$(NATIVE "$1"):/d" "$VERIFY_IMAGE" sh -c 'stat -c %u:%g /d'
}

# 前置状态：结构在宿主机侧建出，属主**显式**在容器内 chown 成 0:0。
# 不能靠「宿主机建的文件在容器里就是 0:0」—— 那只是 Windows bind mount 的实测行为；
# Linux 上宿主机建的文件属主是宿主 uid（容器内即 1000），场景 a/b 的前置根本造不出来。
prep_all_root() {
  mkdir -p "$1/data/uploads"; : > "$1/data/uploads/keep.txt"
  docker run --rm -u 0:0 -v "$(NATIVE "$1/data"):/d" "$VERIFY_IMAGE" \
    sh -c 'chown 0:0 /d /d/uploads /d/uploads/keep.txt'
}
prep_mixed() {
  prep_all_root "$1"
  docker run --rm -u 0:0 -v "$(NATIVE "$1/data"):/d" "$VERIFY_IMAGE" sh -c 'chown 1000:1000 /d'
}
prep_all_1000() {
  prep_all_root "$1"
  docker run --rm -u 0:0 -v "$(NATIVE "$1/data"):/d" "$VERIFY_IMAGE" sh -c 'chown -R 1000:1000 /d'
}

dp_started_at() {  # data-perms 容器最近一次启动时刻 —— 用来判「钩子是否重跑」
  local cid
  cid="$(compose "$1" ps -a -q data-perms)"
  [ -n "$cid" ] || { printf 'none'; return; }
  docker inspect -f '{{.State.StartedAt}}' "$cid"
}

run_scenario() {  # $1 = 前缀；$2 = 预置函数；$3 = 期望的前置 uploads 属主
  local p="$1" prep="$2" want_pre="$3" d="$WORK/$1"
  rm -rf "$d"; mkdir -p "$d"
  "$prep" "$d"

  local pre; pre="$(owner_of "$d/data/uploads")"
  if [ "$pre" != "$want_pre" ]; then
    fail "${p}_pre — 前置状态没造出来（uploads 属主 $pre，想要 $want_pre），本场景结论无效"
    return
  fi
  pass "${p}_pre"

  local up_out
  if ! up_out="$(gen_compose "$d/docker-compose.yml" 2>&1)"; then
    fail "${p}_gen — 临时编排生成失败：$up_out"
    return
  fi
  if ! up_out="$(compose "$d" up -d 2>&1)"; then
    fail "${p}_up — up -d 失败：$up_out"
    return
  fi

  local owner probe uidfile
  { read -r owner; read -r probe; read -r uidfile; } < <(probe_state "$d")
  eq "${p}_owner"   "$owner"   "1000:1000"
  eq "${p}_write"   "$probe"   "yes"
  eq "${p}_nonroot" "$uidfile" "1000"

  local before after
  before="$(dp_started_at "$d")"
  if ! up_out="$(compose "$d" up -d 2>&1)"; then
    fail "${p}_rerun — 二次 up -d 失败：$up_out"
    return
  fi
  after="$(dp_started_at "$d")"
  if [ "$before" = "$after" ]; then
    fail "${p}_rerun — 二次 up -d 后 data-perms 未重跑（StartedAt 仍是 $before）"
  else
    pass "${p}_rerun"
  fi

  { read -r owner; read -r probe; read -r uidfile; } < <(probe_state "$d")
  eq "${p}_rerun_side" "$owner" "1000:1000"
}

echo "── 场景 a：data/ 全 root 属主 ──"
run_scenario a prep_all_root 0:0
echo "── 场景 b：混合属主 ──"
run_scenario b prep_mixed 0:0
echo "── 场景 c：已全为 1000 ──"
run_scenario c prep_all_1000 1000:1000

echo "── 场景 d：钩子命令失败 ──"
d="$WORK/d"; rm -rf "$d"; mkdir -p "$d/data/uploads"
if ! gen_err="$(gen_compose "$d/docker-compose.yml" '["false"]' 2>&1)"; then
  fail "d_gen — 临时编排生成失败：$gen_err"
elif compose "$d" up -d >/dev/null 2>&1; then
  fail "d_blocked — 钩子失败时 up -d 竟然成功"
elif [ -n "$(compose "$d" ps -q app)" ]; then
  fail "d_blocked — 钩子失败，但 app 仍然起来了"
else
  pass "d_blocked"
fi

echo
if [ "$FAILED" -eq 0 ]; then
  echo "全部通过。"
else
  echo "有断言失败（见上面的 FAIL）。"
fi
exit "$FAILED"
