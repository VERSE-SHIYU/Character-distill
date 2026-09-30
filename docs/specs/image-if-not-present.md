# 部署拉镜像改为「本地有就不拉」（IfNotPresent）（2026-09-30）

> 放到 worktree 的 `docs/specs/image-if-not-present.md`，用 `Test-Path` 确认。补充写进「补充」一节。
> 本 spec 取代 `local-image-fallback.md`（那一版为「先拉、失败才用本地」，已作废，未入库）。
> **执行方**：从 `origin/main`（基线 `599e467`）开分支 `fix/image-if-not-present`（`--no-track`）。只改 `.github/workflows/deploy.yml` 与 `tests/test_deploy_image_pulls.py`。不部署、不 dispatch。
> **skill**：`@verification-before-completion`。
> **已拍板（Shiyu，2026-09-30）**：方案 A。postgres 不再在部署时自动更新小版本，升级改为主动操作。

## 问题与根因
`pull_image` 每次部署都先连仓库；仓库全部连不上时中止部署，哪怕本地已有该镜像。根因：对固定版本的镜像每次都强制连仓库，而业界默认是本地有就不拉。

## 出处对照表
| 权威来源（现行版本） | 条目 | 本 spec 行为 |
|---|---|---|
| Kubernetes 文档 Images（页面标注最新小版本 v1.35） | `IfNotPresent`：本地没有才拉；指定 digest 或非 `:latest` tag 时默认即 `IfNotPresent` | 行为 1、2、3 |
| 同上 | 本地有**同一 digest** 的镜像才算命中 | 行为 4（digest 必须一致） |
| Docker Compose 文件规范 services（约 45 天前更新） | `pull_policy: missing`：本地缓存没有才拉；`latest` 即使 missing 也总会拉 | 行为 1；行为 5（调用方不得用 latest） |

## 做法
1. 新增 `have_image <repo:tag> [digest]`：本地有该引用返回真；给了 digest 时，本地镜像的 RepoDigests 必须含 `<repo>@<digest>`。
2. `pull_image` 开头先查本地：阿里云引用（`$1` 非空）命中 → 用它；回落仓库的 `$2:$4` 命中 → 用它；都不命中才走**原有拉取流程（一行不改）**。命中时打一行「本地已有 …，跳过拉取」。

## 已查实的约束（基线 `599e467`；S0 逐条复核，不成立即停下报告）
C1 `deploy.yml:244` `pull_image()` 定义唯一；6 个调用点（`grep -n 'pull_image "'`）：app、nginx（带 digest）、postgres、fail2ban（`$1` 为阿里云、无 digest）、回滚 app / nginx（无 digest，tag 为 PREV_SHA）。
C2 6 个调用点的 `$4` 分别是 `${COMMIT_SHA}`、`${COMMIT_SHA}`、`16-alpine`、`1.1.0`、`${PREV_SHA}`、`${PREV_SHA}`，没有 `latest`。
C3 app / nginx 本地 `:COMMIT_SHA` 由 `docker tag "$2@$3" "$2:$4"` 产生，其 RepoDigests 含 `$2@$3`。
C4 SZ 清理逻辑保留 `COMMIT_SHA`、`PREV_SHA`、`latest`，回滚所需上一版通常在本地。
C5 仓库里没有任何 workflow 向阿里云同步 postgres / fail2ban（`git grep -n "postgres:16-alpine\|fail2ban:1.1.0" -- .github` 只命中 deploy.yml 的拉取与 compose）；阿里云上这两份本就是静态副本。
C6 全部调用都在 `|| { …; exit 1; }` / `|| { …; return 1; }` 里，函数内失败不触发 `set -e`。

### 路径上已有的机制，逐个核对是否被改变
| 机制 | 前提 | 是否改变 | 处理 |
|---|---|---|---|
| 阿里云支 120s 超时与失败提示 | 本地没有时才走到 | 否 | 原测试保留 |
| digest 保证两节点同一 sha256 | 用的镜像就是目标 digest | 否：本地命中也要求 digest 一致 | `test_local_image_with_other_digest_is_pulled` |
| `PULLED_REGISTRY` → compose `*_IMAGE_REGISTRY` | 取自实际使用的那份 | 否：本地命中按所用引用设置 | 本地命中用例断言 RESULT |
| SSH 40m 预算 | 拉取耗时 | 否：只会更快（本地命中不连网） | — |
| postgres 小版本更新 | 部署时重新拉 | **是**：本地有就不再拉 | 已拍板：改为主动升级 |

## 规模表
| 量 | 数值 | 说明 |
|---|---|---|
| 调用点 | 6 | 全经 `pull_image` |
| 通常命中本地的 | postgres、fail2ban、回滚 app/nginx，以及重复部署同一 commit | 不再连仓库 |
| 仍需拉取的 | 新 commit 的 app、nginx | 走原流程 |
| 额外开销 | 1～2 次 `docker image inspect`（毫秒级） | 每次调用 |

## 调用点矩阵
| 调用点 | 可观测输出 | 守它的测试 |
|---|---|---|
| postgres / fail2ban，本地有阿里云那份 | 不调用 docker pull；RESULT=阿里云；日志「跳过拉取」 | `test_local_third_party_image_skips_pull` |
| app / nginx，本地同 digest | 不拉；RESULT=GHCR；日志「跳过拉取」 | `test_local_own_image_with_same_digest_skips_pull` |
| app / nginx，本地 digest 不同 | 照常按 digest 拉 | `test_local_image_with_other_digest_is_pulled` |
| 回滚 app / nginx，本地有 `:PREV_SHA` | 不拉；RESULT=GHCR | `test_local_rollback_image_skips_pull` |
| 任一调用点，本地没有 | 原拉取流程 | 原有 5 条行为用例 |
| 6 个调用点的 tag | 都不是 latest | `test_no_call_site_uses_latest` |

## 改动原文（补丁 `image-if-not-present.patch`，已在干净的 `599e467` 上 `git apply --3way` 通过）
```diff
diff --git a/.github/workflows/deploy.yml b/.github/workflows/deploy.yml
index d1da817..cd9cde8 100644
--- a/.github/workflows/deploy.yml
+++ b/.github/workflows/deploy.yml
@@ -241,8 +241,29 @@ jobs:
             # GHCR 拉 app 实测 21s～1233s，由整体 command_timeout 兜底（预算见 command_timeout 处）。
             # 失败原因不再丢进 /dev/null —— 回落时打一行 exit code，挂住与「tag 不存在」分得清。
             ALIYUN_PULL_TIMEOUT=120
+            # 本地已有**同一个**镜像吗？$1=repo:tag，$2=digest（可空）。有 digest 时本地镜像的
+            # RepoDigests 必须含 <repo>@<digest>（同一 commit 重新构建过则 digest 不同，不认）。
+            have_image() {
+              docker image inspect "$1" >/dev/null 2>&1 || return 1
+              [ -z "$2" ] && return 0
+              docker image inspect --format '{{join .RepoDigests " "}}' "$1" | grep -qF "${1%:*}@$2"
+            }
+
+            # 本地有就不拉（= Kubernetes 对 digest / 非 latest tag 的默认 IfNotPresent，
+            # Compose 的 pull_policy: missing）：固定版本的镜像不必每次部署都连仓库，仓库挂了也不受影响。
+            # 本地没有才走下面的拉取。调用方从不传 :latest（tests 锁住）。
             pull_image() {
               PULLED_REGISTRY=""
+              if [ -n "$1" ] && have_image "$1" "$3"; then
+                PULLED_REGISTRY="${ALIYUN_REPO_PREFIX}"
+                echo "本地已有 $1，跳过拉取"
+                return 0
+              fi
+              if have_image "$2:$4" "$3"; then
+                PULLED_REGISTRY="${2%/*}"
+                echo "本地已有 $2:$4，跳过拉取"
+                return 0
+              fi
               if [ -n "$1" ]; then
                 local rc=0
                 timeout "${ALIYUN_PULL_TIMEOUT}" docker pull "$1" || rc=$?
diff --git a/tests/test_deploy_image_pulls.py b/tests/test_deploy_image_pulls.py
index 5b3c180..b0bd189 100644
--- a/tests/test_deploy_image_pulls.py
+++ b/tests/test_deploy_image_pulls.py
@@ -6,7 +6,8 @@
   - build.yml 不再登录 / 推送阿里云；
   - deploy.yml 的 app / nginx（含回滚）只从 GHCR 拉，postgres / fail2ban 仍阿里云优先
     （Docker Hub 从 SZ 拉会超时，这两个是同区镜像，拉取正常）；
-  - pull_image 的 $1（阿里云引用）为空即跳过阿里云；非空时保留 120s 单次超时与失败原因。
+  - pull_image 的 $1（阿里云引用）为空即跳过阿里云；非空时保留 120s 单次超时与失败原因；
+  - 本地已有同一镜像（有 digest 时须 digest 一致）就不拉（IfNotPresent），本地没有才拉。
 
 行为用例按比例复刻：阿里云超时取 1s、替身挂 30s，断言 10s 内回落。
 """
@@ -76,6 +77,7 @@ def test_third_party_images_stay_aliyun_first():
 # ── deploy.yml：pull_image 行为 ───────────────────────────────────────────────
 
 _FUNC_RE = re.compile(r"^ {12}pull_image\(\) \{\n.*?^ {12}\}\n", re.M | re.S)
+_HAVE_RE = re.compile(r"^ {12}have_image\(\) \{\n.*?^ {12}\}\n", re.M | re.S)
 _ALIYUN = "reg.example/verse-shiyu"
 _GHCR = "ghcr.io/verse-shiyu"
 
@@ -83,6 +85,12 @@ _GHCR = "ghcr.io/verse-shiyu"
 _DOCKER_STUB = textwrap.dedent("""\
     #!/usr/bin/env bash
     if [ "$1" = "tag" ]; then exit 0; fi
+    if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
+      ref="${@: -1}"
+      case " $STUB_LOCAL " in *" $ref "*) ;; *) exit 1 ;; esac
+      if [ "$3" = "--format" ]; then echo "$STUB_REPODIGESTS"; fi
+      exit 0
+    fi
     ref="$2"
     echo "$ref" >> "$STUB_LOG"
     case "$ref" in
@@ -98,12 +106,15 @@ _DOCKER_STUB = textwrap.dedent("""\
 
 
 def _pull_image_src() -> str:
-    funcs = _FUNC_RE.findall(_DEPLOY.read_text(encoding="utf-8"))
+    text = _DEPLOY.read_text(encoding="utf-8")
+    funcs, haves = _FUNC_RE.findall(text), _HAVE_RE.findall(text)
     assert len(funcs) == 1, f"deploy.yml 里 pull_image() 应恰好 1 个，实际 {len(funcs)}"
-    return textwrap.dedent(funcs[0])
+    assert len(haves) == 1, f"deploy.yml 里 have_image() 应恰好 1 个，实际 {len(haves)}"
+    return textwrap.dedent(haves[0]) + textwrap.dedent(funcs[0])
 
 
-def _run(tmp_path: Path, aliyun: str, ghcr: str, first_arg: str | None = None):
+def _run(tmp_path: Path, aliyun: str, ghcr: str, first_arg: str | None = None,
+         digest: str = "sha256:d", local: str = "", repodigests: str = ""):
     stub = tmp_path / "docker"
     stub.write_text(_DOCKER_STUB, encoding="utf-8")
     stub.chmod(0o755)
@@ -114,12 +125,13 @@ def _run(tmp_path: Path, aliyun: str, ghcr: str, first_arg: str | None = None):
         f'ALIYUN_REPO_PREFIX="{_ALIYUN}"',
         "ALIYUN_PULL_TIMEOUT=1",
         _pull_image_src(),
-        f'pull_image "{first}" "{_GHCR}/character-distill-app" "sha256:d" "abc"'
+        f'pull_image "{first}" "{_GHCR}/character-distill-app" "{digest}" "abc"'
         ' || { echo "RESULT=fail"; exit 0; }',
         'echo "RESULT=$PULLED_REGISTRY"',
     ])
     env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
-           "STUB_ALIYUN": aliyun, "STUB_GHCR": ghcr, "STUB_LOG": str(log)}
+           "STUB_ALIYUN": aliyun, "STUB_GHCR": ghcr, "STUB_LOG": str(log),
+           "STUB_LOCAL": local, "STUB_REPODIGESTS": repodigests}
     t0 = time.monotonic()
     proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
     calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
@@ -166,6 +178,49 @@ def test_both_registries_fail_returns_nonzero(tmp_path):
     assert "RESULT=fail" in proc.stdout
 
 
+_LOCAL_APP = f"{_GHCR}/character-distill-app:abc"
+_LOCAL_PG = f"{_ALIYUN}/postgres:16-alpine"
+_SAME = f"{_GHCR}/character-distill-app@sha256:d"
+
+
+@needs_posix
+def test_local_third_party_image_skips_pull(tmp_path):
+    """postgres / fail2ban：本地已有阿里云那份 → 不连任何仓库（仓库挂了也照常部署）。"""
+    proc, _, calls = _run(tmp_path, aliyun="hang", ghcr="fail", first_arg=_LOCAL_PG, digest="", local=_LOCAL_PG)
+    assert calls == [], calls
+    assert f"RESULT={_ALIYUN}" in proc.stdout and f"本地已有 {_LOCAL_PG}，跳过拉取" in proc.stdout
+
+
+@needs_posix
+def test_local_own_image_with_same_digest_skips_pull(tmp_path):
+    """app / nginx 重部署同一 commit：本地 :tag 的 digest 与目标一致 → 不拉。"""
+    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="hang", first_arg="", local=_LOCAL_APP, repodigests=_SAME)
+    assert calls == [], calls
+    assert f"RESULT={_GHCR}" in proc.stdout and f"本地已有 {_LOCAL_APP}，跳过拉取" in proc.stdout
+
+
+@needs_posix
+def test_local_rollback_image_skips_pull(tmp_path):
+    """回滚形态（无 digest，:PREV_SHA）：清理逻辑本就保留上一版 → 不拉。"""
+    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="hang", first_arg="", digest="", local=_LOCAL_APP)
+    assert calls == [], calls
+    assert f"RESULT={_GHCR}" in proc.stdout
+
+
+@needs_posix
+def test_local_image_with_other_digest_is_pulled(tmp_path):
+    """本地同名 tag 但 digest 不同 → 不认本地，照常按 digest 拉（两节点仍是同一 sha256）。"""
+    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="ok", first_arg="", local=_LOCAL_APP,
+                          repodigests=f"{_GHCR}/character-distill-app@sha256:OTHER")
+    assert calls == [_SAME], calls
+    assert "跳过拉取" not in proc.stdout
+
+
+def test_no_call_site_uses_latest():
+    """「本地有就不拉」只对固定版本成立；:latest 必须每次拉（Compose 同规则）。调用方不得传 latest。"""
+    assert all(c[3] != "latest" for c in _calls()), _calls()
+
+
 # ── deploy.yml：规模 ──────────────────────────────────────────────────────────
 
 _TIMEOUT_ASSIGN_RE = re.compile(r"^ {12}ALIYUN_PULL_TIMEOUT=(\d+)\s*$", re.M)
```

## 测试
```powershell
docker compose -f docker-compose.test.yml up -d --wait
python -m pytest tests/test_deploy_image_pulls.py tests/test_health_probe_targets.py -q -rs
```
预期：无失败；`test_deploy_image_pulls.py` 共 15 条，Windows 上 9 条 skipped（行为用例），Linux 分支 CI 上 15 条全部 passed。不跑 `npm test`。合并门是分支 CI；合并只做 git 操作。
沙箱实测：改后 15 passed；基线 deploy.yml + 新测试 9 失败（行为用例因找不到 `have_image` 全部失败），6 通过。YAML 解析与 actionlint 通过。

## 对账表
| 行为变化 | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| 1. 第三方镜像本地有就不拉 | `test_local_third_party_image_skips_pull` | I1、I2 | 阿里云与 Docker Hub 都连不上时 SZ 仍能部署 |
| 2. 自家镜像同 digest 本地有就不拉 | `test_local_own_image_with_same_digest_skips_pull` | I1、I5、I6、I7 | 重部署同一 commit 不连 GHCR |
| 3. 回滚本地有就不拉 | `test_local_rollback_image_skips_pull` | I1、I7 | GHCR 不可达时回滚仍能完成 |
| 4. digest 不同必须重新拉 | `test_local_image_with_other_digest_is_pulled` | I3、I4 | 两节点始终同一 sha256 |
| 5. 调用方不用 latest | `test_no_call_site_uses_latest` | I8 | 「本地有就不拉」只作用于固定版本 |
| 6.（原有）#87 全部行为 | 原 10 条 | G1～G13 | 不变 |

### 变异实跑结果（发出前实跑，共 21 条）
```
RED   G1 build 恢复阿里云推送步 :: test_build_does_not_touch_aliyun
RED   G2 build 恢复 ALIYUN env :: test_build_does_not_touch_aliyun
RED   G3 app 调用改回阿里云优先 :: test_app_and_nginx_pull_only_from_ghcr
RED   G4 回滚 nginx 改回阿里云优先 :: test_app_and_nginx_pull_only_from_ghcr
RED   G5 去掉 $1 为空的跳过判断 :: test_empty_first_arg_goes_straight_to_ghcr, test_local_image_with_other_digest_is_pulled
RED   G6 command_timeout 40m→30m :: test_worst_case_fits_ssh_budget
RED   G7 阿里云单次超时 120→420（超出预算） :: test_worst_case_fits_ssh_budget
RED   G8 去掉阿里云那支的 timeout :: test_aliyun_hang_falls_back_within_timeout
RED   G9 去掉回落说明行 :: test_aliyun_hang_falls_back_within_timeout, test_aliyun_missing_tag_falls_back_and_says_why
RED   G10 恢复 2>/dev/null :: test_aliyun_missing_tag_falls_back_and_says_why
RED   G11 postgres 跳过阿里云 :: test_third_party_images_stay_aliyun_first
RED   G12 阿里云成功后不 return :: test_aliyun_ok_stays_on_aliyun_without_notice
RED   G13 app 回落仓库改成阿里云 :: test_app_and_nginx_pull_only_from_ghcr
RED   I1 删掉本地优先（两段都删） :: test_local_own_image_with_same_digest_skips_pull, test_local_rollback_image_skips_pull, test_local_third_party_image_skips_pull
RED   I2 只删阿里云引用的本地检查 :: test_local_third_party_image_skips_pull
RED   I3 不核对 digest :: test_local_image_with_other_digest_is_pulled
RED   I4 have_image 恒真 :: test_aliyun_hang_falls_back_within_timeout, test_aliyun_missing_tag_falls_back_and_says_why, test_both_registries_fail_returns_nonzero, test_empty_first_arg_goes_straight_to_ghcr, test_local_image_with_other_digest_is_pulled
RED   I5 digest 比对用错仓库名（含 tag） :: test_local_own_image_with_same_digest_skips_pull
RED   I6 删跳过拉取的日志行 :: test_local_own_image_with_same_digest_skips_pull
RED   I7 本地命中后不 return（继续拉取） :: test_local_own_image_with_same_digest_skips_pull, test_local_rollback_image_skips_pull
RED   I8 调用方传 latest :: test_no_call_site_uses_latest
存活变异: 0
```

## 步骤
**S0**：复核 C1–C6 与机制表；按附录重跑变异，须「存活变异: 0」。不成立即停下报告。
**S1（一个 commit）**：`git apply --3way` 应用补丁。commit message：`fix(ci): skip pulling images already present locally (IfNotPresent)`。
**S2**：按「测试」跑，贴原始输出；推分支、开 PR（描述见下）。

本段改动面内的新问题直接修；撞车或需拍板才停下，不自行记账。

### PR 描述（英文，原样使用）
```
## Problem
`pull_image` contacted a registry on every deploy, even for images already on the host, and aborted the SZ deploy when every registry was unreachable.

## Change
Follow the standard IfNotPresent / `pull_policy: missing` behaviour (Kubernetes defaults to it for digest-pinned and non-`latest` tags):
- new `have_image <repo:tag> [digest]`: true when the image exists locally; when a digest is given, its RepoDigests must contain `<repo>@<digest>`;
- `pull_image` first checks the Aliyun reference, then `<repo>:<tag>`; on a hit it uses the local image and logs it; otherwise the existing pull flow runs unchanged.
postgres, fail2ban, rollbacks and redeploys of the same commit no longer need a registry. postgres minor upgrades become a deliberate step.

## Tests
Five new tests in `tests/test_deploy_image_pulls.py`: local third-party image, local own image with the same digest, local rollback image, different digest still pulled, no call site uses `latest`. All 21 mutations (13 existing + 8 new) verified red.
```

## 附录：变异驱动（仅用于 S0 复核，不提交）
```python
import subprocess, sys
B='.github/workflows/build.yml'; D='.github/workflows/deploy.yml'
def rep(old,new):
    def f(s):
        assert s.count(old)==1,(old[:60],s.count(old)); return s.replace(old,new)
    return f
M=[
 ("G1 build 恢复阿里云推送步",B,rep("      - name: Write digest file","      - name: Push app image to Aliyun CR\n        run: docker push crpi-x.personal.cr.aliyuncs.com/a:b\n\n      - name: Write digest file")),
 ("G2 build 恢复 ALIYUN env",B,rep("env:\n  REGISTRY: ghcr.io\n","env:\n  REGISTRY: ghcr.io\n  ALIYUN_REGISTRY: crpi-x.personal.cr.aliyuncs.com\n") ),
 ("G3 app 调用改回阿里云优先",D,rep('pull_image "" "${GHCR_REPO_PREFIX}/character-distill-app" "${APP_DIGEST}"','pull_image "${ALIYUN_REPO_PREFIX}/character-distill-app:${COMMIT_SHA}" "${GHCR_REPO_PREFIX}/character-distill-app" "${APP_DIGEST}"')),
 ("G4 回滚 nginx 改回阿里云优先",D,rep('pull_image "" "${GHCR_REPO_PREFIX}/character-distill-nginx" "" "${PREV_SHA}"','pull_image "${ALIYUN_REPO_PREFIX}/character-distill-nginx:${PREV_SHA}" "${GHCR_REPO_PREFIX}/character-distill-nginx" "" "${PREV_SHA}"')),
 ("G5 去掉 $1 为空的跳过判断",D,rep('              if [ -n "$1" ]; then\n                local rc=0','              if true; then\n                local rc=0')),
 ("G6 command_timeout 40m→30m",D,rep("command_timeout: 40m","command_timeout: 30m")),
 ("G7 阿里云单次超时 120→420（超出预算）",D,rep("ALIYUN_PULL_TIMEOUT=120\n","ALIYUN_PULL_TIMEOUT=420\n")),
 ("G8 去掉阿里云那支的 timeout",D,rep('timeout "${ALIYUN_PULL_TIMEOUT}" docker pull "$1" || rc=$?','docker pull "$1" || rc=$?')),
 ("G9 去掉回落说明行",D,rep('                echo "阿里云拉取未成功（exit=${rc}，124 表示超过 ${ALIYUN_PULL_TIMEOUT}s）：$1 → 回落 $2"\n','')),
 ("G10 恢复 2>/dev/null",D,rep('docker pull "$1" || rc=$?','docker pull "$1" 2>/dev/null || rc=$?')),
 ("G11 postgres 跳过阿里云",D,rep('pull_image "${ALIYUN_REPO_PREFIX}/postgres:16-alpine"','pull_image ""')),
 ("G12 阿里云成功后不 return",D,rep('                  PULLED_REGISTRY="${ALIYUN_REPO_PREFIX}"\n                  return 0\n','                  PULLED_REGISTRY="${ALIYUN_REPO_PREFIX}"\n')),
 ("G13 app 回落仓库改成阿里云",D,rep('pull_image "" "${GHCR_REPO_PREFIX}/character-distill-app" "${APP_DIGEST}"','pull_image "" "${ALIYUN_REPO_PREFIX}/character-distill-app" "${APP_DIGEST}"')),
 ("I1 删掉本地优先（两段都删）",D,rep('''              if [ -n "$1" ] && have_image "$1" "$3"; then
                PULLED_REGISTRY="${ALIYUN_REPO_PREFIX}"
                echo "本地已有 $1，跳过拉取"
                return 0
              fi
              if have_image "$2:$4" "$3"; then
                PULLED_REGISTRY="${2%/*}"
                echo "本地已有 $2:$4，跳过拉取"
                return 0
              fi
''',"")),
 ("I2 只删阿里云引用的本地检查",D,rep('''              if [ -n "$1" ] && have_image "$1" "$3"; then
                PULLED_REGISTRY="${ALIYUN_REPO_PREFIX}"
                echo "本地已有 $1，跳过拉取"
                return 0
              fi
''',"")),
 ("I3 不核对 digest",D,rep('''              [ -z "$2" ] && return 0
''','''              return 0
''')),
 ("I4 have_image 恒真",D,rep('''              docker image inspect "$1" >/dev/null 2>&1 || return 1
''','''              return 0
''')),
 ("I5 digest 比对用错仓库名（含 tag）",D,rep('''grep -qF "${1%:*}@$2"''','''grep -qF "$1@$2"''')),
 ("I6 删跳过拉取的日志行",D,rep('''                echo "本地已有 $2:$4，跳过拉取"
''',"")),
 ("I7 本地命中后不 return（继续拉取）",D,rep('''                echo "本地已有 $2:$4，跳过拉取"
                return 0
''','''                echo "本地已有 $2:$4，跳过拉取"
''')),
 ("I8 调用方传 latest",D,rep('''"docker.io/crazymax/fail2ban" "" "1.1.0"''','''"docker.io/crazymax/fail2ban" "" "latest"''')),
]
alive=0
for name,path,f in M:
    orig=open(path,encoding='utf-8').read()
    new=f(orig)  # 先算好再写：断言失败时不截断原文件
    open(path,'w',encoding='utf-8',newline='\n').write(new)
    r=subprocess.run([sys.executable,'-m','pytest','-q','--noconftest','-p','no:cacheprovider','tests/test_deploy_image_pulls.py'],capture_output=True,text=True)
    open(path,'w',encoding='utf-8',newline='\n').write(orig)
    reds=sorted({l.split('::')[1].split(' ')[0].split('[')[0] for l in r.stdout.splitlines() if l.startswith('FAILED')})
    if not reds: alive+=1
    print(f"{'RED' if reds else 'ALIVE':5} {name} :: {', '.join(reds) if reds else '-'}")
print("存活变异:",alive)
```

## 补充
（空）
