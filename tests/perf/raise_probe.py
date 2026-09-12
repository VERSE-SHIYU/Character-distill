"""pytest 插件：记录每个用例请求期间实际触发的 HTTPException raise 站点。

用途：证明 test_ownership_404.py 的每条用例真的走到了它声称要验的那个
raise（属主判定），而不是被前面的门（LLM 配置 503 / 参数 400 / 会话过期 404 …）
挡下返回了一个「巧合正确」的码。

用法：
    PYTHONPATH=.;tests/perf PROBE_EVIDENCE_ID=ownership-reachability \
        python -m pytest tests/test_ownership_404.py -q -p raise_probe
    # 追加 PROBE_NO_KEY=1 模拟「本机 .env 无 key」的机器，验证用例已环境无关

产物：`docs/evidence/<PROBE_EVIDENCE_ID>.json`（写入出口定落点，本插件不自选路径），
逐用例的 (status, detail, 站点)。id 无默认值 —— 缺了就在 session 结束时抛错。
核对：python tests/perf/check_reachability.py docs/evidence/ownership-reachability.json
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 插件模式下本目录不在 sys.path
from evidence_writer import code_sha, write_evidence        # noqa: E402

ENV = "本机 .env 持有供应商 key（nokey 档以 PROBE_NO_KEY=1 模拟无 key 机器）；无外部服务依赖"

_records: dict[str, list[dict]] = {}
_current: list[dict] | None = None


def _repo_frame() -> str | None:
    """栈里最外层属于本仓的帧：即真正的 raise 所在行。"""
    site = None
    for fr in traceback.extract_stack()[:-1]:
        f = fr.filename.replace("\\", "/")
        if f"/web/" in f or f"/core/" in f or f"/mcp_server/" in f:
            site = f"{Path(fr.filename).relative_to(REPO).as_posix()}:{fr.lineno}:{fr.name}"
    return site


def _install_http_probe() -> None:
    import fastapi.exceptions as fe

    orig = fe.HTTPException.__init__

    def __init__(self, *args, **kwargs):
        if _current is not None:
            status = args[0] if args else kwargs.get("status_code")
            detail = args[1] if len(args) > 1 else kwargs.get("detail")
            _current.append({"status": status, "detail": detail, "site": _repo_frame()})
        orig(self, *args, **kwargs)

    fe.HTTPException.__init__ = __init__


def _install_nokey() -> None:
    """模拟「本机没配 key」：让 LLMAdapter 初始化必失败。

    全局 LLM 因而恒为 None，走 get_user_llm 的端点会返 503。
    若此时用例仍全绿，说明夹具已切断 ambient 凭据依赖。
    """
    import adapters.llm_adapter as la

    def __init__(self, *a, **kw):
        raise RuntimeError("[probe] simulated: no API key on this machine")

    la.LLMAdapter.__init__ = __init__


def pytest_configure(config):
    _install_http_probe()
    if os.environ.get("PROBE_NO_KEY"):
        _install_nokey()
        print("[probe] PROBE_NO_KEY=1 — 模拟本机无 key")


def pytest_runtest_setup(item):
    global _current
    _current = []


def pytest_runtest_teardown(item, nextitem):
    global _current
    _records[item.nodeid] = _current or []
    _current = None


def pytest_sessionfinish(session, exitstatus):
    no_key = bool(os.environ.get("PROBE_NO_KEY"))
    evidence_id = os.environ.get("PROBE_EVIDENCE_ID")
    out = write_evidence(
        evidence_id, {"no_key_mode": no_key, "records": _records},
        claim=(f"属主 404 用例各自命中的 raise 站点（{'无 key 模拟' if no_key else '本机有 key'}，"
               f"{len(_records)} 条用例）"),
        script="tests/perf/raise_probe.py", env=ENV, code_sha=code_sha(),
        reproduce=("PROBE_NO_KEY=1 " if no_key else "")
        + f'PYTHONPATH=".;tests/perf" PROBE_EVIDENCE_ID={evidence_id} '
          "python -m pytest tests/test_ownership_404.py -q -p raise_probe",
    )

    print("\n" + "=" * 100)
    print(f"{'test':<64} {'status':>6}  site")
    print("=" * 100)
    unreached = []
    for nodeid, recs in sorted(_records.items()):
        name = nodeid.split("::", 1)[1] if "::" in nodeid else nodeid
        if not recs:
            print(f"{name:<64} {'-':>6}  (未触发任何 HTTPException)")
            unreached.append(name)
            continue
        for r in recs:
            print(f"{name:<64} {str(r['status']):>6}  {r['site']}  | {str(r['detail'])[:40]}")
            if r["status"] != 404:
                unreached.append(f"{name} -> {r['status']} @ {r['site']}")
        name = ""
    print("=" * 100)
    if unreached:
        print("非 404 或未触发的用例：")
        for u in unreached:
            print("  -", u)
    print(f"产物: {out}")
