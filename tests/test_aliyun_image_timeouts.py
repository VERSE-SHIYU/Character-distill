"""锁住阿里云镜像链路的两处「挂住兜底」（2026-09 推送/拉取挂住诊断）。

build.yml：两个阿里云推送步经 nick-fields/retry 做「单次尝试超时 + 重试」。
  锁的是预算关系，不是某个数字：step 的 timeout-minutes 必须严格大于
  max_attempts × timeout_minutes + (max_attempts − 1) × retry_wait_seconds，
  否则 GitHub 会在重试发生前把整步杀掉，重试形同虚设。

deploy.yml：pull_image 的阿里云那支加单次超时，失败时打一行 exit code 再回落。
  行为按比例复刻：超时取 1s、替身挂 30s —— 挂住必须在超时量级内回落，而不是等满。
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

_ALIYUN_PUSH_STEPS = ("Push app image to Aliyun CR", "Push nginx image to Aliyun CR")
_RETRY_USES_RE = re.compile(r"^nick-fields/retry@[0-9a-f]{40}$")


# ── build.yml：重试契约 ────────────────────────────────────────────────────────

def _build_steps() -> dict[str, dict]:
    wf = yaml.safe_load(_BUILD.read_text(encoding="utf-8"))
    steps = wf["jobs"]["build"]["steps"]
    return {s["name"]: s for s in steps if s.get("name") in _ALIYUN_PUSH_STEPS}


def test_both_aliyun_push_steps_found():
    """负控：两个步骤都必须解析得到，否则下面的参数化断言对空集恒真。"""
    assert set(_build_steps()) == set(_ALIYUN_PUSH_STEPS)


@pytest.mark.parametrize("name", _ALIYUN_PUSH_STEPS)
def test_push_step_uses_pinned_retry_and_stays_optional(name):
    step = _build_steps()[name]
    assert _RETRY_USES_RE.match(step.get("uses", "")), (
        f"{name} 必须经 nick-fields/retry 且钉到 40 位 commit，实际 {step.get('uses')!r}")
    assert step.get("continue-on-error") is True, f"{name} 必须保留 continue-on-error（阿里云是可选的）"
    assert "run" not in step


@pytest.mark.parametrize("name", _ALIYUN_PUSH_STEPS)
def test_step_timeout_leaves_room_for_every_attempt(name):
    step = _build_steps()[name]
    w = step["with"]
    attempts = int(w["max_attempts"])
    per_attempt_s = int(w["timeout_minutes"]) * 60
    wait_s = int(w.get("retry_wait_seconds", 10))  # action.yml 的默认值
    assert attempts >= 2, f"{name} max_attempts={attempts}：不重试就等于没改"
    budget_s = attempts * per_attempt_s + (attempts - 1) * wait_s
    step_s = int(step["timeout-minutes"]) * 60
    assert step_s > budget_s, (
        f"{name}: step timeout {step_s}s 必须大于全部尝试的预算 {budget_s}s")


@pytest.mark.parametrize("name", _ALIYUN_PUSH_STEPS)
def test_command_pushes_sha_before_latest(name):
    cmd = _build_steps()[name]["with"]["command"]
    pushes = [ln.strip() for ln in cmd.splitlines() if ln.strip().startswith("docker push")]
    assert len(pushes) == 2, pushes
    assert pushes[0].endswith(":${{ github.sha }}") and pushes[1].endswith(":latest"), pushes


# ── deploy.yml：pull_image 行为 ────────────────────────────────────────────────

_FUNC_RE = re.compile(r"^ {12}pull_image\(\) \{\n.*?^ {12}\}\n", re.M | re.S)
_TIMEOUT_ASSIGN_RE = re.compile(r"^ {12}ALIYUN_PULL_TIMEOUT=(\d+)\s*$", re.M)
_CMD_TIMEOUT_RE = re.compile(r"^  deploy-sz:.*?command_timeout:\s*(\d+)m", re.M | re.S)

_ALIYUN = "reg.example/verse-shiyu"
_GHCR = "ghcr.io/verse-shiyu"

# docker 替身：按引用前缀决定行为。hang = 挂 30s（远大于测试用的 1s 超时）。
_DOCKER_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [ "$1" = "tag" ]; then exit 0; fi
    ref="$2"
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
    funcs = _FUNC_RE.findall(text)
    assert len(funcs) == 1, f"deploy.yml 里 pull_image() 应恰好 1 个，实际 {len(funcs)}"
    return textwrap.dedent(funcs[0])


def _run(tmp_path: Path, aliyun: str, ghcr: str) -> tuple[subprocess.CompletedProcess, float]:
    stub = tmp_path / "docker"
    stub.write_text(_DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)
    script = "\n".join([
        "set -e",
        f'ALIYUN_REPO_PREFIX="{_ALIYUN}"',
        "ALIYUN_PULL_TIMEOUT=1",
        _pull_image_src(),
        f'pull_image "{_ALIYUN}/character-distill-app:abc" "{_GHCR}/character-distill-app" "sha256:d" "abc"'
        ' || { echo "RESULT=fail"; exit 0; }',
        'echo "RESULT=$PULLED_REGISTRY"',
    ])
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
           "STUB_ALIYUN": aliyun, "STUB_GHCR": ghcr}
    t0 = time.monotonic()
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    return proc, time.monotonic() - t0


needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="需要 bash + coreutils timeout；合并门是 Linux 上的分支 CI")


@needs_posix
def test_aliyun_hang_falls_back_within_timeout(tmp_path):
    proc, elapsed = _run(tmp_path, aliyun="hang", ghcr="ok")
    assert f"RESULT={_GHCR}" in proc.stdout, proc.stdout + proc.stderr
    assert elapsed < 10, f"阿里云挂住必须在超时量级内回落，实际等了 {elapsed:.1f}s"
    assert "exit=124" in proc.stdout, "超时必须在日志里留下 exit=124，不能静默回落"


@needs_posix
def test_aliyun_missing_tag_falls_back_and_says_why(tmp_path):
    proc, _ = _run(tmp_path, aliyun="fail", ghcr="ok")
    assert f"RESULT={_GHCR}" in proc.stdout
    assert "exit=1" in proc.stdout
    assert "manifest unknown" in proc.stdout + proc.stderr, "阿里云的报错不能再被丢进 /dev/null"


@needs_posix
def test_aliyun_ok_stays_on_aliyun_without_notice(tmp_path):
    proc, _ = _run(tmp_path, aliyun="ok", ghcr="fail")
    assert f"RESULT={_ALIYUN}" in proc.stdout
    assert "阿里云拉取未成功" not in proc.stdout


@needs_posix
def test_both_registries_fail_returns_nonzero(tmp_path):
    proc, _ = _run(tmp_path, aliyun="fail", ghcr="fail")
    assert "RESULT=fail" in proc.stdout


def test_aliyun_timeouts_fit_ssh_budget():
    """规模：SZ 四次阿里云拉取全部挂到超时 + GHCR app 实测最长 1233s + 其余步骤约 60s，
    必须仍在 deploy-sz 的 command_timeout 之内（数据：2026-09 deploy census）。"""
    text = _DEPLOY.read_text(encoding="utf-8")
    m = _TIMEOUT_ASSIGN_RE.search(text)
    assert m, "deploy.yml 必须顶格（脚本层）赋值 ALIYUN_PULL_TIMEOUT=<秒>"
    per_pull = int(m.group(1))
    assert per_pull > 42, "不得低于同区正常拉取实测值 42s"
    ssh_s = int(_CMD_TIMEOUT_RE.search(text).group(1)) * 60
    assert 4 * per_pull + 1233 + 60 <= ssh_s, (
        f"4×{per_pull}s + 1233s + 60s 超出 command_timeout {ssh_s}s")
