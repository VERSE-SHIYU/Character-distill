"""事实层的**承重前提**（P-a / P-b）自己那把锁。

**为什么单独一个文件。** 这些用例**不挂 `COMPOSE_ENV`** —— 它们把 `_compose` 整个换成假的，
没有 docker 也要真跑（这正是本次修法的重点：前提不能只在「装了 compose 的机器」上被验）。
而 `test_compose_model.py` 有一条**模块级** `pytestmark = COMPOSE_ENV.skipif(...)`，它盖住
文件里每一条用例（实测：放进类里也逃不掉）。留在那个文件里，这四条要么被一起 skip 掉、
要么得把模块级 mark 拆成逐条手工清单 —— 后者把「默认要 compose」这个 fail-safe 默认换成
一张靠人记得维护的表。故按环境需求分文件：那个文件默认要 compose，这个文件默认不要。

T5 是唯一一条真跑 compose 的（挂 `COMPOSE_ENV`）：它是「CI 上钉的那个版本确实满足前提」的
直接证据 —— 没有它，钉版本只是注释里的一个数字。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import compose_model
from compose_model import COMPOSE_ENV, ComposeFactError, effective_model, sentinel_values

_FAKE_VERSION = "Docker Compose version v9.9.9 (假探针)\n"


@pytest.fixture(autouse=True)
def _fresh_capability():
    """每条用例前清缓存 —— 探针结果是进程级缓存的，不清就串味（假探针的结论会被下一条读到）。"""
    compose_model.capability_defect.cache_clear()
    compose_model._effective_model.cache_clear()
    yield
    compose_model.capability_defect.cache_clear()
    compose_model._effective_model.cache_clear()


def _probe_present_env() -> Path:
    """探针写在临时目录里的「存在的那份 env_file」—— 泄漏用例从这里取真实的哨兵值。"""
    return compose_model._tmp_env_dir() / "capability" / compose_model._PROBE_PRESENT


def _install_probe(monkeypatch, probe):
    """把 `_compose` 换成假的：`version` 与探针调用各走各的，其余调用一律炸。

    按**参数**区分探针调用与其它调用（探针的 `-f` 指向 `_PROBE_FILE`），不是按调用次数 ——
    按次数就是在数「实现碰巧调了几次」，实现一改测试就假绿。
    """
    def fake(args, **kwargs):
        if args == ["version"]:
            return _FAKE_VERSION
        if args[:1] == ["-f"] and str(args[1]).endswith(compose_model._PROBE_FILE):
            return probe()
        raise AssertionError(f"这条用例不该发生别的 compose 调用：{args}")

    monkeypatch.setattr(compose_model, "_compose", fake)


def _synth(tmp_path: Path) -> Path:
    p = tmp_path / "docker-compose.synth.yml"
    p.write_text("services:\n  a:\n    image: alpine\n", encoding="utf-8")
    return p


# ── 负控：前提不成立时必须红，且要说对是哪一条 ────────────────────────────────

def test_p_a_failure_blocks_the_model_and_names_p_a(tmp_path, monkeypatch):
    """P-a 不成立（env_file 缺文件 → 非零退出）时，求模型必须抛，且**报的是 P-a**。

    这条的对手是旧形态：CI 上事实层静默失效，报出来的是 `.env not found` 的原文堆栈 ——
    真凶（本机 compose 不满足前置条件）被一个症状盖住。
    """
    def probe():
        raise ComposeFactError(
            "`docker compose -f /tmp/_capability_probe.yml ... config` 退出码 1；"
            "stderr：stat /tmp/absent.env: no such file or directory")

    _install_probe(monkeypatch, probe)
    p = _synth(tmp_path)
    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(p)
    msg = str(excinfo.value)
    assert compose_model._P_A_REASON in msg, msg
    assert compose_model._P_B_REASON not in msg, msg


def test_p_b_leak_blocks_the_model_and_never_echoes_the_token(tmp_path, monkeypatch):
    """P-b 不成立（env_file 的值进了输出）时必须抛，说 P-b，且**消息里不含那个值**。

    消息里带上哨兵，等于把「本层要防的东西」抄进了异常 —— 异常会进日志、进 CI 输出。
    """
    leaked: dict[str, str] = {}

    def probe():
        # 模拟 v2：env_file 存在，compose 把它的值原样灌进模型输出。
        #
        # **在这个时刻读文件**，不能在这条用例开头读：那份文件是 `capability_defect()`
        # 每次现写的，开头读到的是**上一条用例留下的旧哨兵** —— 拿旧值去断言「新消息里
        # 没有它」恒真，真泄漏也照样绿（本用例第一版就是这么写错的，被 M5 变异照出来）。
        text = _probe_present_env().read_text(encoding="utf-8")
        leaked["value"] = text.split("=", 1)[1].strip()
        return text

    _install_probe(monkeypatch, probe)

    p = _synth(tmp_path)
    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(p)
    msg = str(excinfo.value)
    token = leaked.get("value", "")
    assert token, "探针应当先写出一份带哨兵的 env_file（本用例读不到就说明它没跑）"
    assert compose_model._P_B_REASON in msg, msg
    assert compose_model._P_A_REASON not in msg, msg
    assert token not in msg, "异常消息里不得出现哨兵值本身"


def test_sentinel_values_is_guarded_too(tmp_path, monkeypatch):
    """第二个入口同样要守 —— 只守 `effective_model` 的话，策略层走哨兵那条路照样拿到东西。"""
    def probe():
        raise ComposeFactError("stderr：stat absent.env: no such file or directory")

    _install_probe(monkeypatch, probe)
    with pytest.raises(ComposeFactError) as excinfo:
        sentinel_values([_synth(tmp_path)])
    assert compose_model._P_A_REASON in str(excinfo.value)


def test_no_cli_is_a_third_distinct_reason(tmp_path, monkeypatch):
    """三种成因不得共用一句话：没有 CLI 与「有 CLI 但前提不成立」要分得开。"""
    def fake(args, **kwargs):
        raise ComposeFactError("调不起 docker compose：FileNotFoundError(2, ...)")

    monkeypatch.setattr(compose_model, "_compose", fake)
    defect = compose_model.capability_defect()
    assert defect is not None
    assert compose_model._NO_CLI_REASON in defect, defect
    assert compose_model._P_A_REASON not in defect, defect
    assert compose_model._P_B_REASON not in defect, defect


# ── 正控：探针干净时必须放行 ──────────────────────────────────────────────────

def test_clean_probe_passes_and_reports_no_defect(monkeypatch):
    """正控：探针干净（exit 0、输出里没有哨兵）→ `None`。

    没有这条，上面的负控全可以在「永远返回一个原因」的实现下通过 —— 那样本层就恒不可用了。
    """
    def probe():
        return '{"services": {"probe": {"image": "scratch", "env_file": []}}}\n'

    _install_probe(monkeypatch, probe)
    assert compose_model.capability_defect() is None


# ── 真跑：CI 上钉的那个版本确实满足前提（钉版本不能只写在注释里） ──────────────

@COMPOSE_ENV.skipif("承重前提的真跑用例")
def test_real_compose_satisfies_both_preconditions():
    """真跑一遍：本机 compose 两条前提都满足（CI 上这是「钉住的版本够格」的直接证据）。"""
    compose_model.capability_defect.cache_clear()
    assert compose_model.capability_defect() is None
