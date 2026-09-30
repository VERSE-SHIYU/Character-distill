"""锁住仓库的行尾约定（issue #73）。

约定写在 `.gitattributes`：入库一律 LF；检出到工作区也一律 LF —— 包括 Windows 上
`core.autocrlf=true` 的开发机；只有 `.bat` 检出为 CRLF；二进制不做任何转换。

**测的是 git 按这份配置实际怎么做，不是配置文件长什么样。** 三件事都交给 git 自己算：
  - 检出：`git checkout-index` 在 `core.autocrlf=true` 下把全部入库文件写到临时目录（模拟 Windows 检出）；
  - 入库：`git hash-object --path` 算「这份内容以这个路径提交会存成什么」；
  - 文件分类（文本 / 二进制、含不含 CRLF 字节）：`git ls-files --eol` 与索引里的 blob。

**配置取工作区那份。** checkout 时 git 优先读**索引里的** `.gitattributes`（gitattributes(5)：
checkout 过程先用索引、工作区作回退），所以检出用例在一份临时索引上先把工作区的
`.gitattributes` 登记进去（工作区没有就从临时索引里移除），不碰真索引 —— 改了配置
还没提交时，锁看到的就是改后的配置。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _git(*args: str, env: dict | None = None, data: bytes | None = None) -> bytes:
    out = subprocess.run(["git", *args], cwd=_REPO, input=data, capture_output=True,
                         env={**os.environ, **(env or {})})
    assert out.returncode == 0, f"git {' '.join(args)} 失败：{out.stderr.decode(errors='replace')}"
    return out.stdout


def _tracked() -> list[tuple[str, str]]:
    """`[(索引行尾, 路径)]`，来自 `git ls-files --eol`（如 `i/lf`、`i/-text`）。"""
    rows = []
    for line in _git("-c", "core.quotepath=off", "ls-files", "--eol").decode("utf-8").splitlines():
        meta, path = line.split("\t", 1)
        rows.append((meta.split()[0], path))
    return rows


def _blob(path: str) -> bytes:
    return _git("cat-file", "blob", f":{path}")


def _stored_hash(path: str, content: bytes) -> str:
    """这份内容以 `path` 提交时，git 会存成哪个 blob（按当前工作区的 .gitattributes）。"""
    return _git("hash-object", "--stdin", f"--path={path}", data=content).decode().strip()


@pytest.fixture(scope="module")
def windows_checkout(tmp_path_factory) -> Path:
    """在 core.autocrlf=true 下把全部入库文件检出到临时目录，返回该目录。"""
    tmp = tmp_path_factory.mktemp("eol")
    index = tmp / "index"
    shutil.copyfile(_REPO / _git("rev-parse", "--git-path", "index").decode().strip(), index)
    env = {"GIT_INDEX_FILE": str(index)}
    if (_REPO / ".gitattributes").exists():
        _git("update-index", "--add", ".gitattributes", env=env)
    else:
        _git("update-index", "--force-remove", ".gitattributes", env=env)
    out = tmp / "wt"
    _git("-c", "core.autocrlf=true", "-c", "core.eol=crlf",
         "checkout-index", "-a", "-f", f"--prefix={out.as_posix()}/", env=env)
    return out


def test_parsers_see_every_class():
    """负控：三类文件都真存在，否则下面的断言对空集恒真。"""
    kinds = {p.rsplit(".", 1)[-1] for (_, p) in _tracked()}
    assert {"sh", "bat", "py", "png"} <= kinds
    assert any(eol == "i/-text" for (eol, _) in _tracked())


def test_windows_checkout_is_lf_except_bat(windows_checkout):
    crlf_non_bat, lf_bat = [], []
    for eol, path in _tracked():
        if eol != "i/lf":
            continue
        data = (windows_checkout / path).read_bytes()
        if path.endswith(".bat"):
            if data.count(b"\r\n") != data.count(b"\n"):
                lf_bat.append(path)
        elif b"\r\n" in data:
            crlf_non_bat.append(path)
    assert crlf_non_bat == [], f"Windows 检出后仍是 CRLF（应为 LF）：{crlf_non_bat[:10]} 等 {len(crlf_non_bat)} 个"
    assert lf_bat == [], f".bat 检出后不是 CRLF：{lf_bat}"


def test_binaries_are_checked_out_byte_identical(windows_checkout):
    changed = [p for (eol, p) in _tracked()
               if eol == "i/-text" and (windows_checkout / p).read_bytes() != _blob(p)]
    assert changed == [], f"二进制检出后字节变了：{changed}"


def test_recommitting_blobs_with_crlf_bytes_changes_nothing():
    """含 CRLF 字节的入库文件（实际只有二进制，如 PNG 文件头）重新提交必须原样存回。"""
    risky = [p for (_, p) in _tracked() if b"\r\n" in _blob(p)]
    assert risky, "负控：应至少有一个含 CRLF 字节的入库文件（PNG 文件头），否则本条恒真"
    changed = [p for p in risky if _stored_hash(p, _blob(p)) != _git("rev-parse", f":{p}").decode().strip()]
    assert changed == [], f"重新提交会改写这些文件：{changed}"


@pytest.mark.parametrize("path", ["scripts/backup.sh", "nginx/docker-entrypoint.sh", "web/server.py"])
def test_crlf_text_is_stored_as_lf_on_commit(path):
    """即使提交时关掉 autocrlf，CRLF 的脚本/源码也必须以 LF 入库（issue #73 的真实危险场景）。"""
    lf = _blob(path)
    assert b"\r\n" not in lf and b"\n" in lf
    crlf = lf.replace(b"\n", b"\r\n")
    assert _stored_hash(path, crlf) == _stored_hash(path, lf)
