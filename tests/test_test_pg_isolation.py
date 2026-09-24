"""测试自身连库的隔离：连的必须是独立的测试 PG，绝不能碰开发库。

守的是 `tests/conftest.py` 顶部那段强制赋值与会话检查 —— 那几行一旦被改回
`setdefault`、或者判据被放宽，本机的 `.env` 就能把测试引到开发库 `charsim` 上去，
而那时的「全绿」证明的是别人的数据没被写坏，不是代码对。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import conftest

_TESTS_DIR = Path(__file__).resolve().parent
_DEV_URL = "postgresql://someone@dev-host:5432/charsim"


@pytest.mark.parametrize("name,expected", [
    ("charsim_test", True),
    ("charsim", False),
    ("charsim_testx", False),
    ("", False),
])
def test_is_test_database(name, expected):
    """库名判据：以 `_test` 结尾才算测试库。"""
    assert conftest.is_test_database(name) is expected


def test_session_guard_aborts_on_non_test_database(monkeypatch):
    """连到非测试库时整场中止 —— 判据接不上会话检查时这条红。

    `pytest.exit` 抛的是 `_pytest.outcomes.Exit`（Exception 的子类，带 `returncode`），
    故接 Exception 再核 returncode，免得把别的异常当成「已经中止了」。
    """
    monkeypatch.setattr(conftest, "_current_database", lambda: "charsim")
    with pytest.raises(Exception) as excinfo:
        conftest.pytest_sessionstart(None)
    assert getattr(excinfo.value, "returncode", None) == 1, (
        f"没有以 exit(returncode=1) 中止：{excinfo.value!r}")
    assert "不是测试库" in str(excinfo.value), (
        f"连到开发库时没有中止：{str(excinfo.value)!r}")


def test_session_guard_aborts_when_pg_unreachable(monkeypatch):
    """测试 PG 连不上时整场中止，且提示怎么把库起起来。"""
    monkeypatch.setattr(conftest, "_current_database", lambda: None)
    with pytest.raises(Exception) as excinfo:
        conftest.pytest_sessionstart(None)
    assert getattr(excinfo.value, "returncode", None) == 1, (
        f"没有以 exit(returncode=1) 中止：{excinfo.value!r}")
    assert "docker-compose.test.yml" in str(excinfo.value), (
        f"中止了但没说怎么起库：{str(excinfo.value)!r}")


_CHILD = """
import json, os
import conftest
print("RESULT " + json.dumps({
    "url": os.environ["DATABASE_URL"],
    "backend": os.environ["STORAGE_BACKEND"],
    "expected": conftest.TEST_DATABASE_URL,
}))
"""


def test_conftest_forces_env_over_worktree_env():
    """外部（本机 `.env`）给的开发库地址盖不动它。

    必须在子进程里测：本进程的 conftest 早就导入完了，改不了已经发生的赋值。
    子进程里剥掉 DATABASE_URL / STORAGE_BACKEND，前者塞开发库地址、后者留空 ——
    回退成 `setdefault` 的话这两个值都会原样活下来，本用例即红。
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "STORAGE_BACKEND", "TEST_DATABASE_URL", "DB_PATH")}
    env["DATABASE_URL"] = _DEV_URL

    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=str(_TESTS_DIR), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"子进程导入 conftest 失败：{proc.stderr}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1]
    got = json.loads(line[len("RESULT "):])

    assert got["url"] == got["expected"], (
        f"conftest 没盖掉外部给的 DATABASE_URL（仍是 {got['url']!r}）—— "
        "工作树 .env 指向开发库时测试就会连上开发库")
    assert got["url"].endswith("/charsim_test"), got["url"]
    assert got["backend"] == "postgres", (
        f"STORAGE_BACKEND 被外部值带走了：{got['backend']!r}")


def test_db_path_points_at_temp_dir_not_repo_data():
    """DB_PATH 必须落在临时目录：还有代码走 SQLite 时也写不进仓库的 data/。"""
    db_path = Path(os.environ["DB_PATH"]).resolve()
    data_dir = (_TESTS_DIR.parent / "data").resolve()
    assert not db_path.is_relative_to(data_dir), (
        f"DB_PATH 落进仓库的 data/ 了：{db_path}")
    assert Path(tempfile.gettempdir()).resolve() in db_path.parents, (
        f"DB_PATH 不在临时目录下：{db_path}")
