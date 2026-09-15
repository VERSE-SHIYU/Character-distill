# -*- coding: utf-8 -*-
"""缺陷 42 变异矩阵驱动 —— 四组共 23 条，逐条还原并核对 sha256。

**为什么入库。** 缺陷 42 的三轮 commit（`7009d77` 事实层 / `3e2670d` 返工 /
`2c9fee9` 收尾 / 本步两把锁迁移）每一条都引用了本脚本跑出来的「哪条红、红在哪句」。
那些数字原先骑在仓外 `D:/Temp/*.py` 上 —— 追不到、三个月后复现不了（§四：文档引用的
数字，其产数脚本与原始产物也要入库）。本文件是那四个脚本的并档，**锚点已按当前仓库
文本校准**（原脚本有几条锚的是返工前的文本，直接搬过来会 `count == 0`）。

**不变量（每条变异都成立，脚本自检，不是口头保证）**
  1. **先验基线**：三把锁全绿才开跑。基线不绿时「变异后红」说不清红源（§四：两种成因
     共用一个信号 = 两种都没有守卫）。
  2. **锚点恰一命中**：`src.count(old) == 1`，0 命中或 2 命中都当场 assert —— 锚点漂移
     会静默变成「变异没生效」，那是最危险的假绿。
  3. **还原逐字节**：每条跑完立刻按字节还原，收尾时 sha256 与开跑前比对。

**用法**
    python tests/perf/route_facts_mutations.py              # 全部（不含容器档）
    python tests/perf/route_facts_mutations.py --group V    # 只跑第 3 步迁移那组
    python tests/perf/route_facts_mutations.py --group I    # 只跑两把锁的隔离/无耦合那组
    python tests/perf/route_facts_mutations.py --with-container   # 追加 F-3 容器档

五组：**F/R/X** 打事实层自己（第 1 步三轮），**V** 打第 3 步迁移，**I** 打两把锁的
隔离性（移走一把、以及交错调用后本层输出指纹不变 —— 隔离判据 3 的断言形态）。
**I 组会临时把一把锁改名成 `.hidden`**，跑完立刻还原；收尾核对会点名残留。

**F-3 为什么单独一档。** 「按 `isinstance(APIRoute)` 遍历 `app.routes`」这个错误写法的
可见性**依赖 fastapi 版本**：本地 0.133.1 下 include 进来的路由是内联的（枚举 228 ==
文档 228，**不红**），上锁版本 0.141.1 下被包成没有 `.routes` 的对象（枚举 9 vs 文档 228，
红）。所以 F-3 的本地结果**不能**当作「这条判据没问题」的证据，必须在上锁版本里复跑 ——
本组标 `RED-container`，默认跳过并打印跳过原因（不静默），加 `--with-container` 才跑。

**V 组为什么没有「迁移前的旧锁」两条。** 旧版锁只在迁移那一刻存在于工作树里，之后被新锁
替换 —— 把已删除的旧判据抄回仓里当靶子是「添加 over 删除」。配方（`_MEM_UNREAD` 那条
`Annotated[...]` 注入）留在 V1/V2/V4 里，配上 `git show <迁移前 sha>:tests/
test_auth_param_used.py` 即可复原当时那次绿；V4/V7 是留在树里的**等价形态** —— 把新锁的
判据退回旧的 AST 形状，同一变异仍然绿，证明「旧判据看不见这种写法」。

本脚本不改生产代码：变异只落在测试与 `web/routers/*.py` 的**变异副本**上，
每条跑完立刻还原。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys

# 断言文案是中文，子进程与本进程都过一遍 UTF-8 —— 否则 Windows 控制台的 GBK 会在
# 打印「哪条红、红在哪句」时 UnicodeEncodeError 崩掉，而那正是本脚本唯一的产出。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[2]

RF = ROOT / "tests" / "route_facts.py"                      # 事实层本体（F/R/X 的靶子）
TF = ROOT / "tests" / "test_route_facts.py"                 # 事实层自己的锁
AUTH = ROOT / "tests" / "test_auth_param_used.py"           # 第 3 步迁移的两把锁
L5 = ROOT / "tests" / "test_text_failure_messages.py"
VOICE = ROOT / "web" / "routers" / "voice.py"               # 只作变异副本，跑完还原
MEM = ROOT / "web" / "routers" / "memory.py"
TEXT = ROOT / "web" / "routers" / "text.py"

TARGETS = (RF, TF, AUTH, L5, VOICE, MEM, TEXT)


# ── 变异体 ─────────────────────────────────────────────────────────────────
# 每条 = (编号 + 命题, 靶子测试文件, [(动作, 文件, 载荷)], 期望)
#   动作 repl   : 载荷 = [(old, new), ...]，每个 old 必须恰一命中
#   动作 write  : 载荷 = 整份文件文本
#   动作 append : 载荷 = 追加到文件末尾的文本
#   期望值      : RED / green / RED-container（后者只在 --with-container 下跑）

# F-*：第 1 步 —— 事实层本身的能力
F_GROUP = [
    ("F-1 enumerate_routes 丢掉入口模块",
     "tests/test_route_facts.py",
     [("repl", RF, [('    mods.append(importlib.import_module("server"))\n    return mods',
                     '    return mods')])], "RED"),
    ("F-2 openapi 默认 routes 换成空表",
     "tests/test_route_facts.py",
     [("repl", RF, [('        _cache["spec"] = _spec_from(app().routes)',
                     '        _cache["spec"] = _spec_from([])')])], "RED"),
    ("F-4 form_fields 不解 $ref",
     "tests/test_route_facts.py",
     [("repl", RF, [('        body = _resolve(media.get("schema") or {}, spec)',
                     '        body = {}')])], "RED"),
    ("F-5 form_fields 砍掉 urlencoded 分支",
     "tests/test_route_facts.py",
     [("repl", RF, [('_FORM_CONTENT_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")',
                     '_FORM_CONTENT_TYPES = ("multipart/form-data",)')])], "RED"),
    ("F-6 is_file_field 不看 anyOf",
     "tests/test_route_facts.py",
     [("repl", RF, [('    return any(is_file_field(b) for b in schema.get("anyOf") or ())',
                     '    return False')])], "RED"),
    ("F-7 injected_params 按参数名匹配（不按依赖对象）",
     "tests/test_route_facts.py",
     [("repl", RF, [('    return {d.name for d in dep.dependencies if d.call is dependency}',
                     '    name = getattr(dependency, "__name__", None)\n'
                     '    return {d.name for d in dep.dependencies if d.name == name}')])], "RED"),
    ("F-8 injected_params 退回 AST 扫 Depends（按写法猜）",
     "tests/test_route_facts.py",
     [("repl", RF, [('    return {d.name for d in dep.dependencies if d.call is dependency}',
                     '    import ast, inspect, textwrap\n'
                     '    tree = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint)))\n'
                     '    fn = next(n for n in ast.walk(tree)\n'
                     '              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))\n'
                     '    out = set()\n'
                     '    for arg, dflt in zip(fn.args.args, fn.args.defaults):\n'
                     '        if isinstance(dflt, ast.Call) and getattr(dflt.func, "id", None) == "Depends":\n'
                     '            out.add(arg.arg)\n'
                     '    for arg, dflt in zip(fn.args.kwonlyargs, fn.args.kw_defaults):\n'
                     '        if isinstance(dflt, ast.Call) and getattr(dflt.func, "id", None) == "Depends":\n'
                     '            out.add(arg.arg)\n'
                     '    return out')])], "RED"),
    # F-9：新加的那条「本层输出是 app 的纯函数」守卫不能是纸锁 —— 把缓存拿掉，
    # 它内部那句 `openapi() is cached` 负控必须亮（红源点名它，不是别的用例）。
    ("F-9 openapi 不再缓存（新守卫的负控必须亮）",
     "tests/test_route_facts.py",
     [("repl", RF, [('    if "spec" not in _cache:\n'
                     '        _cache["spec"] = _spec_from(app().routes)\n'
                     '    return _cache["spec"]',
                     '    return _spec_from(app().routes)')])],
     "RED", "缓存没生效"),
]

# F-3：容器档 —— 见模块 docstring「F-3 为什么单独一档」
_F3_OLD = '''    for mod in _default_modules() if modules is None else modules:
        for obj in vars(mod).values():
            if not isinstance(obj, (APIRouter, FastAPI)):
                continue
            for route in obj.routes:
                if not isinstance(route, APIRoute):
                    continue
                for method in route.methods:
                    out[(route.path_format, method.lower())] = route'''

_F3_NEW = '''    for route in app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            out[(route.path_format, method.lower())] = route'''

F3_GROUP = [
    ("F-3 enumerate_routes 改成按 isinstance 遍历 app().routes（版本相关）",
     "tests/test_route_facts.py",
     [("repl", RF, [(_F3_OLD, _F3_NEW)])], "RED-container"),
]

# R-*：第 1 步返工 —— 入口模块身份 + 文件字段形态
R_GROUP = [
    ("R-1a app() 退回 web.server",
     "tests/test_route_facts.py",
     [("repl", RF, [('return importlib.import_module("server").app',
                     'return importlib.import_module("web.server").app')])], "RED"),
    ("R-1b 枚举默认模块退回 web.server",
     "tests/test_route_facts.py",
     [("repl", RF, [('mods.append(importlib.import_module("server"))',
                     'mods.append(importlib.import_module("web.server"))')])], "RED"),
    ("R-2 is_file_field 只认 anyOf 内部（漏必填文件的顶层形态）",
     "tests/test_route_facts.py",
     [("repl", RF, [('    if "contentMediaType" in schema or schema.get("format") == "binary":\n'
                     '        return True\n'
                     '    return any(is_file_field(b) for b in schema.get("anyOf") or ())',
                     '    return any(is_file_field(b) for b in schema.get("anyOf") or ())')])], "RED"),
    ("R-3 is_file_field 不看 anyOf（与 F-6 同源，重跑核一致性）",
     "tests/test_route_facts.py",
     [("repl", RF, [('    return any(is_file_field(b) for b in schema.get("anyOf") or ())',
                     '    return False')])], "RED"),
]

# X-*：第 1 步收尾 —— 并列身份的文件系统身份判据
_ALIAS_LOAD = '''    mods.append(importlib.import_module("server"))

    import importlib.util as _ilu

    _p = str(_repo / "web" / "server.py")
    _alias_path = {path_expr}
    if "srv_alias" not in sys.modules:
        _spec = _ilu.spec_from_file_location("srv_alias", _alias_path)
        _m = _ilu.module_from_spec(_spec)
        sys.modules["srv_alias"] = _m
        _spec.loader.exec_module(_m)
    mods.append(sys.modules["srv_alias"])
    return mods'''

_X_ANCHOR_RF = '    mods.append(importlib.import_module("server"))\n    return mods'
_X_ANCHOR_TF = ('        try:\n'
                '            return os.path.samefile(path, entry_file)\n'
                '        except OSError:      # 路径不存在（如只剩 .pyc）—— 不是同一份文件\n'
                '            return False')

X_GROUP = [
    ("X-3 经改过盘符大小写的路径以别名 srv_alias 装载同一份 server.py",
     "tests/test_route_facts.py",
     [("repl", RF, [(_X_ANCHOR_RF, _ALIAS_LOAD.format(path_expr='_p[:1].swapcase() + _p[1:]'))])],
     "RED"),
    ("X-4 同上但走原路径字符串（证明「红源是同一份文件」而非「路径长得怪」）",
     "tests/test_route_facts.py",
     [("repl", RF, [(_X_ANCHOR_RF, _ALIAS_LOAD.format(path_expr='_p'))])], "RED"),
    ("X-5 samefile 退回字符串 ==，同时保留 X-3 的别名装载（红源唯一性）",
     "tests/test_route_facts.py",
     [("repl", RF, [(_X_ANCHOR_RF, _ALIAS_LOAD.format(path_expr='_p[:1].swapcase() + _p[1:]'))]),
      ("repl", TF, [(_X_ANCHOR_TF, '        return path == entry_file')])], "green"),
]

# V-*：第 3 步 —— 两把锁迁到事实层
_MEM_UNREAD = '''

from typing import Annotated


@router.get("/__probe_unread")
async def _probe_unread(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {"ok": True}
'''

_MEM_READ = '''

from typing import Annotated


@router.get("/__probe_read")
async def _probe_read(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {"user_id": user["id"]}
'''

_AUTH_OLD_SHAPE = [
    ('''        injected = route_facts.injected_params(route, get_current_user)
        if not injected:
            continue
        node = _endpoint_node(route)''',
     '''        node = _endpoint_node(route)
        injected = _old_shape_injected(node)   # 变异：退回旧判据
        if not injected:
            continue'''),
    ("\n\ndef _unused_user_endpoints",
     '''

def _old_shape_injected(node):
    a = node.args
    n_pos = len(a.args) - len(a.defaults)
    pairs = [(x.arg, a.defaults[i - n_pos] if i >= n_pos else None)
             for i, x in enumerate(a.args)]
    pairs += list(zip([x.arg for x in a.kwonlyargs], a.kw_defaults))
    out = set()
    for name, d in pairs:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                and d.func.id == "Depends"
                and any(isinstance(x, ast.Name) and x.id == "get_current_user"
                        for x in d.args)):
            out.add(name)
    return out


def _unused_user_endpoints'''),
]

# 注意第 3 条锚为什么把整张 `_FORM_METADATA` 收敛到一条 op：**只退回覆盖面**会先被
# 负控 `test_l5_scan_is_not_vacuous_and_policy_has_no_stale_entries` 红掉（覆盖率一缩，
# 名单里另外三条立刻成陈旧条目 —— 那是负控**按设计**工作）。本变异要隔离的是
# 「判据+覆盖面」这一对，不是负控，故把策略表一并收敛到同一条 op，负控才不掺进来。
# 仓外旧驱动里这条是绿的，只因迁移前的负控不查陈旧条目。
_L5_OLD_SHAPE = [
    ('''    return {
        (path, method.lower())
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method.lower() in route_facts._METHODS and _has_form_content(item[method])
    }''',
     '''    return {("/api/text/upload", "post")}   # 变异：退回「只锁一个 op」'''),
    ('''    fields = set(route_facts.form_fields(path, method))
    return sorted(fields - {_PAYLOAD_FIELD} - _FORM_METADATA.get((path, method), set()))''',
     '''    import ast as _ast
    tree = _ast.parse((_ROOT / "web" / "routers" / "text.py").read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        if isinstance(node, _ast.AsyncFunctionDef) and node.name == "upload_text":
            a = node.args
            bound = a.args[len(a.args) - len(a.defaults):] if a.defaults else []
            form = {arg.arg for arg, d in zip(bound, a.defaults)
                    if isinstance(d, _ast.Call) and isinstance(d.func, _ast.Name)
                    and d.func.id == "Form"}
            return sorted(form - {_PAYLOAD_FIELD} - _FORM_METADATA.get((path, method), set()))
    raise AssertionError("变异体：锚点没了")'''),
    ('''_FORM_METADATA = {
    ("/api/text/upload", "post"): {"title", "description", "text_type"},
    ("/api/voice/upload", "post"): {"name"},              # 音色名称，落进音色库当标签
    ("/api/voice/ref-audio/upload", "post"): {"card_id", "ref_text"},
    # ↑ `ref_text` 是**判断不是事实**：它是参考音频的转写，本仓对它没有长度上限，与
    #   缺陷 40 的病灶（starlette 1MB **字节** vs 本仓 100 万**字符**，单位不可换算）
    #   不同类。若将来裁定它算正文通道，删掉这里那个键，该 op 立刻红。
    ("/api/voice/asr", "post"): set(),                    # 一个 Form 字段都没有 = 理想形态
}''',
     '''_FORM_METADATA = {
    ("/api/text/upload", "post"): {"title", "description", "text_type"},
}'''),
]

V_GROUP = [
    ("V1 新 auth 锁 · Annotated 注入且**未**引用（旧锁的盲区）",
     "tests/test_auth_param_used.py",
     [("append", MEM, _MEM_UNREAD)], "RED"),
    ("V2 新 auth 锁 · 反向验收：Annotated 注入**且**引用（防「锁收紧到误伤」）",
     "tests/test_auth_param_used.py",
     [("append", MEM, _MEM_READ)], "green"),
    ("V4 新 auth 锁退回旧 AST 形状判据 + V1 变异 —— 红源唯一性",
     "tests/test_auth_param_used.py",
     [("repl", AUTH, _AUTH_OLD_SHAPE), ("append", MEM, _MEM_UNREAD)], "green"),
    ("V6 新 L5 锁 · 同一变异 —— 覆盖面 1 条 → 4 条",
     "tests/test_text_failure_messages.py",
     [("repl", VOICE, [('    ref_text: str = Form(""),\n',
                        '    ref_text: str = Form(""),\n    text: str = Form(""),\n')])], "RED"),
    ("V7 L5 锁整块退回迁移前的判据+覆盖面 + V6 变异 —— 红源唯一性",
     "tests/test_text_failure_messages.py",
     [("repl", L5, _L5_OLD_SHAPE),
      ("repl", VOICE, [('    ref_text: str = Form(""),\n',
                        '    ref_text: str = Form(""),\n    text: str = Form(""),\n')])], "green"),
    ("V8 新 L5 锁 · text op 多一个 Form 字段 —— 原覆盖面等价性",
     "tests/test_text_failure_messages.py",
     [("repl", TEXT, [('    text_type: str = Form("story"),\n',
                       '    text_type: str = Form("story"),\n    language: str = Form(""),\n')])],
     "RED"),
]

# I-*：隔离判据 3 —— 两把锁互不依赖（不是口头保证：移走一把，另一把要照常红同一条）
#
# 第 3 条只做「移走一把」还不够：两把锁都 import route_facts，可能**通过它**隐式耦合
# （共用一格会被对方污染的缓存）。route_facts 确实有模块级缓存 `_cache`，所以 I-3 在
# 同一个进程里按两种顺序把两把锁的扫描都跑一遍，断言本层的输出指纹逐字不变。
_ORDER_SWAP = "<order-swap>"

_ORDER_SWAP_CODE = '''
import hashlib
import json
import sys

sys.path.insert(0, "tests")

import route_facts
import test_auth_param_used as A
import test_text_failure_messages as L5


def snap():
    spec = json.dumps(route_facts.openapi(), sort_keys=True, ensure_ascii=False)
    enum = json.dumps(sorted(route_facts.enumerate_routes()), ensure_ascii=False)
    return (hashlib.sha256(spec.encode()).hexdigest()[:12],
            hashlib.sha256(enum.encode()).hexdigest()[:12],
            len(route_facts.enumerate_routes()))


assert snap()[2] > 0, "枚举是空的 —— 下面的「指纹不变」会恒真（负控失效）"

before = snap()
L5._form_operations()
after_l5 = snap()
A._unused_user_endpoints()
after_auth = snap()

assert after_l5 == before, f"L5 跑过之后本层输出变了：{before} -> {after_l5}（缓存被调用方污染）"
assert after_auth == after_l5, f"auth 跑过之后本层输出变了：{after_l5} -> {after_auth}"
print("ORDER-SWAP OK", before)
'''

I_GROUP = [
    ("I-1 移走 auth 锁文件 · L5 仍按 V6 红同一条（不依赖另一把锁）",
     "tests/test_text_failure_messages.py",
     [("hide", AUTH, None),
      ("repl", VOICE, [('    ref_text: str = Form(""),\n',
                        '    ref_text: str = Form(""),\n    text: str = Form(""),\n')])],
     "RED", "多出了非正文通道的 Form 字段"),
    ("I-2 移走 L5 锁文件 · auth 仍按 V1 红同一条",
     "tests/test_auth_param_used.py",
     [("hide", L5, None), ("append", MEM, _MEM_UNREAD)],
     "RED", "新出现「注入 user 却不引用」的端点"),
    ("I-3 两把锁按序交错调用 · 本层输出指纹不变（无共用缓存污染）",
     _ORDER_SWAP, [], "OK"),
]

GROUPS = {"F": F_GROUP + F3_GROUP, "R": R_GROUP, "X": X_GROUP, "V": V_GROUP,
          "I": I_GROUP}


# ── 执行 ───────────────────────────────────────────────────────────────────


def _hidden(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(path.name + ".hidden")


def _apply(edits):
    for kind, path, payload in edits:
        if kind == "append":
            path.write_text(path.read_text(encoding="utf-8") + payload, encoding="utf-8")
        elif kind == "write":
            path.write_text(payload, encoding="utf-8")
        elif kind == "hide":
            path.rename(_hidden(path))
        elif kind == "repl":
            src = path.read_text(encoding="utf-8")
            for old, new in payload:
                hits = src.count(old)
                assert hits == 1, f"锚点在 {path.name} 命中 {hits} 次（应恰 1）：{old[:70]!r}"
                src = src.replace(old, new)
            path.write_text(src, encoding="utf-8")
        else:
            raise ValueError(f"未知动作 {kind}")


def _restore(baseline):
    for p, b in baseline.items():
        hid = _hidden(p)
        if hid.exists():
            hid.unlink()
        p.write_bytes(b)


def _run(target: str) -> tuple[str, list[str]]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider", "--tb=line"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = r.stdout + r.stderr
    keep = [ln.strip()[:300] for ln in out.splitlines()
            if ln.strip().startswith(("FAILED", "ERROR")) or "AssertionError" in ln]
    summary = next((ln.strip() for ln in out.splitlines()
                    if ("passed" in ln or "failed" in ln or "error" in ln) and " in " in ln), "?")
    return summary, keep


def _run_py(code: str) -> tuple[str, list[str]]:
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = r.stdout + r.stderr
    bad = [ln.strip()[:300] for ln in out.splitlines() if "Error" in ln or "assert" in ln]
    return ("OK" if r.returncode == 0 else f"退出码 {r.returncode}"), bad


def _baseline_gate():
    """先验基线：三把锁全绿才开跑 —— 否则「变异后红」说不清红源。"""
    bad = []
    for target in ("tests/test_route_facts.py", "tests/test_auth_param_used.py",
                   "tests/test_text_failure_messages.py"):
        summary, _ = _run(target)
        print(f"  基线 {target:44s} {summary}")
        if "failed" in summary or "error" in summary:
            bad.append(target)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="FRVX", help="要跑的组，如 V 或 FRVX（默认全部）")
    ap.add_argument("--with-container", action="store_true",
                    help="追加 F-3：把枚举换成 isinstance(app.routes)，在上锁 fastapi 版本里复跑")
    args = ap.parse_args()

    baseline = {p: p.read_bytes() for p in TARGETS}

    print("== 先验基线 ==")
    if _baseline_gate():
        print("\n基线不绿 —— 拒绝跑变异矩阵（红源说不清）。先修基线。")
        return 2

    groups = [g for g in args.group.upper() if g in GROUPS]
    mismatches: list[str] = []
    for name in groups:
        print(f"\n===== {name} 组 =====")
        for item in GROUPS[name]:
            label, target, edits, expect = item[:4]
            marker = item[4] if len(item) > 4 else None
            if expect == "RED-container" and not args.with_container:
                print(f"\n### {label}\n    （跳过：红不红是框架版本的函数，本地跑不出红。"
                      "用 --with-container 在上锁版本里复跑）")
                continue
            _apply(edits)
            if target == _ORDER_SWAP:
                summary, keep = _run_py(_ORDER_SWAP_CODE)
                got = "OK" if summary == "OK" else "FAIL"
            else:
                summary, keep = _run(target)
                got = "RED" if ("failed" in summary or "error" in summary) else "green"
            _restore(baseline)
            want = {"RED": "RED", "RED-container": "RED", "OK": "OK"}.get(expect, "green")
            if got != want:
                mismatches.append(f"{label}：期望 {want} 实得 {got}")
            if marker and not any(marker in k for k in keep):
                mismatches.append(f"{label}：红源里没有 {marker!r}（红的不是那条断言）")
            print(f"\n### {label}   期望={want}  实得={got}")
            for k in keep:
                print("   ", k)
            print("   >>", summary)

    print("\n== 还原核对（sha256 逐字节）==")
    for p in TARGETS:
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        same = got == hashlib.sha256(baseline[p]).hexdigest()
        if not same:
            mismatches.append(f"{p.name} 还原后 sha256 不符")
        if _hidden(p).exists():
            mismatches.append(f"{_hidden(p).name} 残留在树里（移走的文件没还原）")
        print(f"  {str(p.relative_to(ROOT)):38s} {same}  {got[:16]}")

    print("\n== 结论 ==")
    if mismatches:
        for m in mismatches:
            print("  MISMATCH", m)
        return 1
    print("  全部符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
