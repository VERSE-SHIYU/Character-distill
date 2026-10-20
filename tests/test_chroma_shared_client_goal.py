"""P8 目标检查：一个进程一个 chroma 客户端（实现前全部 xfail(strict=True)）。

C1 并发首次构造不再失败；C2 同路径（含相对/绝对写法）共用同一客户端、不同路径不共用；
C3 构造失败不写入缓存；C4 生产代码里 `chromadb.PersistentClient(` 只出现一次，在共享入口。

C1 必须在新子进程里跑：chromadb 的 System 缓存是类级的，同进程内先跑过 C2/C3
就把缓存预热了。只碰**新建的空目录**，不读写集合（Windows 宿主对非空集合会段错误）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _cfg() -> dict:
    # F7：非空假 key 即可构造，不联网。
    return {
        "chunk_size": 200,
        "chunk_overlap": 20,
        "top_k": 3,
        "embedding_key": "sk-fake-test-key",
        "embedding_region": "cn",
    }


_C1_CHILD = textwrap.dedent(
    """
    import sys
    import tempfile
    import threading
    import traceback

    from core.rag import RAGEngine

    path = tempfile.mkdtemp(prefix="chroma-race-")
    cfg = {
        "chunk_size": 200,
        "chunk_overlap": 20,
        "top_k": 3,
        "embedding_key": "sk-fake-test-key",
        "embedding_region": "cn",
    }
    barrier = threading.Barrier(2)
    errors = []

    def worker():
        try:
            barrier.wait(timeout=30)
            RAGEngine(cfg, chroma_path=path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    stuck = [t for t in threads if t.is_alive()]
    for exc in errors:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    if stuck:
        print(f"threads still alive after 60s: {stuck}", file=sys.stderr)
        sys.exit(2)
    if errors:
        sys.exit(1)
    """
)


@pytest.mark.xfail(strict=True, reason="P8 未实现：两个线程并发首次构造会撞上 chromadb 未加锁的 System 缓存")
def test_c1_concurrent_first_construction_is_safe():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO) + os.pathsep + env.get("PYTHONPATH", "")
    outcomes = []
    for _ in range(3):
        proc = subprocess.run(
            [sys.executable, "-c", _C1_CHILD],
            cwd=str(_REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        outcomes.append(proc)
        if proc.returncode != 0:
            break
    failed = [p for p in outcomes if p.returncode != 0]
    assert not failed, (
        f"{len(failed)}/{len(outcomes)} 子进程失败；"
        + "\n".join(f"rc={p.returncode}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}" for p in failed)
    )


@pytest.mark.xfail(strict=True, reason="P8 未实现：每次 RAGEngine 都新造 PersistentClient，不是按路径共用")
def test_c2_one_client_per_path(tmp_path):
    from core.rag import RAGEngine

    first = tmp_path / "chroma"
    first.mkdir()
    second = tmp_path / "chroma-other"
    second.mkdir()

    abs_path = str(first)
    rel_path = os.path.relpath(abs_path)

    a = RAGEngine(_cfg(), chroma_path=abs_path)
    b = RAGEngine(_cfg(), chroma_path=rel_path)
    c = RAGEngine(_cfg(), chroma_path=str(second))

    assert a._client is b._client, "同一路径（相对/绝对写法）应返回同一个客户端"
    assert c._client is not a._client, "不同路径不能共用客户端"


@pytest.mark.xfail(strict=True, reason="P8 未实现：core.rag.chroma_client 尚不存在，失败缓存语义无从谈起")
def test_c3_failure_is_not_cached(tmp_path, monkeypatch):
    try:
        from core.rag import chroma_client
    except ImportError:
        pytest.fail("core.rag.chroma_client 尚未实现")

    import chromadb

    real_persistent_client = chromadb.PersistentClient
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_persistent_client(*args, **kwargs)

    monkeypatch.setattr(chromadb, "PersistentClient", flaky)
    path = str(tmp_path / "c3")

    with pytest.raises(RuntimeError, match="boom"):
        chroma_client(path)

    client = chroma_client(path)
    assert client is not None
    assert calls["n"] == 2, "失败一次后第二次必须真正重试构造"


def _persistent_client_name(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "PersistentClient"
        and isinstance(func.value, ast.Name)
        and func.value.id == "chromadb"
    )


def _enclosing_function(tree: ast.AST) -> dict[ast.AST, str]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    names: dict[ast.AST, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _persistent_client_name(node):
            cur: ast.AST = node
            while cur in parents and not isinstance(
                cur, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                cur = parents[cur]
            names[node] = cur.name if isinstance(
                cur, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) else "<module>"
    return names


@pytest.mark.xfail(strict=True, reason="P8 未实现：core/rag.py 与 scripts/ 各有一处直接构造")
def test_c4_single_persistent_client_constructor():
    hits: list[tuple[str, str]] = []
    for sub in ("core", "web", "mcp_server", "scripts"):
        for py in sorted((_REPO / sub).rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, SyntaxError):
                continue
            for fn in _enclosing_function(tree).values():
                hits.append((py.relative_to(_REPO).as_posix(), fn))

    assert hits == [("core/rag.py", "chroma_client")], hits
