# Spec：生产 data/ 目录权限——Compose 一次性服务修属主

基线：main `dd9f673`。只改编排文件 + 文档 + 验证脚本；**不改镜像、Dockerfile，不加 entrypoint、不引入 gosu**，不改路由与业务代码。（本文件取代上一版「pre_start」方案，原因见「方案与出处」。）

## 目标

`POST /api/text/upload` 在两台生产服务器上都能写 `data/uploads`；属主由编排文件声明式保证，不再依赖 `DEPLOY.md` 里「每台机器手动 chown」的一步。

## 根因（已查实）

- 应用以非 root 的 `appuser`（uid 1000）运行，`./data` 是宿主机 bind mount，镜像内的 chown 对它不生效（`Dockerfile:40-41`、`docker-compose.prod.yml:84`）。
- 对齐属主只写在文档里，要求**每台机器各自手动**执行（`DEPLOY.md:42`「深圳、新加坡两台机器各自独立执行第 1–9 步」、`:178-194`）。该步骤 2026-06-26 因一次崩溃补进文档（`7f79c08c`），是靠人记得，不是机制。
- Sentry 上传事件 IP 为 `47.107.42.x`，`DEPLOY.md:15,33` 记载这是**深圳节点**（阿里云 `47.107.42.111`）；举报与 402 事件的 `43.134.55.x` 是新加坡节点（腾讯云 `43.134.55.201`）。**推断**：只有深圳节点缺这一步手动 chown；S0-4 取证确认。

## 方案与出处

**采用：Compose 一次性服务 + `depends_on: condition: service_completed_successfully`**——Docker 官方文档《Use init containers in Compose》「Replace the one-shot service pattern」一节记载的写法（已拉取原文）。

**不采用 `pre_start`（上一版方案）的理由，已调查**：
- 服务器安装方式是 `apt install docker.io docker-compose-v2`（`DEPLOY.md:49`，发行版软件包）。
- 我查了 Ubuntu 软件源索引：`docker-compose-v2` 在 noble 为 **2.24.6**，在 jammy-updates / noble-updates 为 **2.40.3**，都是 v2.x。
- `pre_start` 在 Compose v5.4.0 发布说明里已出现，Docker 官方文档页 2026-08 才收录；v2.x 不会有它。窗口 A 的 S0-2 也已判定不过。
- 一次性服务写法自 Compose 1.29 起可用，v2.x 全部支持；仓库现有 `depends_on: condition: service_healthy`（`docker-compose.prod.yml:98-100`）用的是同一族语法。

其他业界做法及未采用理由：entrypoint + gosu（要改镜像启动方式，多一个依赖）；named volume（改数据位置，`scripts/backup.sh` 等要跟着改）；`user: "${UID}:${GID}"`（开发机用法）。

**属主修复命令**：照抄 postgres 官方镜像 `docker-entrypoint.sh` 第 58 行的做法——`find <目录> \! -user <用户> -exec chown <用户> '{}' +`（已拉取），只改属主不对的条目，不做 `chown -R` 全量改写。

## S0（先做，不过就停）

0. 在 worktree 路径 `.claude/worktrees/<name>/docs/specs/fix-upload-dir-permission.md` 上 `Test-Path` + 与源文件 hash 比对，不通过即停。
1. 逐条复核下表。
2. 两台服务器各执行 `docker compose version`，**只记录、不作为通过条件**（一次性服务写法所有 v2.x 都支持）。
3. 在 app 镜像里 `id appuser`，确认 uid/gid 为 1000（`DEPLOY.md:191` 这么写，未实测）。
4. 登录**两台**服务器取证（先深圳）：宿主机 `ls -ld data data/*`、容器内 `id` 与 `ls -ld /app/data /app/data/*`，原始输出贴回；再量 `find /app/data | wc -l`。

## 已查实的约束（基线 `dd9f673`）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 报错点：`aiofiles.open` 写 `data/uploads/<uuid>_30736.txt` → `PermissionError [Errno 13]`；Sentry 2 条，间隔约 1 小时，文件名尾号相同（同一用户重试）；同期读接口全 200 | `web/routers/text.py:150`；`UPLOAD_DIR` 在 `:105-106` |
| C2 | 镜像以 `appuser` 运行，只有 CMD，无 ENTRYPOINT——**保持不动** | `Dockerfile:40-41`、`:52` |
| C3 | 生产 `./data` 是 bind mount；本地编排同样 | `docker-compose.prod.yml:84`、`docker-compose.local.yml:92` |
| C4 | `deploy.yml` 用 `docker compose -f docker-compose.prod.yml up -d` 起服务，共 4 处，两地各 2 处 | `.github/workflows/deploy.yml:252`、`:294`、`:402`、`:434` |
| C5 | app 镜像表达式 `${APP_IMAGE_REGISTRY:-ghcr.io/verse-shiyu}/character-distill-app:${APP_IMAGE_TAG:-latest}`；新服务要复用，**不复制第二份** | `docker-compose.prod.yml:76` |
| C6 | app 已有 `depends_on`（postgres，`condition: service_healthy`），新依赖要并入同一个块，不覆盖 | `docker-compose.prod.yml:98-100` |
| C7 | 服务器装法：`apt install docker.io docker-compose-v2` | `DEPLOY.md:49` |

## 出处对照表

| 官方条目（已拉取） | 本 spec 行为 |
|---|---|
| Docker 官方：一次性服务 `restart: "no"` + 依赖方 `condition: service_completed_successfully` | B1、B2 |
| postgres 官方 entrypoint 第 58 行：`find … \! -user … -exec chown … '{}' +` | B1 的命令 |
| gosu README / postgres entrypoint 第 341-343 行（降权用 gosu） | **不采用**，仅作对照 |

## 全量扫描原文

命令：`grep -rn "data/" --include=*.py --include=*.yaml --include=*.yml --include=*.sh --include=Dockerfile .`（排除 tests、scripts、前端、文档）
```
web/routers/text.py:105        UPLOAD_DIR = Path("data/uploads")
web/routers/voice.py:27        VOICE_LIBRARY_DIR = Path("data/voice_library")
web/routers/voice.py:31        REF_AUDIO_DIR = Path("data/ref_audio")
web/deps.py:535                cache_dir=voice_cfg.get("cache_dir", "data/voice_cache")
Dockerfile:37                  RUN mkdir -p /app/data /app/data/voice_cache /app/data/chroma_db /app/data/uploads
speech/edge_tts_client.py:38   cache_dir: str = "data/tts_cache"
core/rag.py:171                chromadb.PersistentClient(path=chroma_path or "./data/chroma_db")
config.example.yaml:33,36      path: data/character_sim.db / cache_dir: data/voice_cache
```
结论：进程往 `/app/data` 写的位置共 6 处，全在同一挂载点下；修复只管 `/app/data` 整体，不列子目录，新增目录不会漏。

## 已有路径上的机制

| 机制 | 计时/计数从哪开始 | 依赖的前提 | 一次性服务是否改变 |
|---|---|---|---|
| HEALTHCHECK `--start-period=60s` | app 容器启动后才计时 | 冷启动在 60s 内 | 不变：一次性服务在 app **启动前**跑完 |
| `deploy.yml` 的 `up -d`（4 处） | 每次部署 | 无 | **每次部署都会重跑一次一次性服务**（官方语义）；命令只改属主不对的条目，稳态近乎零成本 |
| `restart: unless-stopped` | 进程退出 | 重启不重跑依赖 | 不变（仅 compose 操作才会重跑） |
| `depends_on` postgres 健康 | 服务启动顺序 | pg 先健康 | 并入同一块，互不覆盖 |
| `--remove-orphans` | 每次 `up` | 清理不在 compose 里的容器 | 新服务在 compose 里，不会被清 |

## 约束

- 不在路由里 try/except 吞错，不 `chmod 777`。
- 一次性服务用 **app 自己的镜像**（不额外拉镜像，深圳节点从 Docker Hub 拉可能失败）；镜像表达式用 YAML 锚点或 `x-` 扩展字段只写一份（C5）。
- 不改镜像与 Dockerfile。

## 步骤（每步独立 commit）

1. [编排] `docker-compose.prod.yml`：新增一次性服务（建议名 `data-perms`）——app 同镜像、`user: root`、挂 `./data:/app/data`、`restart: "no"`、命令为 postgres 官方那种 `find … \! -user 1000 … chown`（uid 以 S0-3 为准）。app 的 `depends_on` 并入 `data-perms: condition: service_completed_successfully`。
2. [编排] `docker-compose.local.yml` 同步同一段。
3. [文档] `DEPLOY.md` 5.1 的手动 chown 步骤改写为「由 compose 的 `data-perms` 服务自动处理，两台机器同一套」，保留一句原因。
4. [验证脚本] 新增 `scripts/verify_data_perms.sh`（矩阵用）。

## 规模表

| 目录 | 上限 | 策略 |
|---|---|---|
| `/app/data` 全部（uploads、chroma_db、voice_*、tts_cache、ref_audio） | 无上限；上传单文件约 30MB | 稳态每次部署 `find` 遍历一遍、不改文件；S0-4 量文件数与遍历耗时，**超过 30s 就停下报告** |

## 调用点矩阵（行 = 启动场景，列 = 可观测输出，格 = 验证脚本断言名）

| 启动场景 | app 能在 `uploads` 建文件 | 主进程仍非 root | 属主已对齐 | 二次 `up -d` 重跑且无副作用 | 一次性服务失败时 app 不启动 |
|---|---|---|---|---|---|
| `data/` 全 root 属主 | `a_write` | `a_nonroot` | `a_owner` | `a_rerun` | — |
| 混合属主 | `b_write` | `b_nonroot` | `b_owner` | `b_rerun` | — |
| 已全为 1000 | `c_write` | `c_nonroot` | — | `c_rerun` | — |
| 故意让一次性服务命令失败 | — | — | — | — | `d_blocked` |

用一次性临时项目名与临时数据目录、不映射端口，避免端口、容器名、数据卷冲突；跑完即删。

## 测试

- 本地只跑上述验证脚本；不跑业务全量；不涉及前端。
- 合并门槛是分支 CI；**合并只做 git 操作，不跑测试、不等 CI**；执行报告不出现本地全量数字。

## 交付

- 报告开头先列本段改动的文件清单，并贴 S0-2、S0-4 的两台服务器原始输出。
- 新发现：属于本段改动面的直接修；只有会撞车或需拍板时才停下报告，不自行记账。

## 补充（审计发现当场记录在此）

- 2026-09-29 执行端 S0：C5 坐标漂移，`docker-compose.prod.yml` 镜像表达式在基线 `dd9f673` 实为第 78 行（spec 写 76）。
- 2026-09-29 执行端 S0-2：两台服务器均为 Compose `2.40.3+ds1-0ubuntu1~24.04.1`，`pre_start` 被 `config` 拒绝（`additional properties 'pre_start' not allowed`），与本版方案一致。
- 2026-09-29 执行端 S0-4：深圳 `/app/data/uploads` 为 `root:root`，是 `find ! -user 1000` 唯一命中；新加坡全部为 appuser。深圳 19 个条目 / 3ms，新加坡 95 个 / 4ms，远低于 30s 上限。
- 2026-09-29 执行端验证：Windows bind mount 不执行 POSIX 写权限，`*_write` 格在 Windows 本地没有分辨力，须在 Linux（WSL2 原生路径或 CI）复跑后才算数。
- 2026-09-29 审计要求补做：用两台服务器的 Compose 2.40.3 对分支里的 `docker-compose.prod.yml` 执行 `config -q`（临时文件、不 up）；`docker-compose.local.yml` 的 `data-perms` 用 `build: .` 可能与 app 重复构建，须改为复用 app 的镜像定义。
- 2026-09-29 审计（分支 `8b0b23fe`，6 个文件逐个看过）通过。部署路径核对：`deploy.yml` 只 pull app 镜像，`data-perms` 经同一锚点复用它，不产生额外拉取；回滚路径的 `up -d` 会让 `data-perms` 幂等重跑。
- 2026-09-29 审计发现（不改，记录）：`docs/credentials-rotation.md` 的 `up -d --no-deps app` 会跳过 `data-perms`。属主修复是持久的，已部署过的机器无影响；只有在**全新机器**上第一次就用 `--no-deps` 起 app 时才会漏跑，新机器按 `DEPLOY.md` 第 5 步走即可。
