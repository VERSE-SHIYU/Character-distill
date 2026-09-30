"""锁住镜像的产出与拉取路径（2026-09-30：app / nginx 不再经阿里云）。

**背景。** 阿里云个人版（crpi-…personal.cr.aliyuncs.com）官方注明共享带宽、会限流、不用于生产；
从 GitHub runner 跨境推送 93 次里 56 次挂住，加了逐次超时重试后仍 0/1 落地（挂住的是一个
本来就已存在的层）。而 SZ 从 GHCR 回落拉取 10/10 成功。故：
  - build.yml 不再登录 / 推送阿里云；
  - deploy.yml 的 app / nginx（含回滚）只从 GHCR 拉，postgres / fail2ban 仍阿里云优先
    （Docker Hub 从 SZ 拉会超时，这两个是同区镜像，拉取正常）；
  - pull_image 的 $1（阿里云引用）为空即跳过阿里云；非空时保留 120s 单次超时与失败原因；
  - 本地已有同一镜像（有 digest 时须某条 RepoDigests 整条等于 <repo>@<digest>）就不拉（IfNotPresent），
    本地没有才拉。

行为用例按比例复刻：阿里云超时取 1s、替身挂 30s，断言 10s 内回落。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_BUILD = _REPO / ".github" / "workflows" / "build.yml"
_DEPLOY = _REPO / ".github" / "workflows" / "deploy.yml"


# ── build.yml：不再碰阿里云 ────────────────────────────────────────────────────

def test_build_does_not_touch_aliyun():
    """build job 与全局 env 里不得再出现阿里云（登录、推送、变量都算）。只看 YAML 内容，不看注释。"""
    wf = yaml.safe_load(_BUILD.read_text(encoding="utf-8"))
    assert wf["jobs"]["build"]["steps"], "负控：build job 必须解析出步骤"
    text = yaml.safe_dump({"env": wf.get("env"), "build": wf["jobs"]["build"]}, allow_unicode=True)
    hits = [t for t in ("ALIYUN", "Aliyun", "aliyuncs", "ACR_") if t in text]
    assert hits == [], f"build.yml 仍在引用阿里云：{hits}"


# ── deploy.yml：调用点 ────────────────────────────────────────────────────────

_CONT_RE = re.compile(r"\\\r?\n\s*")
_CALL_RE = re.compile(r'^\s*pull_image\s+"([^"]*)"\s+"([^"]*)"\s+"([^"]*)"\s+"([^"]*)"', re.M)


def _calls() -> list[tuple[str, str, str, str]]:
    flat = _CONT_RE.sub(" ", _DEPLOY.read_text(encoding="utf-8"))
    flat = "\n".join("" if ln.lstrip().startswith("#") else ln for ln in flat.splitlines())
    return _CALL_RE.findall(flat)


def test_every_pull_image_call_is_classified():
    """负控：6 个调用点（app / nginx / postgres / fail2ban / 回滚 app / 回滚 nginx）都解析得到。"""
    calls = _calls()
    assert len(calls) == 6, calls
    assert sum("character-distill-" in c[1] for c in calls) == 4


def test_app_and_nginx_pull_only_from_ghcr():
    """自家镜像（含回滚）一律跳过阿里云：$1 为空，回落仓库是 GHCR。"""
    own = [c for c in _calls() if "character-distill-" in c[1]]
    bad = [c for c in own if c[0] != "" or not c[1].startswith("${GHCR_REPO_PREFIX}/")]
    assert bad == [], f"这些调用仍会先走阿里云或不是 GHCR：{bad}"


def test_third_party_images_stay_aliyun_first():
    """postgres / fail2ban 仍阿里云优先（Docker Hub 从 SZ 拉会超时）。"""
    third = [c for c in _calls() if "character-distill-" not in c[1]]
    assert len(third) == 2
    assert all(c[0].startswith("${ALIYUN_REPO_PREFIX}/") for c in third), third


# ── deploy.yml：pull_image 行为 ───────────────────────────────────────────────

_FUNC_RE = re.compile(r"^ {12}pull_image\(\) \{\n.*?^ {12}\}\n", re.M | re.S)
_HAVE_RE = re.compile(r"^ {12}have_image\(\) \{\n.*?^ {12}\}\n", re.M | re.S)
_ALIYUN = "reg.example/verse-shiyu"
_GHCR = "ghcr.io/verse-shiyu"

# docker 替身：记录每次调用的引用；按前缀决定行为。hang = 挂 30s（远大于测试用的 1s 超时）。
_DOCKER_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [ "$1" = "tag" ]; then exit 0; fi
    if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
      ref="${@: -1}"
      case " $STUB_LOCAL " in *" $ref "*) ;; *) exit 1 ;; esac
      if [ "$3" = "--format" ]; then echo "$STUB_REPODIGESTS"; fi
      exit 0
    fi
    ref="$2"
    echo "$ref" >> "$STUB_LOG"
    case "$ref" in
      reg.example/*) mode="$STUB_ALIYUN" ;;
      *)             mode="$STUB_GHCR" ;;
    esac
    case "$mode" in
      hang) sleep 30; exit 1 ;;
      fail) echo "manifest unknown" >&2; exit 1 ;;
      *)    echo "Status: Downloaded newer image for $ref" ;;
    esac
""")


def _pull_image_src() -> str:
    text = _DEPLOY.read_text(encoding="utf-8")
    funcs, haves = _FUNC_RE.findall(text), _HAVE_RE.findall(text)
    assert len(funcs) == 1, f"deploy.yml 里 pull_image() 应恰好 1 个，实际 {len(funcs)}"
    assert len(haves) == 1, f"deploy.yml 里 have_image() 应恰好 1 个，实际 {len(haves)}"
    return textwrap.dedent(haves[0]) + textwrap.dedent(funcs[0])


def _run(tmp_path: Path, aliyun: str, ghcr: str, first_arg: str | None = None,
         digest: str = "sha256:d", local: str = "", repodigests: str = ""):
    stub = tmp_path / "docker"
    stub.write_text(_DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "calls.log"
    first = f"{_ALIYUN}/character-distill-app:abc" if first_arg is None else first_arg
    script = "\n".join([
        "set -e",
        f'ALIYUN_REPO_PREFIX="{_ALIYUN}"',
        "ALIYUN_PULL_TIMEOUT=1",
        _pull_image_src(),
        f'pull_image "{first}" "{_GHCR}/character-distill-app" "{digest}" "abc"'
        ' || { echo "RESULT=fail"; exit 0; }',
        'echo "RESULT=$PULLED_REGISTRY"',
    ])
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
           "STUB_ALIYUN": aliyun, "STUB_GHCR": ghcr, "STUB_LOG": str(log),
           "STUB_LOCAL": local, "STUB_REPODIGESTS": repodigests}
    t0 = time.monotonic()
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, time.monotonic() - t0, calls


needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="需要 bash + coreutils timeout；合并门是 Linux 上的分支 CI")


@needs_posix
def test_empty_first_arg_goes_straight_to_ghcr(tmp_path):
    proc, _, calls = _run(tmp_path, aliyun="hang", ghcr="ok", first_arg="")
    assert f"RESULT={_GHCR}" in proc.stdout, proc.stdout + proc.stderr
    assert calls == [f"{_GHCR}/character-distill-app@sha256:d"], calls
    assert "阿里云拉取未成功" not in proc.stdout


@needs_posix
def test_aliyun_hang_falls_back_within_timeout(tmp_path):
    proc, elapsed, _ = _run(tmp_path, aliyun="hang", ghcr="ok")
    assert f"RESULT={_GHCR}" in proc.stdout, proc.stdout + proc.stderr
    assert elapsed < 10, f"阿里云挂住必须在超时量级内回落，实际等了 {elapsed:.1f}s"
    assert "exit=124" in proc.stdout, "超时必须在日志里留下 exit=124，不能静默回落"


@needs_posix
def test_aliyun_missing_tag_falls_back_and_says_why(tmp_path):
    proc, _, _ = _run(tmp_path, aliyun="fail", ghcr="ok")
    assert f"RESULT={_GHCR}" in proc.stdout
    assert "exit=1" in proc.stdout
    assert "manifest unknown" in proc.stdout + proc.stderr, "阿里云的报错不能被丢进 /dev/null"


@needs_posix
def test_aliyun_ok_stays_on_aliyun_without_notice(tmp_path):
    proc, _, _ = _run(tmp_path, aliyun="ok", ghcr="fail")
    assert f"RESULT={_ALIYUN}" in proc.stdout
    assert "阿里云拉取未成功" not in proc.stdout


@needs_posix
def test_both_registries_fail_returns_nonzero(tmp_path):
    proc, _, _ = _run(tmp_path, aliyun="fail", ghcr="fail")
    assert "RESULT=fail" in proc.stdout


_LOCAL_APP = f"{_GHCR}/character-distill-app:abc"
_LOCAL_PG = f"{_ALIYUN}/postgres:16-alpine"
_SAME = f"{_GHCR}/character-distill-app@sha256:d"


@needs_posix
def test_local_third_party_image_skips_pull(tmp_path):
    """postgres / fail2ban：本地已有阿里云那份 → 不连任何仓库（仓库挂了也照常部署）。"""
    proc, _, calls = _run(tmp_path, aliyun="hang", ghcr="fail", first_arg=_LOCAL_PG, digest="", local=_LOCAL_PG)
    assert calls == [], calls
    assert f"RESULT={_ALIYUN}" in proc.stdout and f"本地已有 {_LOCAL_PG}，跳过拉取" in proc.stdout


@needs_posix
def test_local_own_image_with_same_digest_skips_pull(tmp_path):
    """app / nginx 重部署同一 commit：本地 :tag 的 digest 与目标一致 → 不拉。"""
    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="hang", first_arg="", local=_LOCAL_APP, repodigests=_SAME)
    assert calls == [], calls
    assert f"RESULT={_GHCR}" in proc.stdout and f"本地已有 {_LOCAL_APP}，跳过拉取" in proc.stdout


@needs_posix
def test_local_rollback_image_skips_pull(tmp_path):
    """回滚形态（无 digest，:PREV_SHA）：清理逻辑本就保留上一版 → 不拉。"""
    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="hang", first_arg="", digest="", local=_LOCAL_APP)
    assert calls == [], calls
    assert f"RESULT={_GHCR}" in proc.stdout


@needs_posix
def test_local_image_with_other_digest_is_pulled(tmp_path):
    """本地同名 tag 但 digest 不同 → 不认本地，照常按 digest 拉（两节点仍是同一 sha256）。"""
    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="ok", first_arg="", local=_LOCAL_APP,
                          repodigests=f"{_GHCR}/character-distill-app@sha256:OTHER")
    assert calls == [_SAME], calls
    assert "跳过拉取" not in proc.stdout


@needs_posix
def test_digest_prefix_is_not_a_match(tmp_path):
    """本地只有「以目标 digest 开头」的另一条记录 → 不是同一镜像，必须照常拉（整条比对，不是子串）。"""
    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="ok", first_arg="", local=_LOCAL_APP,
                          repodigests=f"{_GHCR}/character-distill-app@sha256:dOTHER")
    assert calls == [_SAME], calls
    assert "跳过拉取" not in proc.stdout


@needs_posix
def test_digest_found_among_several_repodigests(tmp_path):
    """RepoDigests 有多条（阿里云 + GHCR）时，只要其中一条整条等于目标就算命中。"""
    proc, _, calls = _run(tmp_path, aliyun="fail", ghcr="hang", first_arg="", local=_LOCAL_APP,
                          repodigests=f"{_ALIYUN}/character-distill-app@sha256:x {_SAME}")
    assert calls == [], calls
    assert f"本地已有 {_LOCAL_APP}，跳过拉取" in proc.stdout


def test_no_call_site_uses_latest():
    """「本地有就不拉」对 :latest 不适用，:latest 必须每次拉（Compose 同规则），调用方不得传 latest。
    注意这只拦 latest：16-alpine / 1.1.0 这类会移动的 tag 本地有了也不再更新（已拍板，升级靠手动 pull）。"""
    assert all(c[3] != "latest" for c in _calls()), _calls()


# ── deploy.yml：规模 ──────────────────────────────────────────────────────────

_TIMEOUT_ASSIGN_RE = re.compile(r"^ {12}ALIYUN_PULL_TIMEOUT=(\d+)\s*$", re.M)
_CMD_TIMEOUT_RE = re.compile(r"^  deploy-sz:.*?command_timeout:\s*(\d+)m", re.M | re.S)
_GHCR_APP_MAX_S = 1233    # SZ 从 GHCR 拉 app 实测最长（2026-09 deploy census，10 次 21s～1233s）
_GHCR_NGINX_EST_S = 231   # 未实测：按压缩体积 60.7/324.3 MiB 从 app 最长值等比估算
_OTHER_STEPS_S = 60       # SZ 除拉取外其余步骤（1286s 总长 − 1233s 拉取，run 36397274083）


def test_worst_case_fits_ssh_budget():
    text = _DEPLOY.read_text(encoding="utf-8")
    m = _TIMEOUT_ASSIGN_RE.search(text)
    assert m, "deploy.yml 必须在脚本层赋值 ALIYUN_PULL_TIMEOUT=<秒>"
    per_pull = int(m.group(1))
    assert per_pull > 42, "不得低于同区正常拉取实测值 42s"
    aliyun_first = sum(1 for c in _calls()[:4] if c[0])   # 主路径 4 次拉取里仍走阿里云的
    budget = aliyun_first * per_pull + _GHCR_APP_MAX_S + _GHCR_NGINX_EST_S + _OTHER_STEPS_S
    ssh_s = int(_CMD_TIMEOUT_RE.search(text).group(1)) * 60
    assert budget <= ssh_s - 120, f"最坏 {budget}s，command_timeout {ssh_s}s，余量不足 120s"
