"""已知缺口名单（「只许减少」）—— 今天没有任何变异撞得到的判别器，逐条写清「要撞到它得做什么」。

**这不是豁免名单，是账。** 名单的键是**判别器的稳定身份** `(文件, 判别器源码, 同文本第几处)`，
由 `lock_coverage.discriminator_identities()` 从现场算出（`gap_keys()` 对**整个覆盖域**的
那次调用，与产物里存的是**同一套**身份）；`tests/test_lock_coverage.py` 双向对账：

  - ① 现场未覆盖 ⊆ 名单 —— **新增缺口不在名单里就红**（这才是「不设豁免」那条老规矩的继承）；
  - ② 名单 ⊆ 现场未覆盖 —— **补上了却没删名单也红**（防名单退化成只增不减的手工清单）。

**为什么允许这样一张名单存在。** 原先的规矩是「不设名单，在任何合法变异下都红不了的判别器
就该删掉」。那条规矩没错，只是不完整：它没回答「今天就已经有一批撞不到的判别器时怎么办」。
全矩阵跑完仍有 84 条，而锁**天天红** —— 生产上的 PDF 已经坏了 73 天，锁红着，没有任何人
看得出来。**一直红的锁等于没有锁**：命题从「全覆盖」换成「只许减少」，后者今天就能成立，
且方向 ① 仍然保证新长出来的判别器不许被豁免。理由与裁定的完整记录见 `AGENTS.md` 缺陷 41
的收口段与 `tests/lock_coverage.py` 的模块 docstring。

**理由要写什么。** 不写「待补」「TODO」这类占位 —— 那种条目对将来清名单的人**零信息**：
他不知道该补哪条变异。写清**这条判别器守的是什么失效、要撞到它得构造什么**。
三条分组的计数跟着驱动走：pg_gate / ping / route_facts，与 `test_lock_coverage.py` 的
参数化同一份驱动名，删条目时能定位到是哪一组。

**边界。** 本文件只是数据 + 一条「理由是不是占位语」的机械判据，不含任何断言 ——
判别器的定义与双向对账都在 `tests/lock_coverage.py`。
"""

from __future__ import annotations

ALLOWED_GAPS: dict[str, dict[tuple[str, str, int], str]] = {
    # ───── tests/perf/pg_gate_mutations.py（26 条）─────
    'tests/perf/pg_gate_mutations.py': {
        # ── tests/test_compose_model.py ──
        ('tests/test_compose_model.py',
         'assert effective_model(p)["services"]["a"]["healthcheck"] == { "test": ["CMD-SHELL", "true"], "interval": "10s',
         0):
            '`<<: *hc`（YAML merge key）必须展开成完整 healthcheck。要撞它得让 `effective_model` 不再展开 merge key（把 `_expand_merge` 那条路拆掉）—— 矩阵里今天没有一条变异碰 merge 展开。',   # test_merge_key_is_expanded
        ('tests/test_compose_model.py',
         'assert effective_model(p)["services"]["a"]["healthcheck"]["disable"] is True',
         0):
            '`disable: true` 必须在模型里**可见** —— 它是「这道门被关了」，不是「少了个键」。要撞它得让 merge 展开把 `disable` 吞掉，或让解析把未知键丢弃。',   # test_disable_true_is_visible
        ('tests/test_compose_model.py',
         'assert compose_model.sentinel_values([p]) == frozenset({"sentinel_SYNTH_VAR"})',
         0):
            '宿主插值 `${SYNTH_VAR}` 被换成哨兵值，模型里看不到 `${VAR}` 本身。要撞它得让 `sentinel_values()` 不再把宿主插值认出来（改它的替换正则）—— 矩阵里没有一条变异动宿主插值的识别。',   # test_host_interpolation_becomes_the_sentinel
        ('tests/test_compose_model.py',
         'assert effective_model(p)["services"]["a"]["healthcheck"]["test"] == [ "CMD-SHELL", "echo $$SYNTH_VAR"]',
         0):
            '容器内 `$$SYNTH_VAR` 必须原样保留（那是留给容器 shell 的）。要撞它得把替换正则从「宿主插值」放宽到「所有 `$`」，把它一并吃掉。',   # test_container_shell_dollar_is_left_alone
        ('tests/test_compose_model.py',
         'assert str(p) in msg, msg',
         0):
            'compose 求值失败时，异常里必须带**命令原文**，不是「出错了」三个字。要撞它得让 `_run_compose_config` 的失败分支不再把命令行拼进异常。',   # test_eval_failure_raises_with_command_and_stderr
        ('tests/test_compose_model.py',
         'assert \'depends on undefined service "nope"\' in msg, msg',
         0):
            '同上：异常里要带 compose 自己给的 stderr 原文（这里点名 `depends on undefined service "nope"`）。要撞它得让 stderr 被截掉或不再透传。',   # test_eval_failure_raises_with_command_and_stderr
        ('tests/test_compose_model.py',
         'assert "docker compose" in str(excinfo.value)',
         0):
            'docker CLI 调不起来时必须**抛**，绝不退化成 `{}`（空值 = 静默放行）。今天代码里根本没有「返回空模型」那条路可撞 —— 要撞它得先有人写出那条退化路。',   # test_missing_cli_raises_instead_of_returning_empty
        ('tests/test_compose_model.py',
         'assert first is second',
         0):
            '同一文件第二次求值必须命中缓存（`is` 同一对象）。要撞它得去掉 `_effective_model` 的缓存装饰器，或把缓存键改坏。',   # test_effective_model_is_cached_per_file
        ('tests/test_compose_model.py',
         'assert compose_model.capability_defect() is None',
         0):
            '真编排文件必须满足两条承重前提（`capability_defect() is None`）。这条断言的对象是**仓里真文件 + 本机 compose 版本**；要撞它得改真编排文件或改 `capability_defect` 的定义 —— 变异矩阵不动真文件，所以撞不到。',   # test_real_compose_satisfies_both_preconditions
        ('tests/test_compose_model.py',
         'assert hits == []',
         0):
            '事实层（`compose_model.py`）源码里不得出现任何本仓专名。禁词表由仓根真编排文件**推导**得出；要撞它得往事实层写一个真服务名或真文件名。',   # test_fact_layer_names_no_repo_specifics
        ('tests/test_compose_model.py',
         'assert [p.name for p in compose_model.project_files()] == [ "docker-compose.local.yml", "docker-compose.prod.y',
         0):
            '`project_files()` 由 `docker-compose*.yml` 的 glob 派生，不是写死的清单（用例把 `_REPO` 指到 tmp，造两个符合模式的文件与一个不符合的）。要撞它得改 glob 模式或把清单写死。',   # test_project_files_is_derived_from_the_glob
        ('tests/test_compose_model.py',
         'assert [p.name for p in compose_model.autoload_files_present()] == [ "docker-compose.override.yml"]',
         0):
            'compose 工具**默认自动加载**的名字（`docker-compose.override.yml`）要被认出来，而本仓自己的命名模式不算。要撞它得改 `autoload_files_present` 的名字表。',   # test_autoload_files_present_flags_the_tools_default_names
        # ── tests/test_pg_gate.py ──
        ('tests/test_pg_gate.py',
         'raise AssertionError( f"无法解析时长 {value!r}：compose 的时长写法是 `10s` / `1m30s` / `500ms` —— " "解析不了就报错点名这个值，不返回默认值")',
         0):
            'compose 时长写法的解析：解析不了就报错**点名那个值**，不返回默认值（返默认值 = 门静默放过一个写错的时长）。要撞它得给真编排文件的 `interval`/`timeout` 喂一个非 `10s`/`1m30s`/`500ms` 形态的串。',   # _duration_seconds
        ('tests/test_pg_gate.py',
         'raise AssertionError( f"{hc_where} 不是映射（现为 {hc!r}）—— 没有它，`depends_on: service_healthy` " "永远等不到健康状态")',
         0):
            'healthcheck 必须是映射 —— 不是映射时 `depends_on: service_healthy` 永远等不到健康状态。要撞它得把真编排文件某个服务的 healthcheck 改成非映射（标量 / 列表）。',   # _assert_healthcheck_config
        ('tests/test_pg_gate.py',
         'raise AssertionError( f"{hc_where} 少了键 {missing}：键集合**恰好**是 {sorted(_HEALTHCHECK_KEYS)}，" "缺一个就等于门少了一部分")',
         0):
            'healthcheck 的**键集合恰好**是 `_HEALTHCHECK_KEYS`，缺一个就等于门少了一部分。要撞它得删掉真编排文件 healthcheck 里的一个键。',   # _assert_healthcheck_config
        ('tests/test_pg_gate.py',
         'raise AssertionError( f"{hc_where} 的 test 必须是**恰好两个元素的列表**、首元素为 \'CMD-SHELL\'" f"（现为 {test!r}）—— 字符串写法与 \'CMD\' / ',
         0):
            '`test` 必须是恰好两个元素的列表、首元素 `CMD-SHELL` —— 字符串写法与 `CMD`/`CMD` 混写会让探针语义不同。要撞它得把真编排文件里的 test 改成别的写法。',   # _assert_healthcheck_config
        ('tests/test_pg_gate.py',
         'raise AssertionError(f"{hc_where} 的 interval={hc[\'interval\']!r} 必须大于 0")',
         0):
            '`interval` 必须 > 0（0 让这道门判不出「不健康」）。要撞它得把真编排文件的 interval 写成 0。',   # _assert_healthcheck_config
        ('tests/test_pg_gate.py',
         'raise AssertionError(f"{hc_where} 的 timeout={hc[\'timeout\']!r} 必须大于 0")',
         0):
            '`timeout` 必须 > 0。要撞它得把真编排文件的 timeout 写成 0。',   # _assert_healthcheck_config
        ('tests/test_pg_gate.py',
         'raise AssertionError( f"{hc_where} 的 retries={retries!r} 不是正整数 —— 探针要连续失败 retries 次才判" "不健康，0 或负数让这道门判不出「不健康」"',
         0):
            '`retries` 必须是正整数：探针要连续失败 retries 次才判不健康，0 或负数让这道门失效。要撞它得把真编排文件的 retries 写成 0 或负数。',   # _assert_healthcheck_config
        ('tests/test_pg_gate.py',
         'raise AssertionError(f"{where} 对 {db!r} 的 depends_on 条目不是映射（现为 {entry!r}）")',
         0):
            '`depends_on` 的条目必须是映射（短写法 `- db` 与长写法要给不同的信息）。要撞它得把真编排文件的 depends_on 条目改成非映射。',   # _assert_consumer_waits
        ('tests/test_pg_gate.py',
         'assert files, ("仓库根目录按 `docker-compose*.yml` 找不出任何编排文件 —— " "本文件所有判据都在空转")',
         0):
            '空转先验：仓根按 `docker-compose*.yml` 找不出任何编排文件的话，本文件所有判据都在空转。要撞它得让 glob 找不到文件 —— 变异不动真文件名。',   # test_lock_is_not_vacuous
        ('tests/test_pg_gate.py',
         'assert hard, f"{path.name}:{db} 解析不出任何硬消费者 —— 判据 3 在空转"',
         0):
            '空转先验：某个编排文件解析不出任何硬消费者的话，判据 3 在空转。要撞它得把真编排文件的 depends_on 全删掉。',   # test_lock_is_not_vacuous
        ('tests/test_pg_gate.py',
         'assert _duration_seconds("10s") == 10',
         0):
            '`_duration_seconds` 的正控：`10s` → 10。要撞它得改解析实现，让秒数算错。',   # test_duration_parser_has_no_fallback_value
        ('tests/test_pg_gate.py',
         'assert _duration_seconds("1m30s") == 90',
         0):
            '正控：`1m30s` → 90（复合写法的进位）。要撞它得只认单段时长。',   # test_duration_parser_has_no_fallback_value
        ('tests/test_pg_gate.py',
         'assert _duration_seconds("500ms") == 0.5',
         0):
            '正控：`500ms` → 0.5（毫秒这一档）。要撞它得把 ms 档丢掉或算错单位。',   # test_duration_parser_has_no_fallback_value
        ('tests/test_pg_gate.py',
         'with pytest.raises(AssertionError):',
         0):
            '负控：解析不了的时长必须**抛**。要撞它得给 `_duration_seconds` 加一个默认返回值 —— 那时这里变成 DID NOT RAISE。',   # test_duration_parser_has_no_fallback_value
    },
    # ───── tests/perf/ping_mutations.py（19 条）─────
    'tests/perf/ping_mutations.py': {
        # ── tests/test_health_probe_targets.py ──
        ('tests/test_health_probe_targets.py',
         'assert regions, "deploy.yml 里解析不出任何 deploy-* 区域"',
         0):
            '`deploy.yml` 里必须解析出至少一个 `deploy-*` 区域 —— 解析不出就说明解析器瞎了。要撞它得改 `deploy.yml` 的区域命名（矩阵不动这个文件）或改解析器的正则。',   # test_parsers_see_at_least_one_target
        ('tests/test_health_probe_targets.py',
         'assert info["targets"], f"{name} 里解析不出任何 gate 调用 —— 解析器瞎了，锁在空转"',
         0):
            '每个区域里必须解析出 gate 调用 —— 解析不出则下面的调用检查全在空转。要撞它得改 gate 的调用写法或解析器。',   # test_parsers_see_at_least_one_target
        ('tests/test_health_probe_targets.py',
         'assert not bad, f"库正常时这些目标不答 2xx：{bad} —— 先验探针本身就走不通"',
         0):
            '先验探针本身要走得通：库正常时这些目标必须答 2xx。要撞它得让某个 target 在库正常时答非 2xx（改探针的目标清单 / 路由）—— 前提是本地真有服务在跑。',   # test_all_targets_answer_when_storage_is_healthy
        ('tests/test_health_probe_targets.py',
         'assert info["gate"], ( f"{name} 里没解析出恰好一个 <X>_gate() 定义（实得 {info[\'defs\']} 个）—— " "没有门函数的区域，下面的调用检查无从谈起")',
         0):
            '每个区域必须有**恰好一个** `<X>_gate()` 定义。要撞它得在 `deploy.yml` 里加/删一个 gate 函数。',   # test_every_region_has_exactly_one_gate_function
        ('tests/test_health_probe_targets.py',
         'assert param, f"{name} 的 gate 里找不到从 $2 取路径的变量（实得 {param!r}）"',
         0):
            'gate 里必须有一个从 `$2` 取路径的变量 —— 没有它，gate 探的不是「给它的那条路径」。要撞它得改 gate 的取参写法。',   # test_gate_probes_the_path_it_was_given
        ('tests/test_health_probe_targets.py',
         'assert f"${{{param}}}" in line, ( f"{name} 的 gate 里这条 curl 没走路径参数 ${{{param}}}：{line.strip()}")',
         0):
            'gate 里那条 curl 必须**真的用**那个路径参数（`${param}`），而不是写死一条路径。要撞它得把 `$2` 换成硬编码路径。',   # test_gate_probes_the_path_it_was_given
        # ── tests/test_health_ready.py ──
        ('tests/test_health_ready.py',
         'assert resp.json() == {"status": "ready"}',
         0):
            '`/health/ready` 在存储答得上来时必须返回 `{"status": "ready"}` —— 这个字面量是下游判据的契约。要撞它得改 ready 的返回体。',   # test_ready_returns_200_when_storage_answers
        ('tests/test_health_ready.py',
         'assert resp.status_code != 401',
         0):
            '健康端点必须**免 token**（不是 401）。要撞它得给 `/health/ready` 挂上鉴权依赖。',   # test_reachable_without_token
        ('tests/test_health_ready.py',
         'assert op in declared | enumerated, f"{op} 不在任何一份路由账本里"',
         0):
            '路由账本对账之一：这个 op 必须出现在声明的账本或枚举出的集合里。要撞它得新增一条既没声明、也枚举不到的路由。',   # test_op_exists_in_the_real_app
        ('tests/test_health_ready.py',
         'assert op in enumerated, f"{op} 只在 OpenAPI 文档里、枚举不到 —— 账本对不上"',
         0):
            'op 必须**枚举得到**，不能只写在 OpenAPI 文档里 —— 两份账本漂移是最难发现的那一类。要撞它得让某个 op 只出现在文档里。',   # test_op_exists_in_the_real_app
        ('tests/test_health_ready.py',
         'assert op not in extra, f"{op} 枚举得到、文档里没有：两份账本漂移"',
         0):
            'op 不能只枚举得到、文档里没有（同上，方向相反）。要撞它得让某个 op 只被枚举到、文档里查不到。',   # test_op_exists_in_the_real_app
        # ── tests/test_storage_ping.py ──
        ('tests/test_storage_ping.py',
         'assert "ping" in str(excinfo.value), ( f"实例化确实抛了 TypeError，但点名的不是 ping：{excinfo.value}")',
         0):
            '`StorageBase` 的子类不实现 `ping` 时，实例化必须抛 **TypeError 且点名 `ping`** —— 点名是这条判据的全部内容（只说「不能实例化」等于没说哪个）。要撞它得让抽象方法不被检查，或把方法名写错。',   # test_storage_base_subclass_without_ping_cannot_be_instantiated
        ('tests/test_storage_ping.py',
         'assert "ping" not in cls.__abstractmethods__',
         0):
            '契约锁的牙齿之一：`ping` **不在** `__abstractmethods__` 里 —— 基类上有它的实现，不是纯抽象。要撞它得把 `ping` 改成 `@abstractmethod`。',   # test_contract_lock_has_teeth
        ('tests/test_storage_ping.py',
         'assert isinstance(instance, StorageBase)',
         0):
            '实例必须是 `StorageBase` 的实例（契约真的被继承）。要撞它得让子类不继承基类。',   # test_contract_lock_has_teeth
        ('tests/test_storage_ping.py',
         'assert callable(getattr(instance, "ping"))',
         0):
            '实例上 `ping` 必须**可调用** —— 光有同名属性不算（属性可以是任何东西）。要撞它得把 ping 换成非可调用对象。',   # test_contract_lock_has_teeth
        ('tests/test_storage_ping.py',
         'assert await store.ping() is None',
         1):
            'SQLite 可用时 `store.ping()` 必须返回 `None`（不抛、也不返回 False/空串）—— 契约是「无返回值的成功」。要撞它得让 ping 返回一个值。',   # test_sqlite_ping_succeeds_on_usable_database
        ('tests/test_storage_ping.py',
         'with pytest.raises(OSError):',
         0):
            'DB 路径不可用时 SQLite 的 ping 必须抛 `OSError`，不许静默成功。要撞它得在 ping 里吞掉底层 OSError。',   # test_sqlite_ping_raises_when_db_path_is_unusable
        ('tests/test_storage_ping.py',
         'with pytest.raises(sqlite3.OperationalError, match="locked"):',
         0):
            '库被锁住时 mid-flight 的 ping 必须抛 `sqlite3.OperationalError` 且消息含 `locked` —— 「锁住了」与「连不上」是两种失效，不许合并。要撞它得在 ping 里吞掉 OperationalError。',   # test_sqlite_ping_raises_when_locked_out_mid_flight
        ('tests/test_storage_ping.py',
         'assert await store.ping() is None',
         0):
            '真 PG 可达时 `ping()` 返回 `None`。这一条要真 PG，本机与容器都跳过（另账）；要撞它得改 pg ping 的实现。',   # test_pg_ping_succeeds_on_reachable_database
    },
    # ───── tests/perf/route_facts_mutations.py（39 条）─────
    'tests/perf/route_facts_mutations.py': {
        # ── tests/test_auth_param_used.py ──
        ('tests/test_auth_param_used.py',
         'raise AssertionError( f"读不到 {route.path_format} 端点函数的源码（{exc!r}）—— 本锁对该路由失效。" "换成源码可读的函数，或把该路由显式移进 ALLOWLIST。"',
         0):
            '读不到端点函数源码时**报错点名那条路由**，不退化成「跳过这条」—— 静默跳过等于这把锁对这条路由失效。要撞它得让某条路由的 `path_format` 指到读不到源码的函数（lambda / 动态生成）。',   # _endpoint_node
        ('tests/test_auth_param_used.py',
         'assert not stale, f"ALLOWLIST 里的条目已不再命中，请删除：{sorted(stale)}"',
         0):
            'ALLOWLIST（哪些端点注入了身份参数却有意不引用）里不许有**已不再命中**的陈旧条目。要撞它得让某条被豁免的端点改掉签名或消失 —— 那正是要人同步删表的那一刻。',   # test_allowlist_is_neither_stale_nor_reasonless
        ('tests/test_auth_param_used.py',
         'assert injected, ( "一条「注入 get_current_user 的端点」都没扫到 —— 判据面失效（依赖对象换了 / " "get_dependant 的用法变了），上面两条断言都在假绿")',
         0):
            '空转先验：一条「注入 get_current_user 的端点」都没扫到 ⇒ 上面两条断言都在假绿（依赖对象换了 / `get_dependant` 用法变了）。要撞它得把身份依赖换成别的写法。',   # test_scan_is_not_vacuous
        # ── tests/test_policy_table.py ──
        ('tests/test_policy_table.py',
         'assert policy_table.empty_reasons({}) == set()',
         0):
            '`empty_reasons` 对空表返空集（不许把「一张空表」判成「有理由缺失」）。要撞它得让实现返回非空。',   # test_empty_reasons_of_an_empty_table_is_empty
        ('tests/test_policy_table.py',
         'assert policy_table.empty_reasons({("k",): "理由"}) == set()',
         0):
            '非空理由不被判为空（把 `"理由"` 判成空 = 判据过粗）。要撞它得让实现把所有键都判成缺理由。',   # test_empty_reasons_keeps_a_real_reason
        ('tests/test_policy_table.py',
         'assert policy_table.empty_reasons({("k",): " 理由 "}) == set()',
         0):
            '前后只有空白的真理由不被判为空（判空必须先 `strip`，否则 `" 理由 "` 会被误判）。要撞它得去掉实现里的 strip。',   # test_empty_reasons_keeps_a_real_reason
        ('tests/test_policy_table.py',
         'assert policy_table.empty_reasons(table) == set()',
         0):
            '合规表上的正控：`empty_reasons` 返空集 —— 否则上面几条负控可能恒真。',   # test_all_compliant_yields_only_empty_sets
        ('tests/test_policy_table.py',
         'assert policy_table.stale_keys(table, observed) == set()',
         0):
            '合规表上的正控：`stale_keys` 返空集（否则这个函数可能恒返全集）。',   # test_all_compliant_yields_only_empty_sets
        ('tests/test_policy_table.py',
         'assert policy_table.unexpected(observed, table) == set()',
         0):
            '合规表上的正控：`unexpected` 返空集。',   # test_all_compliant_yields_only_empty_sets
        ('tests/test_policy_table.py',
         'assert policy_table.stale_keys({}, set()) == set()',
         0):
            '两侧都空时 `stale_keys` 返空集（边界：空表 ∩ 空观察集）。',   # test_both_sides_empty
        ('tests/test_policy_table.py',
         'assert policy_table.unexpected(set(), {}) == set()',
         0):
            '两侧都空时 `unexpected` 返空集（同上，方向相反）。',   # test_both_sides_empty
        # ── tests/test_route_facts.py ──
        ('tests/test_route_facts.py',
         'assert set(content) == {"application/x-www-form-urlencoded"}, sorted(content)',
         0):
            '纯 Form 路由的 content-type 恰好是 `application/x-www-form-urlencoded`（不是 multipart、也不多带一个）。要撞它得改 `route_facts` 的类型推导。',   # test_synth_pure_form_route_is_urlencoded
        ('tests/test_route_facts.py',
         'assert "multipart/form-data" in content, sorted(content)',
         0):
            '带文件字段的路由 content-type 里必须含 `multipart/form-data`。要撞它得让类型推导只看第一个字段。',   # test_synth_file_route_is_multipart
        ('tests/test_route_facts.py',
         'with pytest.raises(KeyError):',
         0):
            '对未知 op 反问字段必须抛 `KeyError`，不返回空列表 —— 静默返回空等于调用方拿不到「这个 op 根本不存在」。要撞它得加一个默认返回。',   # test_form_fields_raises_on_unknown_operation
        ('tests/test_route_facts.py',
         'assert recomputed == cached, ( "同一 app 重算出的文档与缓存值不同 —— 本层状态被调用方污染了" "（多把锁共用本层，谁先跑就会决定另一把看到什么）")',
         0):
            '共用层的输出必须是 app 的**纯函数**：同一 app 重算出的文档与缓存值相同（多把锁共用本层，谁先跑就会决定另一把看到什么）。要撞它得让本层状态被调用方污染（缓存里存可变对象并就地改）。',   # test_shared_layer_output_is_a_pure_function_of_the_app
        # ── tests/test_text_failure_messages.py ──
        ('tests/test_text_failure_messages.py',
         'raise AssertionError("core/text_manager.py 没有 import TEXT_FAILURE_MESSAGES —— 锁的定位锚点没了")',
         0):
            '定位锚点：`core/text_manager.py` 必须 import `TEXT_FAILURE_MESSAGES` —— 没有它，本文件所有「文案只有一处出处」的判据都在对着空集跑。要撞它得把表改成别的引用方式或删掉那个 import。',   # _table_alias
        ('tests/test_text_failure_messages.py',
         'assert isinstance(exc, ast.Call) and exc.args, ( f"{node.lineno}: raise 的不是带实参的异常构造调用：{ast.unparse(exc)[:80]}"',
         0):
            '`raise` 的实参必须是**带实参的异常构造调用**（`_MSG["k"].format(...)` 这类），否则本层判断不了「文案从哪来」。要撞它得写 `raise X` 或 `raise _MSG["k"]`（不带实参）。',   # _raise_arg
        ('tests/test_text_failure_messages.py',
         'assert not bad, ( "text_manager.py 的 raise 实参必须是 `_MSG[\\"key\\"]`（可 .format(...)）形态。" "出现字面量 = 文案又多了一个出处；出现库对象 ',
         0):
            '`text_manager.py` 的 raise 实参必须是 `_MSG["key"]`（可 `.format(...)`）形态：出现**字面量** = 文案又多了一个出处；出现**库对象** = 缺陷 39 复发（库原文上屏）。要撞它得在 raise 里写一句硬编码文案。',   # test_l1_every_raise_arg_is_a_table_lookup
        ('tests/test_text_failure_messages.py',
         'assert not offenders, ( "库的异常对象被格式化进了 raise 实参 —— 缺陷 39 复发。实际：\\n " + "\\n ".join(offenders))',
         0):
            '专门盯「库的异常对象被格式化进 raise 实参」那一类 —— 缺陷 39 的原形。要撞它得把 `{e}` 拼进 `_MSG[...]` 的实参里。',   # test_l1_no_library_exception_reaches_any_raise_arg
        ('tests/test_text_failure_messages.py',
         'assert used - table == set(), f"用了表里没有的键：{sorted(used - table)}"',
         0):
            '用了表里没有的键 = 文案没有出处。要撞它得在源码里写 `_MSG["不存在的键"]`。',   # test_l2_used_keys_exactly_match_the_table
        ('tests/test_text_failure_messages.py',
         'assert table - used == set(), f"表里有没人用的陈旧键：{sorted(table - used)}"',
         0):
            '表里有没人用的陈旧键 = 表在膨胀。要撞它得往 `TEXT_FAILURE_MESSAGES` 加一个没人引用的键。',   # test_l2_used_keys_exactly_match_the_table
        ('tests/test_text_failure_messages.py',
         'assert n >= _MIN_TABLE_RAISES, ( f"L1 只判到 {n} 条表查询形态的 raise，少于预期的 {_MIN_TABLE_RAISES} —— " "扫描范围有盲区（别名没解析对 / 文',
         0):
            '空转先验：L1 只判到 n 条表查询形态的 raise，少于 `_MIN_TABLE_RAISES` 就说明扫描有盲区（别名没解析对 / 文案换了写法）。要撞它得把 raise 换成扫描认不出的形态，或删掉几条 raise。',   # test_l4_scan_is_not_vacuous
        ('tests/test_text_failure_messages.py',
         'assert ops, "一条表单 op 都没枚举到 —— 扫描面失效（spec 生成 / content-type 判据坏了）"',
         0):
            '空转先验：一条表单 op 都没枚举到 ⇒ 扫描面失效（spec 生成 / content-type 判据坏了）。要撞它得让 app 的 OpenAPI 里不再有 Form 路由。',   # test_l5_scan_is_not_vacuous
        ('tests/test_text_failure_messages.py',
         'assert with_file_field, ( "没有任何表单 op 带文件字段 —— 「正文只能走文件字段」这条断言失去了对象，是假绿")',
         0):
            '空转先验：「正文只能走文件字段」这条断言必须有对象 —— 没有任何表单 op 带文件字段时它是对空集断言（假绿）。要撞它得删掉文件上传端点。',   # test_l5_scan_is_not_vacuous
        ('tests/test_text_failure_messages.py',
         'assert _observed_non_file_fields(), ( "现场一个非文件 Form 字段都数不出来 —— 主判据在对空集断言（假绿）")',
         0):
            '空转先验：现场一个非文件 Form 字段都数不出来 ⇒ 主判据在对空集断言。要撞它得把所有非文件 Form 字段删掉。',   # test_l5_scan_is_not_vacuous
        ('tests/test_text_failure_messages.py',
         'assert r.status_code == 400, r.text',
         0):
            '非法字节必须得到 **400**（不是 500、不是 200）。要撞它得让 `pdf_open_failed` 在路由层被映射成别的状态码。',   # test_l6_bad_pdf_is_rejected_with_the_open_failure_message
        ('tests/test_text_failure_messages.py',
         'assert r.json()["detail"] == TEXT_FAILURE_MESSAGES["pdf_open_failed"]',
         0):
            '400 的 detail 必须是**本仓表里那一句**（不夹带库原文）。要撞它得让原始异常直接上屏。',   # test_l6_bad_pdf_is_rejected_with_the_open_failure_message
        ('tests/test_text_failure_messages.py',
         'assert r.status_code == 400, r.text',
         1):
            '同一件事（非法 PDF → 400）在 L3 那条路径上的重复判据 —— 两条路径的异常映射是分开写的，所以两条都要守。要撞它得改 L3 这条路的状态码映射。',   # test_l3_bad_pdf_screens_table_wording_and_logs_the_original
        ('tests/test_text_failure_messages.py',
         'assert detail == TEXT_FAILURE_MESSAGES["pdf_open_failed"], detail',
         0):
            'L3：上屏 detail 必须恰好是 `pdf_open_failed` 那一句（防「本仓的句子 + 库的句子」叠成双包）。要撞它得让内层异常消息被拼上去。',   # test_l3_bad_pdf_screens_table_wording_and_logs_the_original
        ('tests/test_text_failure_messages.py',
         'assert leak not in detail, f"库原文漏上屏：{detail!r}"',
         0):
            '库原文（`Failed to open file` 一类）**不得**出现在上屏 detail 里 —— 缺陷 39 的泄漏方向。要撞它得把原始异常消息直接当 detail。',   # test_l3_bad_pdf_screens_table_wording_and_logs_the_original
        ('tests/test_text_failure_messages.py',
         'assert "PDF open failed" in out, f"原始诊断被收走却没进日志 = 从「泄漏」换成「瞎」：{out!r}"',
         0):
            '但原始诊断**必须**进日志：收走上屏却不记日志 = 从「泄漏」换成「瞎」。要撞它得删掉那行 print/日志。',   # test_l3_bad_pdf_screens_table_wording_and_logs_the_original
        ('tests/test_text_failure_messages.py',
         'assert "Failed to open file" in out, out',
         0):
            '日志里必须能看到库给的那句原文 —— 这才是「原始诊断没丢」的证据（只判「有日志」会让一句无关的日志把缺口盖住）。要撞它得改掉被打印的内容。',   # test_l3_bad_pdf_screens_table_wording_and_logs_the_original
        ('tests/test_text_failure_messages.py',
         'assert r.status_code == 400, r.text',
         2):
            '空 DOCX 必须得 400（不是 500）。要撞它得让 `docx_empty` 被映射成别的状态码。',   # test_l3_empty_docx_is_not_double_wrapped
        ('tests/test_text_failure_messages.py',
         'assert detail == TEXT_FAILURE_MESSAGES["docx_empty"], ( f"双包复发（上屏该只有本仓那一句）：{detail!r}")',
         0):
            '空 DOCX 的 detail 必须是表里的 `docx_empty`，不许双包（上屏该只有本仓那一句）。要撞它得让 python-docx 的消息被拼上去。',   # test_l3_empty_docx_is_not_double_wrapped
        ('tests/test_text_failure_messages.py',
         'assert "解析失败" not in detail, detail',
         0):
            'detail 里不得出现中间层的「解析失败」措辞 —— 双包复发。要撞它得让内层异常消息上屏。',   # test_l3_empty_docx_is_not_double_wrapped
        ('tests/test_text_failure_messages.py',
         'assert r.status_code == 400, r.text',
         3):
            '超长文本必须得 400。要撞它得让 `too_long` 被映射成别的状态码。',   # test_l3_oversized_text_screens_table_wording
        ('tests/test_text_failure_messages.py',
         'assert r.json()["detail"] == TEXT_FAILURE_MESSAGES["too_long"].format(limit_text="100 万")',
         0):
            '超长的 detail 必须是表里那一句、且 limit 被格式化进去（文案里的占位符要真的被填，不是原样上屏）。要撞它得跳过 `.format()`。',   # test_l3_oversized_text_screens_table_wording
        ('tests/test_text_failure_messages.py',
         'assert msg not in literals, f"表键 {key!r} 的文案在 text_manager.py 里又抄了一份"',
         0):
            '表键的文案在 `text_manager.py` 里不得再抄一份（文案唯一出处）。要撞它得在源码里复制一句表里的文案字面量。',   # test_l3_wording_has_a_single_source
        ('tests/test_text_failure_messages.py',
         'assert TEXT_FAILURE_MESSAGES[key].strip(), f"{key} 是空文案"',
         0):
            '表里每条文案必须是非空 prose（空文案上屏 = 用户看到空白错误）。要撞它得把某条文案改成空串或只有空白。',   # test_table_entries_are_nonempty_prose
    },
}

# 合计 84 条。


# ── 「理由是不是占位语」：一条机械判据，须标明层级与失效方向 ────────────────────
#
# 这是**③层字符串代理**，不是 0 层事实：0 层要问的是「清名单的人能否据此知道该补什么变异」，
# 那句话机器判不了。代理的可接受之处在**失效方向是红不是绿** —— 人在理由里写了这些词
# 就当场红，逼人看一眼；反过来，写得含糊但不含这些词的理由（如「不好写」）照样混得过去。
# 所以它**不是**这条要求的全部守卫，只是最后一道机械底线；真正的判据是复核（PR 里读理由）。
#
# **为什么还要加长度下限（实测踩过）。** 只按「出现这些词」判会**红在正确的理由上**：
# 本仓实测，`test_l3_oversized_text_screens_table_wording` 那条缺口的理由写的是
# 「文案里的**占位符**要真的被填，不是原样上屏」—— 「占位符」是 Python 格式化占位符这个
# **领域词**，理由完全正确，却被 `占位` 这个子串当场判红。这种红比漏判更坏：它逼人为了
# 绕开代理去改写正确的句子，代理就从「底线」退化成「措辞税」。
# 于是加长度下限：占位语的形状是**短**（「待补」「以后再补」「TBD」都在 8 字以内），
# 而真理由的形状是**长**（实测 84 条：最短 25 字、中位 75 字）。下限取 20，落在两者之间。
# 天花板明写：**一长段全是空话的理由照样混得过去** —— 这正是上面那句「不是全部守卫」，
# 复核仍是真正的判据。

_PLACEHOLDER_TOKENS = ("待补", "待定", "占位", "暂时", "以后", "TODO", "TBD", "FIXME")
_PLACEHOLDER_MAX_LEN = 20      # 见上：实测真理由最短 25 字，占位语都远短于 20


def placeholder_reasons(table):
    """理由里**短到等于没写**、且用的是占位词的条目 —— `policy_table.empty_reasons` 的下一个刻度。

    「没写理由」与「写了理由但等于没写」是两种，`empty_reasons` 只管前一种。两个都留着，
    是因为它们**失效方式不同**：前者让名单变成空壳，后者让名单变成一句正确的废话。
    """
    return {k for k, reason in table.items()
            if isinstance(reason, str)
            and len(reason) < _PLACEHOLDER_MAX_LEN
            and any(t in reason for t in _PLACEHOLDER_TOKENS)}


def unknown_drivers(table, drivers):
    """名单里点了**不存在**的驱动 —— 驱动改名/删除后留下的孤儿段。

    孤儿段不会被 `test_lock_coverage.py` 的参数化跑到（那个驱动的产物行没了），所以它
    既不红也不绿 —— 与方向 ② 是同一个病，只是在上一层：方向 ② 防「判别器补上了却留着
    名单条目」，这一条防「整段驱动都没了却留着名单」。
    """
    return set(table) - set(drivers)
