# 导出接口合并 + 测试限流统一隔离 + 变异产物行尾

基线：main `b187a499`（含 #185）。分支 `refactor/dedupe-export-limiter-eol`。
原则：只去重复，不丢功能。会改变的行为在「行为变化」里逐条列出，此外不改任何行为。

## 一、目标检查（第 1 个提交只放这些检查，标 `xfail(strict=True)`；之后每个提交都跑）

新建 `tests/test_dedupe_export_limiter_goal.py`：

- **G1 导出只有一个后端实现**：`TestClient` 用 `/api/cards/{cid}/export` 请求得到 404，并且 `app.routes` 里路径以 `/export` 结尾、带 `card_id` 的路由只有 `/api/distill/cards/{card_id}/export` 这一条。
- **G2 坏 JSON 也能导出 raw**：在存储里放一张 `card_json` 为非法 JSON 的卡，`GET /api/distill/cards/{cid}/export?format=raw` 返回 200，`Content-Disposition` 含 `attachment`，响应体与原始 `card_json` 字节一致。（现状：distill 版先做 `model_validate_json`，这里会抛 500，所以检查是红的。）
- **G3 保留限流**：同一个用例里连续请求 61 次 `/api/distill/cards/{cid}/export`，第 61 次返回 429。（现状：distill 版没有限流，检查是红的。）
- **G4 限流隔离只写一处**：扫描 `tests/**/*.py`，`limiter.reset(` 只出现在 `tests/conftest.py`；任何文件都没有 `limiter.limit =` 这种赋值。

G2、G3 的 app：照 `tests/test_distill_task_api.py:387-398` 的 `_build_app` 搭（`include_router(D.router)`，覆盖 `get_storage` / `get_current_user`），另加 `app.state.limiter = limiter` 和 `app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)`（两个都从 `slowapi` 导入；生产环境的注册在 `web/server.py:179`、`:213`）。不加的话，超限时测试拿到的是异常，不是 429。

行尾（第 3 节）在 Linux CI 上测不出红：Linux 默认就写 LF。所以它的目标检查放到本地 Windows 上手动看，见「验证」。

## 二、S0 开工前核对（任何一条对不上就停下报告）

先用 `Test-Path docs/specs/dedupe-export-limiter-eol.md` 确认文件在。

| 位置 | 应看到 |
|---|---|
| `web/routers/card.py:68-100` | `@router.get("/{card_id}/export")` + `@limiter.limit("60/minute")`；:89-91 `json.JSONDecodeError` 时 `parsed = {}`，:92 `char_name = parsed.get("name", record.get("name", card_id))` |
| `web/routers/distill.py:1463-1509` | `@router.get("/cards/{card_id}/export")`，没有 limiter；:1481 `card = CharacterCard.model_validate_json(record["card_json"])` 写在 `format` 分支之前 |
| `web/routers/distill.py:8`、`:15`、`:41` | 已经 import 了 `json`、`quote`、`limiter` |
| `web/frontend/src/api/client.js:429-444` | `exportCard(cardId, format='raw')`，用 `fetchWithTimeout`，文件名取自 `Content-Disposition` |
| `web/frontend/src/components/DistillWorkbench.jsx:57-73` | 本地 `async function exportCard(card)`，失败只 `console.warn`；按钮在 :382（`task.card_id`）、:442（`card.id`） |
| `tests/conftest.py:99` | `_jwt_secret_for_tests` autouse fixture |
| `tests/test_auth_tokens.py:21-24` | 导入期执行 `_lim_.limiter.limit = lambda *a, **kw: lambda f: f` |
| `tests/lock_coverage.py:440-445` | `pathlib.Path(path).write_text(..., encoding="utf-8")`，没有 `newline` |

全量扫描原始输出（基线 b187a499）：
```
$ grep -rn "cards/.*export\|async function exportCard" web/ tests/ --include=*.py --include=*.js --include=*.jsx
web/routers/distill.py:1463:@router.get("/cards/{card_id}/export")
web/frontend/src/components/DistillWorkbench.jsx:57:async function exportCard(card) {
web/frontend/src/components/DistillWorkbench.jsx:60:    const res = await fetchWithTimeout(`/api/distill/cards/${card.id}/export?format=raw`)
web/frontend/src/api/client.js:429:export async function exportCard(cardId, format = 'raw') {
web/frontend/src/api/client.js:430:  const res = await fetchWithTimeout(`/api/distill/cards/${cardId}/export?format=${format}`)
web/frontend/src/api/__tests__/exportCard.test.js:51:    expect(fetchMock.mock.calls[2][0]).toBe('/api/distill/cards/card1/export?format=raw')
tests/test_examples_pending_goal.py:501:    exported = client.get(f"/api/distill/cards/{cid}/export?format=raw")
tests/test_distill_draft_persist.py:341:    export = _build_client(store, uid).get(f"/api/distill/cards/{card_id}/export")
tests/test_security_authz.py:228:        r = client_b.get(f"/api/cards/{cid}/export")
tests/test_security_authz.py:639:        r = client_a.get(f"/api/cards/{cid}/export")
tests/test_ownership_404.py:238:        r = intruder_client.get(f"/api/cards/{cid}/export")
tests/test_ownership_404.py:270:        r = intruder_client.get(f"/api/distill/cards/{cid}/export")
（card.py:68 的路由前缀是 /api/cards，见 card.py:22；上面的 grep 没扫到它，因为它写成了 "/{card_id}/export"）

$ grep -rn "limiter.reset\|limiter.limit =\|\"enabled\", False" tests/
tests/test_profile_outbox.py:48 / test_cross_region_search.py:39 / test_live_llm_refresh.py:138 / test_cross_border_outbox.py:235 / test_cross_region_fork.py:38 / test_embedding_test_endpoint.py:71 / test_message_backfill.py:194   → monkeypatch enabled=False（7 个文件）
tests/test_auth_tokens.py:24   → limiter.limit = no-op
tests/test_text_upload_filename.py:34,36 / test_retro_position_check_goal.py:102,104 / test_phase_locate_goal.py:168,170   → limiter.reset()

$ grep -rn "write_artifact(" --include=*.py .
./tests/perf/mutation_framework.py:363  （唯一调用方）
./tests/lock_coverage.py:418            （定义）
```

## 三、行为变化（只有这三条，其余不变）

| # | 变化 | 理由 |
|---|---|---|
| B1 | 前端实际在用的导出接口，加上 60/分钟限流 | 被删掉的旧接口本来就有这条限流；合并以后不能丢 |
| B2 | raw 导出遇到坏 JSON，从 500 改成 200 照样导出 | 被删掉的旧接口就是这个行为（card.py:89-92） |
| B3 | `/api/cards/{id}/export` 返回 404 | 前端没有调用方（见扫描）；只有测试在用，测试改到新路径 |

## 四、提交 2：导出只留一个后端接口、一个前端函数

`web/routers/distill.py` `export_card`：
1. 在 `@router.get("/cards/{card_id}/export")` 下一行加 `@limiter.limit("60/minute")`。
2. 把 :1481 的 `CharacterCard.model_validate_json` 挪进 `if format == "tavern":` 分支。`raw` 分支照搬 card.py:84-100 的写法：`json.loads` 失败时 `parsed = {}`，`name = parsed.get("name", record.get("name", card_id))`，内容原样返回 `record["card_json"]`，文件名 `quote(f"{name}.json")`。

`web/routers/card.py`：删掉 `export_card`（:68-100），以及删掉之后不再使用的 `from urllib.parse import quote`（:6，只有 :94 在用）。

`web/frontend/src/components/DistillWorkbench.jsx`：
- 删掉本地 `exportCard`（:57-73）；
- :3 改成 `import { fetchWithTimeout, exportCard } from '../api/client'`（`fetchWithTimeout` 别处还在用就留着，不用就删）；
- :382 改成 `exportCard(task.card_id).catch(e => console.warn('[distill-workbench] export failed:', e))`，:442 同理，参数用 `card.id`；
- `parseCardJson` 在 :187、:397 还在用，保留。

测试改路径（断言不改）：
- `tests/test_security_authz.py:228`、`:639`：`/api/cards/{cid}/export` 改成 `/api/distill/cards/{cid}/export`；
- `tests/test_ownership_404.py:236-239`（`TestCardOwnership.test_export_card_404`）：删掉，它和 :268-271 是同一个检查。

## 五、提交 3：测试限流统一在 conftest 隔离

依据：slowapi 0.1.10 是 PyPI 上的最新版（`pip index versions slowapi` 显示最新就是 0.1.10，`requirements.txt:339`）。它清空计数的对外方法是 `Limiter.reset()`（`slowapi/extension.py:355-363`，调用 `self._storage.reset()`；memory 存储支持清空）。

在 `tests/conftest.py` 的 `_jwt_secret_for_tests` 后面加：

```python
@pytest.fixture(autouse=True)
def _isolate_rate_limiter():
    """web/limiter.py 的 limiter 是进程级单例，用 memory 存储，按 client IP 计数；所有 TestClient
    都是同一个 IP「testclient」。不清空的话，全量跑时各用例共用一个桶，会互相顶成 429。
    每个用例前后各清一次。"""
    from limiter import limiter
    limiter.reset()
    yield
    limiter.reset()
```

- 删掉 3 个文件里同名的 fixture：test_text_upload_filename.py:30-36、test_retro_position_check_goal.py:97-104、test_phase_locate_goal.py:165-170。随之不再使用的 `from limiter import limiter` 一并删掉；test_text_upload_filename.py:60 还用着它，那里保留。
- 删掉 `tests/test_auth_tokens.py:21-24`。理由见 test_embedding_test_endpoint.py:62-68 的注释：这种写法生不生效，取决于哪个文件先 import `routers.auth`。删掉以后跑本文件：
  - 全绿：完成；
  - 出现 429：给本文件加 autouse 的 `monkeypatch.setattr(limiter, "enabled", False)`（和那 7 个文件的写法一样），并在报告里写明是哪个用例、发了几次请求。
- 那 7 个 `enabled=False` 的文件不动，本 PR 不改它们。

## 六、提交 4：变异产物按 LF 写出

`tests/lock_coverage.py:440` 的 `write_text(...)` 加上 `newline="\n"`（写法参照仓库已有的 `tests/test_push_preflight.py:68`；`.gitattributes` 约定工作区一律用 LF）。

## 七、结构自检

| 问题 | 改后 |
|---|---|
| 导出规则写了几处 | 后端 1 处（distill.py `export_card`），前端 1 处（client.js `exportCard`） |
| 测试限流隔离写了几处 | 1 处（conftest）。G4 守住 |
| 变异产物写出口 | 1 处（lock_coverage.write_artifact） |
| 以后再加一种导出格式 | 只在 distill.py `export_card` 里加一个分支 |

## 八、验证

本地（Docker PG），只跑受影响的文件：
```
pytest -q tests/test_dedupe_export_limiter_goal.py tests/test_security_authz.py tests/test_ownership_404.py tests/test_distill_draft_persist.py tests/test_examples_pending_goal.py tests/test_auth_tokens.py tests/test_text_upload_filename.py tests/test_retro_position_check_goal.py tests/test_phase_locate_goal.py tests/test_embedding_test_endpoint.py tests/test_mutation_framework.py tests/test_line_endings.py
cd web/frontend && npm test
```
- 行尾检查（Windows 本机）：跑任意一个变异驱动（复现方式同 #184 发现时那次）之后执行 `git status --porcelain`，应当没有变异产物显示为已修改。
- 全量回归交给分支 CI，gate / 前端 / sentinel 全绿才算过。

报告内容：每个提交的 `git show --stat`；第 1 个提交 G1–G4 的红色输出，以及最后一个提交的绿色输出；上面命令最后的汇总行；test_auth_tokens 删掉 patch 后的结果；Windows 上 `git status` 的原始输出；CI 结果。末尾附「已核实 / 未核实」。范围内的问题直接修，碰到冲突或需要决定的事项才停下。
