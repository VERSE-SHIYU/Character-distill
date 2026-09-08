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
}

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
            tc = {"id": "call_mock_1", "type": "function",
                  "function": {"name": tool,
                               "arguments": json.dumps({"query": "我们之间过往的交流与共同回忆"}, ensure_ascii=False)}}
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
    ap.add_argument("--preset", default=os.environ.get("PRESET", "fast"))
    args = ap.parse_args()
    apply_preset(args.preset)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[mock-llm] listening 127.0.0.1:{args.port} preset={args.preset} cfg={CFG}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
