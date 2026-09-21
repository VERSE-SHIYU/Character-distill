# -*- coding: utf-8 -*-
"""`tests/repo_files.py` 的锁：面取自 **git**，不是目录树。

现场自证 + 合成反例两层（AGENTS §四「形态锁 + 语义用例」）：
  1. 自证 —— 面非空且含本文件，否则下面那条判据（和两条 census 锁）在假绿；
  2. 合成反例 —— 一个 `.gitignore` 覆盖的 `.py` 不许进面。**这条才是判据**，且
     **不依赖现场**：干净 clone（CI）上没有兄弟 worktree，只有它还能判。
"""

from __future__ import annotations

import pathlib
import subprocess

import repo_files

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _git(root: pathlib.Path, *args: str) -> None:
    out = subprocess.run(["git", "-c", "core.quotePath=false", *args],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, f"git {' '.join(args)} 失败：{out.stderr.strip()}"


def test_real_repo_face_is_not_empty_and_carries_itself():
    """自证：面是活的 —— 空面会让所有普查判据恒绿（§四「恒绿的锁与没有锁是一回事」）。

    不写「至少 N 个」那种魔法数字（合法增删文件就会静默绕过它）；本文件永远在、永远叫
    `test_*.py`，面扫不到它一定是取法坏了。
    """
    face = repo_files.repo_py(ROOT)
    me = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()
    assert me in face, f"面里没有本文件（{me}），扫到的共 {len(face)} 条：{face[:10]}…"


def test_face_follows_git_not_the_directory_tree(tmp_path):
    """合成反例：`git` 认不认这条路径，才是它进不进面的唯一依据。

    造一个小仓库：`keep.py` 入库、`new.py` 未入库也未被忽略、`junk/hidden.py` 被
    `.gitignore` 覆盖。面里必须有前两个、必须没有第三个。

    变异：把 `repo_py` 换回 `os.walk` / `Path.rglob`（即本文件存在的理由）→
    `junk/hidden.py` 立刻现身 → 本用例红。**与现场有没有兄弟 worktree 无关**，
    故 CI 上同样可判定；真仓那几百条 `.claude/worktrees/**` 只是同一个病的重症形态。
    """
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("junk/\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "hidden.py").write_text("z = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "keep.py")

    face = repo_files.repo_py(tmp_path)

    assert face == ["keep.py", "new.py"], (
        f"面不等于「索引 ∪ 未被忽略的未跟踪文件」：{face}。"
        "多出 junk/hidden.py = 走的是目录树；少了 new.py = 只认 --cached，"
        "新写的文件在 git add 之前对锁隐形。")
