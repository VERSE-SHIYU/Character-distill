"""锁住三处探针的**行为**：Dockerfile 的 HEALTHCHECK、deploy.yml 的每个区域（缺陷 41）。

**为什么要锁**：探针回退到 `/api/health` 是**静默的** —— 没有任何运行时反馈。容器照样
healthy、部署照样成功、脚本照样往下走，只是「库答不上话」这件事再也没人看。这类回退
不会红、不会报错，只会让判据变成装饰；故它必须由一条用例钉住，而不是靠评审记得。

**身份按行为判定，不按路径形状判定。** 「就绪 = 同族里更长的那条 / 存活是就绪的前缀」
是拿**形状**当身份：改个名、加一层前缀、多一条同族端点，都能让它悄悄指错地方而照样绿
（§四：判据不得建立在「看起来像」之上）。这里改成行为判定：对真 app 挂一个 ping 必抛的
存储，逐个目标发无 token 请求 —— **库坏时还答 2xx 的那个就不是就绪探针**，路径长什么样
都不影响结论。

**只认真实调用，不认字符串出现。** 区域从所有 `deploy-*` job 现取（不写死 SZ/SG）；
探针路径只从 `<X>_gate "<阶段>" "<路径>"` 的**调用行**里取、按调用顺序排列 —— 注释、
echo、失败文案里出现的路径一律不算。上一版按「这一区里出现过哪些路径字面量」取数，
于是把整条调用删掉、只留注释，它照样数得出来（B-10 的形态）：判据看着在管，其实漏着。

**负控**：两个解析器都必须真解析出非空结果，否则空集换来的断言恒真；再挂一个 ping
正常的存储，断言所有目标都答 2xx —— 排除「目标本身就是 404/401」换来的假红。

**变异（B-6..B-14，驱动在 `tests/perf/ping_mutations.py`）**：
  - B-6  Dockerfile 的 HEALTHCHECK 换回存活路径
  - B-7  删掉 SG 就绪那一段的调用
  - B-8  两段顺序颠倒
  - B-9  解析器恒返回空
  - B-10 删掉 SG 存活检查的**调用行**（注释与文案保留）
  - B-11 SG 的存活检查改探就绪路径
  - B-12 两个区域的 gate 都把 curl 写成固定路径（不再用参数）
  - B-13 就绪端点不调 ping、直接回 ready
  - B-14 新增一个只调用一次 gate 的 `deploy-xx` job
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import route_facts

_REPO = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO / "Dockerfile"
_DEPLOY = _REPO / ".github" / "workflows" / "deploy.yml"

_DOCKER_URL_RE = re.compile(r"https?://[^\s'\"`)]+")
_CONTINUATION_RE = re.compile(r"\\\r?\n\s*")
# job 名只匹配顶格两空格的 key，故 `needs: [.. deploy-sg ..]` 那类引用不会被算成区域。
_JOB_RE = re.compile(r"^  (deploy-[a-z0-9-]+):\s*$", re.M)
# gate 定义：12 空格缩进的 `<X>_gate() {` 到同缩进的 `}`。
_GATE_DEF_RE = re.compile(
    r"^ {12}([A-Za-z_][A-Za-z0-9_]*)_gate\(\) \{(?P<body>.*?)^ {12}\}", re.M | re.S)
# 调用行：`if ! <X>_gate "阶段" "路径"` —— 两个引号参数缺一不可，定义行（`()` 开头）匹配不上。
_GATE_CALL_RE = re.compile(
    r'^\s*(?:if\s+!\s+)?([A-Za-z_][A-Za-z0-9_]*)_gate\s+"([^"]*)"\s+"([^"]*)"', re.M)
# gate 体内「从 $2 取路径」的那个变量名。
_PATH_PARAM_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="?\$2"?')
_CURL_RE = re.compile(r"\bcurl\b[^\n;]*")
_LITERAL_PATH_RE = re.compile(r"/api/[\w/-]*")


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


def _normalize_deploy(text: str) -> str:
    """接上续行、并把整行注释抹成空行。

    抹注释是「只认调用」的前提：失败文案与注释里到处是 `/api/health`，留着它们就等于
    又把「字符串出现过」当成了「真的探过」。空行而非删除 —— 行号与切片位置保持不变。
    """
    flat = _CONTINUATION_RE.sub(" ", text)
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in flat.splitlines())


def _deploy_regions(text: str) -> dict[str, dict]:
    """`{区域: {gate, defs, targets, path_param, curl, literals}}` —— 全部来自真实调用。

    `targets` 按调用行出现次序排列 —— 次序本身是判据的一部分（存活在前），故不能是集合。
    """
    norm = _normalize_deploy(text)
    marks = list(_JOB_RE.finditer(norm))
    out: dict[str, dict] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(norm)
        chunk = norm[m.end():end]

        defs = list(_GATE_DEF_RE.finditer(chunk))
        gate = defs[0].group(1) if len(defs) == 1 else None
        gate_body = defs[0].group("body") if len(defs) == 1 else None

        calls = [(c.group(1), c.group(3)) for c in _GATE_CALL_RE.finditer(chunk)]
        param = _PATH_PARAM_RE.search(gate_body).group(1) if gate_body else None
        out[m.group(1)] = {
            "gate": gate,
            "defs": len(defs),
            "targets": [path for (name, path) in calls if name == gate],
            "path_param": param,
            "curl": _CURL_RE.findall(gate_body) if gate_body else [],
            "literals": _LITERAL_PATH_RE.findall(gate_body) if gate_body else [],
        }
    return out


def _regions() -> dict[str, dict]:
    return _deploy_regions(_DEPLOY.read_text(encoding="utf-8"))


# ── 探针：对真 app 发无 token 请求，库好/库坏两种形态 ────────────────────────────

class _Pinger:
    """只有 ping 的替身：`exc` 为 None 时静默成功，否则必抛。"""

    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc

    async def ping(self) -> None:
        if self._exc is not None:
            raise self._exc


_BROKEN = RuntimeError("storage is down")
_HEALTHY = None


@pytest.fixture
def probe(monkeypatch):
    """`probe(exc, path) -> status_code`，请求**不带** Authorization。

    换的是 `deps` 的**单例**而不是 `dependency_overrides`：`Depends(get_storage)` 与端点
    里直接调 `get_storage()` 两种写法都读那个全局，故这一处替换两种写法都覆盖得住
    （override 只盖前者）。真 app 是必要的：`PUBLIC_PATHS` 的判定在它的中间件里。
    """
    import deps

    client = TestClient(route_facts.app())

    def _probe(exc: BaseException | None, path: str) -> int:
        monkeypatch.setattr(deps, "_storage", _Pinger(exc))
        return client.get(path).status_code

    return _probe


def _all_targets() -> set[str]:
    targets = _dockerfile_health_targets(_DOCKERFILE.read_text(encoding="utf-8"))
    for info in _regions().values():
        targets |= set(info["targets"])
    return targets


# ── 负控：解析器与先验探针 ────────────────────────────────────────────────────

def test_parsers_see_at_least_one_target():
    """负控：两个解析器都要真看得见东西，否则下面的断言全是空集换来的恒真。"""
    docker = _dockerfile_health_targets(_DOCKERFILE.read_text(encoding="utf-8"))
    assert docker, "Dockerfile 里解析不出任何健康检查目标 —— 解析器瞎了，下面的锁在空转"

    regions = _regions()
    assert regions, "deploy.yml 里解析不出任何 deploy-* 区域"
    for name, info in sorted(regions.items()):
        assert info["targets"], f"{name} 里解析不出任何 gate 调用 —— 解析器瞎了，锁在空转"


def test_all_targets_answer_when_storage_is_healthy(probe):
    """负控：库好时每个目标都得答 2xx —— 排除「目标本身就是 404/401」换来的假红。"""
    bad = {p: code for p in sorted(_all_targets()) if (code := probe(_HEALTHY, p)) // 100 != 2}
    assert not bad, f"库正常时这些目标不答 2xx：{bad} —— 先验探针本身就走不通"


# ── 门长什么样：真在探传进来的路径 ─────────────────────────────────────────────

def test_every_region_has_exactly_one_gate_function():
    """区域自 deploy-* job 现取，不写死 SZ/SG；每个区域必须恰好一个 `<X>_gate()`。"""
    for name, info in sorted(_regions().items()):
        assert info["gate"], (
            f"{name} 里没解析出恰好一个 <X>_gate() 定义（实得 {info['defs']} 个）—— "
            "没有门函数的区域，下面的调用检查无从谈起")


def test_gate_probes_the_path_it_was_given():
    """gate 必须探**传进来的**路径。写死时调用行上传什么都不再生效，两段门退化成一段。"""
    for name, info in sorted(_regions().items()):
        param = info["path_param"]
        assert param, f"{name} 的 gate 里找不到从 $2 取路径的变量（实得 {param!r}）"
        assert not info["literals"], (
            f"{name} 的 gate 函数体里出现了写死的路径 {info['literals']} —— "
            "探针目标这时由函数体决定，调用行失去了意义")
        for line in info["curl"]:
            assert f"${{{param}}}" in line, (
                f"{name} 的 gate 里这条 curl 没走路径参数 ${{{param}}}：{line.strip()}")


# ── 行为：库坏时该红的红、该绿的绿 ────────────────────────────────────────────

def test_dockerfile_targets_go_unhealthy_when_storage_is_broken(probe):
    """库坏时容器必须被判 unhealthy —— 探到 2xx 的那条探针在所有该红的场合都是绿的。"""
    for path in sorted(_dockerfile_health_targets(_DOCKERFILE.read_text(encoding="utf-8"))):
        code = probe(_BROKEN, path)
        assert code // 100 != 2, (
            f"Dockerfile 的 HEALTHCHECK 探 {path!r}，库坏时返回 {code} —— "
            "容器会照样 healthy，这条探针在所有需要它红的场合都是绿的")


def test_every_region_gates_readiness_last(probe):
    """每个区域的**最后一次**调用必须依赖库：库坏时非 2xx。"""
    for name, info in sorted(_regions().items()):
        last = info["targets"][-1]
        code = probe(_BROKEN, last)
        assert code // 100 != 2, (
            f"{name} 的最后一次 gate 调用探 {last!r}，库坏时返回 {code} —— "
            "这个区域在库不可用时不会红")


def test_every_region_gates_liveness_first(probe):
    """每个区域的**第一次**调用必须不依赖库（2xx），且与最后一次不是同一条路径。"""
    for name, info in sorted(_regions().items()):
        first, last = info["targets"][0], info["targets"][-1]
        code = probe(_BROKEN, first)
        assert code // 100 == 2, (
            f"{name} 的第一次 gate 调用探 {first!r}，库坏时返回 {code} —— "
            "第一段是存活检查，不该依赖库")
        assert first != last, (
            f"{name} 的两次 gate 调用探的是同一条路径 {first!r} —— "
            "「app 没起来」与「app 起来了但库连不上」又共用一个信号了")


def test_every_region_calls_gate_at_least_twice():
    """两段门至少两次调用。删掉一整条调用行时，注释与失败文案里还留着路径 —— 不算数。"""
    for name, info in sorted(_regions().items()):
        assert len(info["targets"]) >= 2, (
            f"{name} 只调用了 {len(info['targets'])} 次 gate（{info['targets']}）—— "
            "两段门至少要两次调用")
