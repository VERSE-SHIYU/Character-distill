# -*- coding: utf-8 -*-
"""仓库自有 `.py` 的**唯一**取法：问 `git`，不遍历文件系统。

**它防的是什么。** `os.walk` / `Path.rglob` 走的是**目录树**，而目录树里躺着的并不都是
本仓代码。最要命的是 `.claude/worktrees/<兄弟 worktree>/` —— 同一份源码的第二、第三份
拷贝，且随别的会话实时增删。于是普查结果开始取决于**别的会话今天开了几个 worktree**：
仓库根下挂 3 个 worktree 时，两条 census 锁各自多出几百条 offender，全红；把 worktree
删掉又全绿。红绿不再由被测仓库决定，这种锁**看着在守、其实在报天气**。

被它误收的还有一批本地产物：`e2e/scratch/**`、`data/eval_scratch/**`、
`scripts/import_shiyu.py`（`.gitignore` 第 282-283 行**点名**忽略）等上百个 `.py`。
它们同样不该进任何普查。

**取法。** `git ls-files --cached --others --exclude-standard '*.py'`，两半各管一件事：

  - `--cached`：索引里已入库的；
  - `--others --exclude-standard`：**工作区里尚未 `git add`、且不被 `.gitignore` 覆盖**的
    文件。后半个不是可有可无 —— 只认 `--cached` 时，新写的文件在 `git add` 之前对锁是
    **隐形**的，而「新写一个该被看见的文件」恰恰是这两条 census 锁要拦的动作
    （同一取向与实测见 `tests/test_llm_access_gate.py` 的 `_production_py`：L12 在
    `web/llm_gate.py` 未入库时读数是 3/2、真值是 1/1，锁当时看着是绿的）。

排除规则因此只有**一份** —— `.gitignore`。锁里不再各留一份 `_PRUNED_DIRS`：两份名单
迟早不相等，而「第三方 vendored 代码 / 构建缓存 / 兄弟 worktree」本来就不是「豁免」，
是**不在仓库里**，该由 `.gitignore` 说，不该由每条锁各说一遍。
"""

from __future__ import annotations

import pathlib
import subprocess


def repo_py(root: pathlib.Path) -> list[str]:
    """仓库自有 `.py` 的仓库相对 posix 路径，已排序。

    `root` 既是 git 的工作目录，也是结果的相对基准。取不到 git（不在仓库里、git 不可用）
    时**抛**，不返回空表 —— 空表会让所有普查判据恒绿（§四「恒绿的锁与没有锁是一回事」）。

    只留盘上还存在的文件：索引里可能有一条已被删除但未 `git add -A` 的路径，而
    「文件不在」这件事对「它会不会被跑 / 它有没有登记」两个命题都无意义，读它会炸成
    假红。（真删了的话 `git status` 会显示 ` D`，那是它自己的事。）
    """
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files",
         "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=root, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(
            f"git ls-files 取仓库自有 .py 失败（cwd={root}）：{out.stderr.strip()}")
    return sorted(rel for rel in out.stdout.splitlines()
                  if rel and (root / rel).is_file())
