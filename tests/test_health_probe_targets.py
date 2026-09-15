"""锁住三处探针的目标：Dockerfile、deploy.yml 两个区域（缺陷 41 的 B 轮）。

**为什么要锁**：探针回退到 `/api/health` 是**静默的** —— 没有任何运行时反馈。容器照样
healthy、部署照样成功、脚本照样往下走，只是「库答不上话」这件事再也没人看。这类回退
不会红、不会报错，只会让判据变成装饰；故它必须由一条用例钉住，而不是靠评审记得。

**路径不写字面量**：就绪路径从 `route_facts` 的真实 app 里取（存活与就绪是同一族的两个
端点，存活是就绪的前缀），再拿去比对 Dockerfile 与 deploy.yml 里的目标。字面量写在这边
等于把「三处探的是同一个东西」偷换成「三处抄了同一个字符串」。

**负控**：两个解析器都必须真看得见至少一个目标 —— 解析写歪换来的空集会让下面的断言
恒真，那种绿与「三处一致」的绿长得一样（§四）。

**变异（B-6..B-9，驱动在 `tests/perf/ping_mutations.py`）**：
  - B-6 Dockerfile 换回存活路径 → `test_dockerfile_healthcheck_probes_readiness`
  - B-7 删掉 SG 的第二段 → `test_both_regions_probe_liveness_then_readiness`（点名 SG）
  - B-8 两段顺序颠倒 → 同上
  - B-9 让解析器恒返回空 → `test_parsers_see_at_least_one_target`
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import route_facts

_REPO = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO / "Dockerfile"
_DEPLOY = _REPO / ".github" / "workflows" / "deploy.yml"

# 健康族的路径字面量。deploy.yml 里探针路径是**变量**传进门的
# （`curl -sf "http://localhost:7860${SZ_path}"`），故这条正则找的是调用点那个字面量。
_HEALTH_PATH_RE = re.compile(r"/api/health(?:/[a-z]+)?")
_DOCKER_URL_RE = re.compile(r"https?://[^\s'\"`)]+")
_CONTINUATION_RE = re.compile(r"\\\r?\n\s*")
_REGION_RE = re.compile(r"^  (deploy-[a-z]+):\s*$", re.M)
_GATE_DEF_RE = re.compile(r"^ {12}(SZ|SG)_gate\(\) \{(?P<body>.*?)^ {12}\}", re.M | re.S)


# ── 就绪路径：从真实 app 取，不写字面量 ────────────────────────────────────────

def _health_family_paths() -> set[str]:
    """真实 app 上所有 `/api/health*` 路径（从模块级 router 枚举）。"""
    return {path for (path, _method) in route_facts.enumerate_routes()
            if path.startswith("/api/health")}


def _liveness_and_readiness() -> tuple[str, str]:
    """`(存活, 就绪)`：同族里短的是存活、长的是就绪，且存活必须是就绪的前缀。

    前缀关系是结构事实（就绪是存活加一段后缀），不是命名约定 —— 若哪天两者不再同族，
    这里直接红，而不是继续拿「最长的那个」当就绪。
    """
    paths = _health_family_paths()
    assert len(paths) == 2, (
        f"健康族里应当正好两条端点（存活 + 就绪），实得 {sorted(paths)}。"
        "多一条少一条都会让「哪条是就绪」这个推导失去依据。")
    live = min(paths, key=len)
    ready = max(paths, key=len)
    assert live != ready and ready.startswith(live), (
        f"就绪路径不是存活路径加后缀：存活 {live!r} / 就绪 {ready!r} —— 推导的前提破了")
    return live, ready


# ── 解析器 ────────────────────────────────────────────────────────────────────

def _dockerfile_health_targets(text: str) -> set[str]:
    """Dockerfile 里 HEALTHCHECK 探的路径集合（先接上反斜杠续行）。"""
    out: set[str] = set()
    for line in _CONTINUATION_RE.sub(" ", text).splitlines():
        if not line.strip().upper().startswith("HEALTHCHECK"):
            continue
        for url in _DOCKER_URL_RE.findall(line):
            out.add("/" + url.split("/", 3)[3].split("?")[0] if url.count("/") >= 3 else url)
    return out


def _deploy_probe_targets(text: str) -> dict[str, list[str]]:
    """`{区域: [探针路径，按出现次序]}` —— 按 deploy-* job 切段后各取字面量。"""
    out: dict[str, list[str]] = {}
    marks = list(_REGION_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = _HEALTH_PATH_RE.findall(text[m.end():end])
    return out


def _gate_bodies(text: str) -> dict[str, str]:
    """`{区域名: gate 函数体}` —— 两个区域的门必须逐字同形（差异只能来自区域名）。"""
    return {m.group(1): m.group("body") for m in _GATE_DEF_RE.finditer(text)}


# ── 锁 ────────────────────────────────────────────────────────────────────────

def test_parsers_see_at_least_one_target():
    """负控：两个解析器都要真看得见东西，否则下面的断言全是空集换来的恒真。"""
    docker = _dockerfile_health_targets(_DOCKERFILE.read_text(encoding="utf-8"))
    assert docker, "Dockerfile 里解析不出任何健康检查目标 —— 解析器瞎了，下面的锁在空转"

    regions = _deploy_probe_targets(_DEPLOY.read_text(encoding="utf-8"))
    assert regions, "deploy.yml 里解析不出任何 deploy-* 区域"
    empty = [r for r, paths in regions.items() if not paths]
    assert not empty, f"这些区域里解析不出任何探针路径：{empty}"


def test_dockerfile_healthcheck_probes_readiness():
    """容器的 HEALTHCHECK 必须探就绪 —— 探存活则容器在库连不上时照样 healthy。"""
    live, ready = _liveness_and_readiness()
    targets = _dockerfile_health_targets(_DOCKERFILE.read_text(encoding="utf-8"))
    assert ready in targets, (
        f"Dockerfile 的 HEALTHCHECK 没探就绪路径 {ready!r}，实得 {sorted(targets)}")
    assert live not in targets, (
        f"Dockerfile 的 HEALTHCHECK 探的是存活路径 {live!r} —— 容器会在库不可用时照样 "
        "healthy，没有任何运行时反馈")


def test_both_regions_probe_liveness_then_readiness():
    """两个区域都：先探存活、后探就绪。顺序颠倒或少了第二段都红。"""
    live, ready = _liveness_and_readiness()
    regions = _deploy_probe_targets(_DEPLOY.read_text(encoding="utf-8"))

    for name, paths in sorted(regions.items()):
        assert live in paths, f"{name} 没有探存活路径 {live!r}（实得 {paths}）"
        assert ready in paths, (
            f"{name} 没有探就绪路径 {ready!r} —— 库不可用时这个区域不会红："
            f"实得 {paths}")
        assert paths.index(live) < paths.index(ready), (
            f"{name} 把两段顺序弄反了（先就绪、后存活）：{paths}。"
            "存活在前是有意义的 —— 「app 没起来」与「app 起来了但库连不上」要能分开报")


def test_both_regions_use_the_same_gate():
    """两处实现逐字同形，差异只能来自区域名 —— 否则「两个区域同形」只是一句话。"""
    bodies = _gate_bodies(_DEPLOY.read_text(encoding="utf-8"))
    assert set(bodies) == {"SZ", "SG"}, (
        f"两个区域的探针函数没能都解析出来：{sorted(bodies)}（解析失败与实现不一致共用一个红）")
    sz = bodies["SZ"].replace("SZ", "")
    sg = bodies["SG"].replace("SG", "")
    assert sz == sg, "两个区域的探针函数除区域名外还不一致，改一处不会红"
