# 属主 404 用例的「到达性」取证

**结论**：`tests/test_ownership_404.py` 的 **32 条属主用例 + 3 条 B 类用例 + 7 条文案一致性用例**，
每一条都真的走到了它声称要验的那个 `raise`，**没有一条是被前面的门（LLM 配置 503 / 参数 400 /
会话过期 404 …）挡下、返回了一个「巧合正确」的状态码。**

## 为什么需要这份取证

「非属主 → 404」这条用例，只有在**请求真的抵达属主判定**时才有意义。若请求在更早的守卫
（例如 `create_group` 的「请先在设置页配置 API Key」503）就被拦下，用例可能仍然「绿」——
**但绿的理由是别的东西**。凑巧过的断言比失败更危险：它的成立取决于测试机状态。

实测确实踩到了：`test_create_group_with_foreign_card_404` 在本机（`.env` 有 `DEEPSEEK_API_KEY`）
绿、在无 key 的机器上红（503），即属此类。

## 怎么做

`raise_probe.py` 是 pytest 插件，包住 `fastapi.exceptions.HTTPException.__init__`，在每次异常构造时
记录**栈里最外层属于本仓的帧**（`file:line:func`）+ `status` + `detail`。于是每条用例「实际命中
了哪个 raise」是直接观测值，不是推断。

```bash
# 本机环境（落点与清单条目由 PROBE_EVIDENCE_ID 决定，无路径参数 —— 见本目录 README.md）
PROBE_EVIDENCE_ID=ownership-reachability \
  PYTHONPATH=".;tests/perf" python -m pytest tests/test_ownership_404.py -q -p raise_probe
# 模拟「测试机没配 API key」——用例必须仍全绿，否则说明断言依赖测试机凭据
PROBE_NO_KEY=1 PROBE_EVIDENCE_ID=ownership-reachability-nokey \
  PYTHONPATH=".;tests/perf" python -m pytest tests/test_ownership_404.py -q -p raise_probe

# 核对：逐条比对「期望 handler」与「实际命中的站点」
python tests/perf/check_reachability.py \
  docs/evidence/ownership-reachability.json docs/evidence/ownership-reachability-nokey.json
```

产物：`docs/evidence/ownership-reachability.json`（本机）、`docs/evidence/ownership-reachability-nokey.json`（无 key 模拟）。
两者 `check_reachability.py` 退出码均为 0。

## 结果（现跑现取，2026-09-12）

| 类别 | 条数 | 判定 |
|---|---|---|
| A 属主型（32 端点） | 32 | 各命中 1 个 raise，`status=404`，函数名 == 期望 handler |
| B 权限型 | 3 | `retract_dm_message` / `delete_card_version` / `require_admin`，均 `403`，函数名相符 |
| 文案一致性 | 7 | 各命中 **2** 个 raise（非属主 + 不存在），且**函数名与状态码都相同** |

两个模式下（有 key / 无 key 模拟）用例数均 `42 passed`，站点表逐行相同。

## 修掉了哪两条 ambient 依赖

两条都属「不修则断言的成立性取决于测试机」，在 `tests/test_ownership_404.py` 的 autouse fixture
`_no_ambient_state` 里钉死：

1. **`deps.get_llm`** —— `deps.get_user_llm` 在用户没配 key 时 fallback 到它（读 `.env` /
   `config.yaml`）。有 key 的机器上门开着、用例能到属主判定；没 key 的机器 `get_user_llm` 返 None，
   `create_group` 先吃 503。**这是「本机绿、他机红」的直接原因。**
2. **`deps.get_memory_manager`** —— `create_group` 是**内联** `from deps import get_memory_manager`，
   不走 `Depends`，故 `dependency_overrides` 管不到。不钉住就会构造真 MemoryManager
   （chroma → fastembed → onnxruntime），本机直接打 `Windows fatal exception: access violation`
   （既有的 chroma 原生崩溃），且结果依赖测试机 `data/` 状态。

### 踩坑记录（值得记，因为症状是「假绿」）

第一版修复写的是 `import web.deps` 再去 patch。**本仓 `web/` 没有 `__init__.py`**，`web.deps` 与
router 实际 import 的 `deps` 是**两个不同的模块对象**（同一文件、两份 globals）。patch 打空、
套件照样绿——因为真门还开着。必须 `import deps`（`web/` 在 `sys.path` 上）。

**判据**：patch 之后要用「把被 patch 的东西真的弄坏」的方式验证（这里是 `PROBE_NO_KEY=1` 模拟
无 key），否则「改完绿了」什么都证明不了。
