"""compose 事实层自己的锁：用**合成**编排文件核对「事实层确实读到了 compose 的有效模型」。

合成文件写在 `tmp_path`，绝不碰仓内编排文件；每条断言恰等于期望值。

**为什么每条都必须存在。** 事实层的价值全在「它读到的是 compose 的有效模型，不是文本」。
下面每条各锁住其中一层变换或一种失效方向 —— 少一条，那层变换就可以悄悄退化（比如退回
原始 YAML 解析）而这里照样全绿，策略层的锁也就跟着对着代理判绿。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import compose_model
from compose_model import COMPOSE_ENV, ComposeFactError, effective_model

pytestmark = COMPOSE_ENV.skipif("compose 事实层用例")

_MODEL_SRC = Path(compose_model.__file__)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "docker-compose.synth.yml"
    p.write_text(text, encoding="utf-8")
    return p


# ── 五层变换各一条 ────────────────────────────────────────────────────────────

def test_short_depends_on_is_normalized(tmp_path):
    """短式 `depends_on: [b]` 在模型里是带 condition 的映射（原始 YAML 里没有 condition）。"""
    p = _write(tmp_path, """
services:
  a:
    image: alpine
    depends_on: [b]
  b:
    image: alpine
""")
    assert effective_model(p)["services"]["a"]["depends_on"] == {
        "b": {"condition": "service_started", "required": True}}


def test_merge_key_is_expanded(tmp_path):
    """`<<: *anchor` 在模型里已展开成实键（原始 YAML 里 healthcheck 下只有 `<<`）。"""
    p = _write(tmp_path, """
x-synth-anchor: &hc
  test: ["CMD-SHELL", "true"]
  interval: 10s
services:
  a:
    image: alpine
    healthcheck:
      <<: *hc
""")
    assert effective_model(p)["services"]["a"]["healthcheck"] == {
        "test": ["CMD-SHELL", "true"], "interval": "10s"}


def test_extends_is_expanded(tmp_path):
    """`extends` 在同文件内已展开（子服务继承到父服务的键）。"""
    p = _write(tmp_path, """
services:
  parent:
    image: alpine
    environment:
      FROM_PARENT: "1"
  child:
    extends:
      service: parent
    environment:
      OWN: "2"
""")
    assert effective_model(p)["services"]["child"]["environment"] == {
        "FROM_PARENT": "1", "OWN": "2"}


def test_disable_true_is_visible(tmp_path):
    """`disable: true` 在模型里可见 —— 它是「这道门被关了」，不是「缺少某个键」。"""
    p = _write(tmp_path, """
services:
  a:
    image: alpine
    healthcheck:
      test: ["CMD-SHELL", "true"]
      disable: true
""")
    assert effective_model(p)["services"]["a"]["healthcheck"]["disable"] is True


def test_host_interpolation_becomes_the_sentinel(tmp_path):
    """`${VAR}` 已被宿主机插值替换成哨兵 —— 模型里看不到 `${VAR}` 本身。"""
    p = _write(tmp_path, """
services:
  a:
    image: alpine
    environment:
      X: "${SYNTH_VAR}"
""")
    assert effective_model(p)["services"]["a"]["environment"]["X"] == "sentinel_SYNTH_VAR"
    assert compose_model.sentinel_values([p]) == frozenset({"sentinel_SYNTH_VAR"})


def test_container_shell_dollar_is_left_alone(tmp_path):
    """`$$VAR` 在模型里仍是 `$$VAR` —— 那是留给容器内 shell 的，不是宿主机的插值。"""
    p = _write(tmp_path, """
services:
  a:
    image: alpine
    healthcheck:
      test: ["CMD-SHELL", "echo $$SYNTH_VAR"]
""")
    assert effective_model(p)["services"]["a"]["healthcheck"]["test"] == [
        "CMD-SHELL", "echo $$SYNTH_VAR"]


# ── 失效方向：一律抛，不返回空值 ──────────────────────────────────────────────

def test_eval_failure_raises_with_command_and_stderr(tmp_path):
    """求值失败必须抛，且异常里带命令与 stderr 原文（不是「出错了」三个字）。"""
    p = _write(tmp_path, "services:\n  a:\n    image: alpine\n    depends_on: [nope]\n")
    with pytest.raises(ComposeFactError) as excinfo:
        effective_model(p)
    msg = str(excinfo.value)
    assert str(p) in msg, msg
    assert 'depends on undefined service "nope"' in msg, msg


def test_missing_cli_raises_instead_of_returning_empty(tmp_path, monkeypatch):
    """调不起 docker 时抛异常 —— 绝不退化成 `{}`（空值 = 静默放行）。"""
    p = _write(tmp_path, "services:\n  a:\n    image: alpine\n")

    def _boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(subprocess, "run", _boom)
    compose_model._effective_model.cache_clear()
    try:
        with pytest.raises(ComposeFactError) as excinfo:
            compose_model.effective_model(p)
    finally:
        compose_model._effective_model.cache_clear()
    assert "docker compose" in str(excinfo.value)


def test_effective_model_is_cached_per_file(tmp_path):
    """同一个文件读第二遍不该再起一次进程（哨兵环境也只写一次）。"""
    p = _write(tmp_path, "services:\n  a:\n    image: alpine\n")
    first = effective_model(p)
    second = effective_model(p)
    assert first is second


# ── 事实层自己的边界：不许认识本仓 ────────────────────────────────────────────

def test_fact_layer_names_no_repo_specifics():
    """事实层源码里不得出现本仓的具体编排文件名、具体服务名（依赖只能自上而下）。

    名单由仓库现状**推导**得出，不是写死的一张表 —— 写死的话，新加一个服务时这条用例会
    继续绿，而事实层已经悄悄认识它了。

    按词边界比，不按子串：服务名 `app` 是 `names.append` 的子串，纯 `in` 会把正常的英文
    单词判成泄漏。
    """
    src = _MODEL_SRC.read_text(encoding="utf-8")
    forbidden = {p.name for p in compose_model.project_files()}
    for p in compose_model.project_files():
        forbidden |= set(effective_model(p).get("services") or {})
    hits = [t for t in sorted(forbidden)
            if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", src)]
    assert hits == []


def test_project_files_is_derived_from_the_glob(tmp_path, monkeypatch):
    """编排文件由命名模式 glob 得出，不是一份写死的清单。"""
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.local.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "other.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(compose_model, "_REPO", tmp_path)
    assert [p.name for p in compose_model.project_files()] == [
        "docker-compose.local.yml", "docker-compose.prod.yml"]


def test_autoload_files_present_flags_the_tools_default_names(tmp_path, monkeypatch):
    """compose 默认会自动加载的名字要被认出来（本仓自己的命名模式不算）。"""
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.override.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(compose_model, "_REPO", tmp_path)
    assert [p.name for p in compose_model.autoload_files_present()] == [
        "docker-compose.override.yml"]
