"""事实层的**承重前提**（P-a / P-b）自己那把锁。

**本文件默认禁止真跑 compose** —— 真跑用例一律放进 `test_compose_model.py`。
这里每条用例都把 `_compose` 换成假的，故本文件**没有 docker 也要能跑**（这正是本次修法
的重点：前提不能只在「装了 compose 的机器」上被验）。默认那条「直接炸」的假实现由 autouse
fixture 装上，不靠用例自己记得换 —— 忘了换的用例会去调真的 docker，于是「本机装了什么」
悄悄影响了本该是纯逻辑的判断。

**为什么单独一个文件。** 而 `test_compose_model.py` 有一条**模块级** `pytestmark`
（环境声明 mark），它盖住文件里每一条用例（实测：放进类里也逃不掉）。
留在那个文件里，这几条要么被一起 skip 掉、要么得把模块级 mark 拆成逐条手工清单 ——
后者把「默认要 compose」这个 fail-safe 默认换成一张靠人记得维护的表。故按环境需求分文件：
那个文件默认要 compose，这个文件默认不要，真跑那条归前者管。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import compose_model
from compose_model import ComposeFactError, effective_model, sentinel_values

_FAKE_VERSION = "Docker Compose version v9.9.9 (假探针)\n"


@pytest.fixture(autouse=True)
def _no_real_compose(monkeypatch):
    """**本文件的用例不得真跑 compose**；顺带清缓存。

    探针结果是进程级缓存的，不清就串味（假探针的结论会被下一条读到）。
    """
    def forbidden(args, **kwargs):
        raise AssertionError("本文件的用例不得真跑 compose")

    compose_model.capability_defect.cache_clear()
    compose_model._effective_model.cache_clear()
    monkeypatch.setattr(compose_model, "_compose", forbidden)
    yield
    compose_model.capability_defect.cache_clear()
    compose_model._effective_model.cache_clear()


def _probe_present_env() -> Path:
    """探针写在临时目录里的「存在的那份 env_file」—— 泄漏用例从这里取真实的哨兵值。"""
    return compose_model._tmp_env_dir() / "capability" / compose_model._PROBE_PRESENT


def _ok() -> str:
    """一条干净探针的 stdout（不含哨兵）。"""
    return '{"services": {"probe": {"image": "scratch"}}}\n'


def _raises(message: str):
    def probe():
        raise ComposeFactError(message)
    return probe


def _install_probe(monkeypatch, *, control, missing, leaking):
    """把 `_compose` 换成假的：`version` 单独走，三条探针各按 `-f` 的文件名分派。

    按**合成文件的文件名**区分 C / P-a / P-b，不是按调用次数 —— 按次数就是在数「实现
    碰巧调了几次」，实现一改测试就假绿。

    **三条都要显式给。** 默认值会变成一种「没写就当干净」的隐式放行：漏给哪条，哪条就
    恒绿 —— 那正是本层的用例最不该有的形状。
    """
    by_name = {
        compose_model._PROBE_CONTROL: control,
        compose_model._PROBE_MISSING: missing,
        compose_model._PROBE_LEAKING: leaking,
    }

    def fake(args, **kwargs):
        if args == ["version"]:
            return _FAKE_VERSION
        if args[:1] == ["-f"]:
            name = Path(args[1]).name
            if name in by_name:
                return by_name[name]()
        raise AssertionError(f"这条用例不该发生别的 compose 调用：{args}")

    monkeypatch.setattr(compose_model, "_compose", fake)


def _synth(tmp_path: Path) -> Path:
    p = tmp_path / "docker-compose.synth.yml"
    p.write_text("services:\n  a:\n    image: alpine\n", encoding="utf-8")
    return p


def _leaking_probe(leaked: dict[str, str]):
    """P-b 探针：回显那份带哨兵的 env_file —— 模拟 v2 的泄漏。

    **在这个时刻读文件**，不能在这条用例开头读：那份文件是 `capability_defect()` 每次
    现写的，开头读到的是**上一条用例留下的旧哨兵** —— 拿旧值去断言「新消息里没有它」
    恒真，真泄漏也照样绿（本用例第一版就是这么写错的，被 M5 变异照出来）。
    """
    def probe() -> str:
        text = _probe_present_env().read_text(encoding="utf-8")
        leaked["value"] = text.split("=", 1)[1].strip()
        return text
    return probe


# ── 负控：前提不成立时必须红，且要说对是哪一条 ────────────────────────────────

def test_p_a_failure_blocks_the_model_and_names_p_a(tmp_path, monkeypatch):
    """P-a 不成立（env_file 缺文件 → 非零退出）时，求模型必须抛，且**报的是 P-a**。

    这条的对手是旧形态：CI 上事实层静默失效，报出来的是 `.env not found` 的原文堆栈 ——
    真凶（本机 compose 不满足前置条件）被一个症状盖住。
    """
    _install_probe(
        monkeypatch, control=_ok,
        missing=_raises(
            "`docker compose -f /tmp/_capability_missing.yml ... config` 退出码 1；"
            "stderr：stat /tmp/absent.env: no such file or directory"),
        leaking=_ok)

    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(_synth(tmp_path))
    msg = str(excinfo.value)
    assert compose_model._P_A_REASON in msg, msg
    assert compose_model._P_B_REASON not in msg, msg


def test_p_b_leak_blocks_the_model_and_never_echoes_the_token(tmp_path, monkeypatch):
    """P-b 不成立（env_file 的值进了输出）时必须抛，说 P-b，且**消息里不含那个值**。

    消息里带上哨兵，等于把「本层要防的东西」抄进了异常 —— 异常会进日志、进 CI 输出。
    """
    leaked: dict[str, str] = {}
    _install_probe(monkeypatch, control=_ok, missing=_ok,
                   leaking=_leaking_probe(leaked))

    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(_synth(tmp_path))
    msg = str(excinfo.value)
    token = leaked.get("value", "")
    assert token, "探针应当先写出一份带哨兵的 env_file（本用例读不到就说明它没跑）"
    assert compose_model._P_B_REASON in msg, msg
    assert compose_model._P_A_REASON not in msg, msg
    assert token not in msg, "异常消息里不得出现哨兵值本身"


def test_both_defects_are_reported_together(tmp_path, monkeypatch):
    """**F1 的对手**：C 成功、P-a 失败、P-b 也回显 token（v2.38.2 的真实情况）。

    两条必须**都报**。原先那版在 P-a 那一步就 `return` 了，于是 v2 机器上永远只看得见
    P-a —— 而**那台机器恰恰也在泄漏**，泄漏被前一条遮住。两条成因共用一个信号，
    等于少了一整条守卫。
    """
    leaked: dict[str, str] = {}
    _install_probe(
        monkeypatch, control=_ok,
        missing=_raises(
            "退出码 1；stderr：stat /tmp/absent.env: no such file or directory"),
        leaking=_leaking_probe(leaked))

    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(_synth(tmp_path))
    msg = str(excinfo.value)
    token = leaked.get("value", "")
    assert token, "探针应当先写出一份带哨兵的 env_file（本用例读不到就说明它没跑）"
    assert compose_model._P_A_REASON in msg, msg
    assert compose_model._P_B_REASON in msg, msg
    assert token not in msg, "异常消息里不得出现哨兵值本身"


def test_broken_control_is_its_own_reason(tmp_path, monkeypatch):
    """**F2 的对手**：对照运行就不成立（合成文件写错、命令形状不被支持…）。

    此时「非零退出」**不等于** P-a —— 后面两条性质一个字都不许判，只说探针不成立。
    并且就此打住：C 都跑不通，再跑 P-a/P-b 得到的结论没有任何意义。
    """
    def _unexpected():
        raise AssertionError("对照运行已经失败，不该再跑 P-a/P-b 探针")

    _install_probe(
        monkeypatch,
        control=_raises("退出码 2；stderr：services.probe.env_file must be a list"),
        missing=_unexpected, leaking=_unexpected)

    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(_synth(tmp_path))
    msg = str(excinfo.value)
    assert compose_model._PROBE_BROKEN_REASON in msg, msg
    assert compose_model._P_A_REASON not in msg, msg
    assert compose_model._P_B_REASON not in msg, msg


def test_sentinel_values_is_guarded_too(tmp_path, monkeypatch):
    """第二个入口同样要守 —— 只守 `effective_model` 的话，策略层走哨兵那条路照样拿到东西。"""
    _install_probe(monkeypatch, control=_ok,
                   missing=_raises("stderr：stat absent.env: no such file or directory"),
                   leaking=_ok)
    with pytest.raises(ComposeFactError) as excinfo:
        sentinel_values([_synth(tmp_path)])
    assert compose_model._P_A_REASON in str(excinfo.value)


def test_no_cli_is_a_third_distinct_reason(tmp_path, monkeypatch):
    """四种成因不得共用一句话：没有 CLI 与「有 CLI 但前提不成立」要分得开。"""
    def fake(args, **kwargs):
        raise ComposeFactError("调不起 docker compose：FileNotFoundError(2, ...)")

    monkeypatch.setattr(compose_model, "_compose", fake)
    defect = compose_model.capability_defect()
    assert defect is not None
    assert compose_model._NO_CLI_REASON in defect, defect
    assert compose_model._PROBE_BROKEN_REASON not in defect, defect
    assert compose_model._P_A_REASON not in defect, defect
    assert compose_model._P_B_REASON not in defect, defect


# ── 正控：探针干净时必须放行 ──────────────────────────────────────────────────

def test_clean_probe_passes_and_reports_no_defect(monkeypatch):
    """正控：三条探针都干净（exit 0、输出里没有哨兵）→ `None`。

    没有这条，上面的负控全可以在「永远返回一个原因」的实现下通过 —— 那样本层就恒不可用了。
    """
    _install_probe(monkeypatch, control=_ok, missing=_ok, leaking=_ok)
    assert compose_model.capability_defect() is None
