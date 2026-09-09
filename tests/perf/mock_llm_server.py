# -*- coding: utf-8 -*-
"""本地 mock：DeepSeek(Chat) + DashScope(Embedding) 两个 OpenAI 兼容端点。

用于 ②④ 压测，剥离真实外部网络延迟，让归因落在自己系统上。

端点（与生产调用形态对齐，见文件尾「协议对照」）：
  POST /v1/chat/completions  支持 stream(SSE) 与非 stream
  POST /v1/embeddings        DashScope compatible-mode，维度对齐 text-embedding-v4
  GET  /health               就绪探针（压测/自验用，非 OpenAI 协议）

延迟模型：
  chat:   TTFT_MS(首 token 前等待) + 每 TOKEN_INTERVAL_MS 吐一个 token，
          输出 token 数 = min(请求 max_tokens, OUTPUT_TOKENS)
  embed:  固定 EMBED_LATENCY_MS
用「两档预设」区分瓶颈随外部延迟线性 / 卡在自身。

纯标准库，不改任何生产代码。生产 base_url 通过配置/参数指向本服务即可。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── 预设表 ─────────────────────────────────────────────
PRESETS = {
    "fast": {"ttft_ms": 200, "token_interval_ms": 10, "output_tokens": 64, "embed_latency_ms": 20},
    "slow": {"ttft_ms": 2000, "token_interval_ms": 50, "output_tokens": 64, "embed_latency_ms": 200},
}
EMBED_DIM = 1024  # 对齐 core/embeddings.py DashScopeEmbedding 默认 dimensions=1024

CFG = {
    "ttft_ms": int(os.environ.get("TTFT_MS", 200)),
    "token_interval_ms": int(os.environ.get("TOKEN_INTERVAL_MS", 10)),
    "output_tokens": int(os.environ.get("OUTPUT_TOKENS", 64)),
    "embed_latency_ms": int(os.environ.get("EMBED_LATENCY_MS", 20)),
    # 预留故障注入：MOCK_FAIL=chat:429 | chat:500 | chat:disconnect | embed:429 | ...
    "fail": os.environ.get("MOCK_FAIL", ""),
    # MOCK_TOOL=<tool_name>：首个 tools-bearing 非流式请求返回一次 tool_call（仿真 agent 路由决策）。
    # 后续（messages 已含 role="tool"）正常回 content —— 让 AgentLoop 恰好执行一次工具。
    "tool": os.environ.get("MOCK_TOOL", ""),
    # 阶段 B 决策轮故障注入：MOCK_DECISION_FAIL_RATE=<0-100> + MODE=raise|hang|blackhole
    # 只作用于 tools-bearing 决策请求（AgentLoop.chat_with_tools）。该轮故障 → AgentLoop 抛异常 →
    # 上层降级 legacy 纯生成（chat 仍 done）——测降级路径是否守恒端到端可用性。
    # raise=HTTP 500 立即；hang=先挂 MOCK_DECISION_FAIL_HANG_MS 再 500（自限界，不永久挂）；
    # blackhole（阶段 D1b）=接受请求后不吐任何字节（区别于 hang 会回 500），只靠 app 侧
    # per-attempt socket 读超时打断——证明 D1b create(timeout=min(ceiling, 剩余−margin))
    # 真能斩断单次阻塞，deadline 对单次请求也封顶。
    # 注：tool 执行走本地 ContextEngine（mem0 mock 恒空），不经 mock HTTP → 工具层故障本 rig 不可注入。
    "decision_fail_rate": int(os.environ.get("MOCK_DECISION_FAIL_RATE", 0)),
    "decision_fail_mode": os.environ.get("MOCK_DECISION_FAIL_MODE", ""),
    "decision_fail_hang_ms": int(os.environ.get("MOCK_DECISION_FAIL_HANG_MS", 8000)),
    # 阻塞工具探针（阶段 D tools.py 执行器泄漏 before 基线）：故障打在本 rig 唯一会
    # 被真实工具 handler 同步等待的 HTTP 上——mem0 search 的 query embed。
    #   embed_hang_ms>0 → /v1/embeddings 先挂 N ms 再回；app 侧 OpenAI client 读超时
    #     (8s) 前不吐字节 → _call_api 阻塞，tools.execute 的 fut.result(MEMORY_TIMEOUT=5s)
    #     先超时 → shutdown(cancel_futures) 杀不掉已启动 handler 线程 → 泄漏到 embed 结束。
    #   tool_query 覆盖决策轮 canned 的 search query 文本：探针给每请求一个 NOVEL 文本，
    #     使 app 侧按文本 keyed 的共享 embed LRU 必 miss → 才真发 HTTP 挂起。
    "embed_hang_ms": int(os.environ.get("MOCK_EMBED_HANG_MS", 0)),
    "tool_query": os.environ.get("MOCK_TOOL_QUERY", ""),
}

_BLACKHOLE_HOLD_MS = 30_000  # blackhole 档零字节悬挂时长（须 ≥ app 侧任一 per-attempt 超时上限）
# dict 写多线程下 GIL 原子；偶发双写同值无害。进程存活期间累计，重启即清。
_DECISION_VERDICT: dict[str, bool] = {}

def _sim(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def resolve_output_tokens(req: dict) -> int:
    """输出 token 数：请求给了 max_tokens 且更小则按它，否则按预设档。"""
    cap = CFG["output_tokens"]
    mt = req.get("max_tokens")
    if isinstance(mt, int) and mt > 0:
        return min(mt, cap) if cap else mt
    return cap


def content_tokens(n: int) -> list[str]:
    return ["mock-tok-%04d" % i for i in range(n)]  # 每 token 定长，字节可复现


# 蒸馏的「格式化/生成角色卡」LLM 调用会在 system prompt 里嵌入 CharacterCard 的
# model_json_schema（含顶层字段名 "personality_traits"）。map/reduce/analyze/chat 的
# prompt 都不含该字段 → 用它对这类请求回一份合法角色卡 JSON，让 run_stream 端到端 done。
# 内容对压测无意义，只要过 CharacterCard.model_validate（仅 name 必填）。
CANNED_CARD = {
    "name": "阿明", "identity": "压测 mock 蒸馏角色",
    "personality_traits": ["沉稳（mock）", "念旧（mock）"],
    "speaking_style": {"tone": "平静", "sentence_pattern": "短句",
                       "catchphrases": [], "vocabulary_level": "中", "taboo_words": []},
    "values": ["守信"], "key_memories": ["（mock）"], "relationships": [],
    "inner_tensions": [], "background": "压测 mock 背景", "first_message": "你好。",
    "dialogue_examples": [], "emotional_patterns": [], "decision_style": "谨慎",
    "character_arc": [], "tags": [],
}
CANNED_CARD_JSON = json.dumps(CANNED_CARD, ensure_ascii=False)
_CARD_ANCHOR = '"personality_traits"'  # 仅出现在 CharacterCard schema 转储里


def is_card_request(req: dict) -> bool:
    joined = " ".join(
        str(m.get("content") or "") for m in (req.get("messages") or [])
        if isinstance(m, dict)
    )
    return _CARD_ANCHOR in joined


def chat_error(code: int, msg: str) -> tuple[int, bytes]:
    return code, json.dumps(
        {"error": {"message": msg, "type": "mock_" + str(code), "param": None, "code": str(code)}},
        ensure_ascii=False,
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 流式靠连接关闭收尾，免 chunked 复杂度

    def log_message(self, *a):  # 静默
        pass

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def _fail_injected(self, endpoint: str) -> bool:
        """命中 MOCK_FAIL 则已写好响应返回 True，否则 False。"""
        spec = CFG["fail"]
        if not spec or spec.split(":")[0] != endpoint:
            return False
        action = spec.split(":")[1]
        if action in ("429", "500"):
            body = chat_error(int(action), f"injected {action}")
            self.send_response(int(action))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body[1])
            return True
        if action == "disconnect":  # 直接断连：客户端见连接错误/读超时
            self.connection.close()
            return True
        return False

    def _decision_fault(self, req: dict) -> bool:
        """命中决策轮故障注入则写好 500 响应返回 True（rate/mode 见 CFG 注释）。

        裁决按 messages 体 key 记忆：同轮 SDK 重试（adapter 3× × SDK 2× = 至多 9 次同 body）共享
        同一裁决（同故障窗口），否则双重重试会把单轮故障稀释成 degraded≈R^9 而非 R。
        注意：压测各会话必须发不同文本（step4_load 消息内嵌会话号），否则 trajectories 相同的
        会话共享同一 key → 裁决碰撞（曾致 raise 档 100% degraded 的 bug）。每 key 一次独立随机抽签，
        无跨档滚动计数器的路径依赖。
        """
        rate = CFG["decision_fail_rate"]
        if rate <= 0:
            return False
        key = json.dumps(req.get("messages") or [], ensure_ascii=False, sort_keys=True)
        verdict = _DECISION_VERDICT.get(key)
        if verdict is None:
            verdict = random.random() * 100.0 < rate  # 每逻辑轮独立伯努利
            _DECISION_VERDICT[key] = verdict
        if not verdict:
            return False
        if CFG["decision_fail_mode"] == "hang":
            _sim(CFG["decision_fail_hang_ms"])
        elif CFG["decision_fail_mode"] == "blackhole":
            # 永不回：不吐任何字节悬挂连接（区别于 hang 会回 500）。app 侧 per-attempt socket
            # 读超时（D1b create timeout）打断后弃连；本 handler 睡满 hold 再关，线程占用有界。
            # mock 是一次性 rig，进程回收即清，不做优雅退出。
            _sim(_BLACKHOLE_HOLD_MS)
            self.connection.close()
            return True
        code, body = chat_error(500, f"injected decision {CFG['decision_fail_mode']} rate={rate}")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
        return True

    # ── 路由 ─────────────────────────────────────
    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "preset": CFG}).encode())
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/admin/set":
            # 压测中途热改故障参数（避免逐档重启）：body {"decision_fail_rate":0-100,
            # "decision_fail_mode":"raise|hang"}. 仅改 CFG 运行时项，不改 preset。
            try:
                d = self._read_body()
                for k in ("decision_fail_rate", "decision_fail_mode", "decision_fail_hang_ms",
                          "embed_hang_ms", "tool_query"):
                    if k in d and d[k] is not None:
                        CFG[k] = int(d[k]) if isinstance(d[k], (int, float)) else d[k]
            except Exception as exc:
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "cfg": CFG}).encode())
            return
        if path.endswith("/chat/completions"):
            self._handle_chat()
        elif path.endswith("/embeddings"):
            self._handle_embed()
        else:
            self.send_error(404)

    # ── chat/completions ─────────────────────────
    def _handle_chat(self):
        if self._fail_injected("chat"):
            return
        req = self._read_body()
        if is_card_request(req):
            # 角色卡生成请求：回合法 JSON。切成小块走同一流式管道（等价 token 数很少）。
            if req.get("stream"):
                self._handle_chat_stream([CANNED_CARD_JSON[i:i + 30] for i in range(0, len(CANNED_CARD_JSON), 30)])
            else:
                self._handle_chat_single([CANNED_CARD_JSON], req)
            return
        n = resolve_output_tokens(req)
        tokens = content_tokens(n)
        if req.get("stream"):
            self._handle_chat_stream(tokens)
        else:
            self._handle_chat_single(tokens, req)

    def _sse(self, obj: dict) -> None:
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _handle_chat_stream(self, tokens: list[str]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        _sim(CFG["ttft_ms"])  # 首 token 前固定等待
        base = {"id": "mock-%d" % time.time_ns(), "model": "mock-chat", "created": int(time.time())}
        for i, t in enumerate(tokens):
            if i:  # 相邻 token 间隔
                _sim(CFG["token_interval_ms"])
            last = i == len(tokens) - 1
            self._sse({
                **base, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": t},
                             "finish_reason": "stop" if last else None}],
            })
        self._sse({  # usage 尾块：llm_adapter 消费 chunk.usage，必带空 choices
            **base, "object": "chat.completion.chunk", "choices": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
        })
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _handle_chat_single(self, tokens: list[str], req: dict) -> None:
        _sim(CFG["ttft_ms"] + CFG["token_interval_ms"] * len(tokens))  # 等价整段生成耗时
        base = {
            "id": "mock-%d" % time.time_ns(), "object": "chat.completion",
            "created": int(time.time()), "model": "mock-chat",
            "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
        }
        tool = CFG.get("tool", "")
        msgs = req.get("messages") or []
        already_tooled = any(isinstance(m, dict) and m.get("role") == "tool" for m in msgs)
        if tool and req.get("tools") and not already_tooled:
            # 决策轮：恰发一次工具调用；agent 执行完回填 role="tool" 后再来即走 content 分支
            if self._decision_fault(req):
                return  # 阶段 B：决策轮故障 → app chat_with_tools 抛 → 降级 legacy
            q = CFG.get("tool_query") or "我们之间过往的交流与共同回忆"
            tc = {"id": "call_mock_1", "type": "function",
                  "function": {"name": tool,
                               "arguments": json.dumps({"query": q}, ensure_ascii=False)}}
            msg = {"role": "assistant", "content": None, "tool_calls": [tc]}
            body = {**base, "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}]}
        else:
            text = " ".join(tokens)
            msg = {"role": "assistant", "content": text}
            body = {**base, "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode())

    # ── embeddings ───────────────────────────────
    def _handle_embed(self):
        if self._fail_injected("embed"):
            return
        req = self._read_body()
        _sim(CFG["embed_latency_ms"])
        _sim(CFG.get("embed_hang_ms", 0))  # 阻塞工具探针：卡住 handler 线程（见 CFG 注释）
        inp = req.get("input", [])
        if isinstance(inp, str):
            inp = [inp]
        dim = int(req.get("dimensions", EMBED_DIM))
        # 确定性向量（按文本哈希化），保证同文本同向量——便于缓存/去重验证
        data = []
        for i, t in enumerate(inp):
            seed = sum(t.encode("utf-8")) + 7
            v = [(seed * 31 + i * 17 + j * 13) % 1000 / 100.0 for j in range(dim)]
            data.append({"object": "embedding", "index": i, "embedding": v})
        body = {"object": "list", "data": data, "model": req.get("model", "mock-embed"),
                "usage": {"prompt_tokens": 0, "total_tokens": 0}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


def apply_preset(name: str) -> None:
    p = PRESETS.get(name)
    if not p:
        raise SystemExit(f"unknown preset: {name} (fast|slow)")
    for k, v in (("ttft_ms", p["ttft_ms"]), ("token_interval_ms", p["token_interval_ms"]),
                 ("output_tokens", p["output_tokens"]), ("embed_latency_ms", p["embed_latency_ms"])):
        if os.environ.get({  # 显式 env 覆盖预设
            "ttft_ms": "TTFT_MS", "token_interval_ms": "TOKEN_INTERVAL_MS",
            "output_tokens": "OUTPUT_TOKENS", "embed_latency_ms": "EMBED_LATENCY_MS",
        }[k], "") == "":
            CFG[k] = v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--preset", default=os.environ.get("PRESET", "fast"))
    args = ap.parse_args()
    apply_preset(args.preset)
    # Step 4 rig：app 在容器里经 host.docker.internal 访问 → mock 需绑 0.0.0.0
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mock-llm] listening {args.host}:{args.port} preset={args.preset} cfg={CFG}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
