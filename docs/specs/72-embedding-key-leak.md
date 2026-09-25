@incremental-implementation @search-first @tdd

# 72 线 · 用户 API key 不进日志、不当缓存身份

## 目标
用户填写的向量检索 API key 不再以明文或片段出现在任何日志里；以 key 区分的两处进程内缓存改用同一个不可逆指纹作身份，消除日志泄漏与「前缀相同的两个 key 共用同一个 embedder」两个问题。

## 已定决策（Shiyu 拍板）
- 本段并入 72 线，顶替已证伪的「库重启后首个请求 500」（取证：两次 500 都在库已停止、尚未启动的窗口内，`socket.gaierror` 主机名解析失败；三种重启方式共 9/9 次请求全 200 —— 不是缺陷，不修）
- 新开一条台账记本缺陷（Shiyu 同意）

## 已查实的约束（基线 origin/main `1408bae`）
1. 泄漏点：`core/indexing_service.py:45` `cache_key = f"{text_id}:{embedding_key}"`，`:48` `print(f"[RAG] cache HIT {cache_key}")`、`:50` `print(f"[RAG] cache MISS {cache_key}, building...")` —— 每次取 RAG 都把**完整 key** 打到 stdout（运行时验证日志里实见 `[RAG] cache HIT text_…:sk-…`）
2. 同类身份问题：`core/embeddings.py:438` `cache_key = f"dashscope:{region}:{api_key[:8]}"` —— 只取前 8 个字符（`sk-` 之后仅 5 个随机字符）作缓存身份：两个不同 key 前缀相同时共用同一个 `DashScopeEmbedding`，后者的请求会用前者的 key 发出、记在前者账上。它本身不打日志
3. 全仓普查（AST，函数内污点追踪：参数名或赋值来源含 key / secret / password / token / dsn / credential 的变量，出现在 `print` 或 `logging` 调用参数里）：命中只有第 1 条那两行；其余命中均为 token 计数类变量（`total_used`、`estimated`、`_STATS_TOKENS`）与 `distill.py:549/562` 的 `card_id`，不是密钥
4. 两处缓存的键格式没有外部读者：`_text_rag_cache` 只在 `indexing_service.py:33–63` 内读写；`embeddings._cache` 只在 `:88/:438–441` 读写，全仓无其它模块引用
5. 现有密钥锁：`tests/test_secret_never_plaintext.py`（`TestSqliteCanary` :233、`TestPostgresCanary` :253）只断言「凭据不以明文落库」，不管日志

**S0**（只核坐标，几分钟）：逐条读代码复核 1、2、4、5；第 3 条用 `git grep -n "cache_key\|embedding_key\|api_key" -- core web adapters storage` 抽查 print / logger 调用即可，不重跑普查脚本。不成立就停下报告。

## 约束
- 指纹只有一处定义：在 `core/embeddings.py` 新增 `key_fingerprint(key: str) -> str`（SHA-256 的前 16 位十六进制），两处缓存都用它；不在各处各算一遍
- 日志里只出现 `text_id` / region 等非敏感字段，不出现 key、key 片段或指纹以外的派生值
- 不改缓存语义（按「书 + key」、「region + key」区分），只换身份的表示方式
- 新发现属本段改动面的直接修；需拍板或会撞车的停下报告；台账只按步骤 3 新开一条

## 步骤（按依赖顺序，每步独立 commit）

### 步骤 1：[core] 两处缓存改用指纹作身份，日志不带 key
- 先写失败用例：① 以一个形如 `sk-test…` 的假 key 走 `IndexingService._get_or_build_rag`（`RAGEngine` 用替身，不真建索引），`capsys` 与 `caplog` 都不得包含该 key 或其任意 8 位以上子串；② `create_safe_embedding_fn` 对两个前 8 位相同、之后不同的 key 返回**不同**实例
- 实现：`key_fingerprint`；`indexing_service.py:45` 改为 `f"{text_id}:{key_fingerprint(embedding_key)}"`（空 key 保持空串语义）；`:48/:50` 只打 `text_id`；`embeddings.py:438` 改为 `f"dashscope:{region}:{key_fingerprint(api_key)}"`
- commit：`fix(rag): never log the user's embedding key, and key caches by a fingerprint`

### 步骤 2：[tests] 密钥锁扩展到日志
在 `tests/test_secret_never_plaintext.py` 的两个 canary 里，驱动链路的同时收集 stdout 与日志记录，断言 canary 凭据（含 embedding key）不出现在其中。变异：把 `indexing_service.py` 的日志改回打印完整 `cache_key`，canary 必须变红。
commit：`test(secrets): the canary also checks that credentials never reach the logs`

### 步骤 3：[docs] 台账
`AGENTS.md` 按现有最大号顺延新开一条：「用户向量检索 API key 明文进日志；embedder 缓存按 key 前 8 位区分」，状态已修（附步骤 1、2 的 commit），并写明：生产两台的旧 `docker logs` 里存在明文 key，随下次部署重建 app 容器时清除（见下）。
commit：`docs(ledger): record and close the embedding-key log leak`

## 测试
- 本地只跑受影响文件（Docker PG）：`test_secret_never_plaintext.py`、`test_embeddings*.py`、`test_indexing_service*.py`（按 `git grep -l "indexing_service\|create_safe_embedding_fn" -- tests` 得到的完整清单）
- 不跑全量；合并门是分支 CI

## 验证
- `git grep -n "cache HIT {cache_key}\|api_key\[:8\]"` 为空
- 变异（各一次，跑完还原）：日志改回打完整 `cache_key` → 步骤 1 ① 与步骤 2 变红；`embeddings` 改回 `api_key[:8]` → 步骤 1 ② 变红
- 推送、开 PR、分支 CI 绿后等审计；审计通过由执行方合并（Create a merge commit，只做 git 操作）

## 上线后（服务器操作，合并后由 Shiyu 触发正常部署流程；本段不执行）
部署会重建 app 容器，旧容器连同它的 `docker logs`（json-file）一并删除。部署后在 SZ、SG 各执行一次核验：`docker logs character-distill-app-1 2>&1 | grep -c "cache HIT .*:sk-"` 应为 0，并确认旧 app 容器已不存在（`docker ps -a`）。
