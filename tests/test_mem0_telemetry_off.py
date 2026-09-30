"""mem0 遥测在镜像里默认关（Dockerfile `ENV MEM0_TELEMETRY=...`）。

mem0 默认把每次记忆读写的元数据发往它官方的 PostHog（us.i.posthog.com），本项目用不上，
境内节点还构成出境。两条断言：

1. Dockerfile 里设了这个变量，且按 mem0 **自己的**解析规则读出来是关 —— 判据不自抄一份
   「哪些字符串算 false」，而是在子进程里 import mem0，看它得到的布尔值；
2. 在这个取值下，遥测客户端确实不建（``posthog is None``），``capture_event`` 发不出去。

子进程跑：mem0 在 import 时读环境变量，同进程里别的用例可能已经 import 过它。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_env(name: str) -> str | None:
    """Dockerfile 里最后一次 ``ENV name=value`` 的值（没有返回 None）。"""
    value = None
    for line in (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(rf"\s*ENV\s+{re.escape(name)}=(\S+)\s*$", line)
        if m:
            value = m.group(1)
    return value


def test_image_turns_mem0_telemetry_off():
    value = _dockerfile_env("MEM0_TELEMETRY")
    assert value is not None, "Dockerfile 没有 ENV MEM0_TELEMETRY=...，镜像里 mem0 遥测会默认开启"

    probe = (
        "import mem0.memory.telemetry as t\n"
        "tel = t.AnonymousTelemetry()\n"
        "print(t.MEM0_TELEMETRY, tel.posthog is None)\n"
    )
    env = {**os.environ, "MEM0_TELEMETRY": value}
    out = subprocess.run(
        [sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-2:] == ["False", "True"], (
        f"MEM0_TELEMETRY={value!r} 在 mem0 里没有被解析成关闭（输出 {out.stdout!r}）"
    )
