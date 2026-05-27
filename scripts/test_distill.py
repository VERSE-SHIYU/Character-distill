"""Test distillation of a character from a stored text via the SSE API."""
import urllib.request
import json
import time
import sys

TEXT_ID = sys.argv[1] if len(sys.argv) > 1 else "172239fd232b"
CHAR_NAME = sys.argv[2] if len(sys.argv) > 2 else "汪东城"
API_URL = "http://127.0.0.1:7860/api/distill/run_stream"

start = time.time()
req = urllib.request.Request(
    API_URL,
    data=json.dumps({"text_id": TEXT_ID, "character_name": CHAR_NAME, "force": True}).encode(),
    headers={"Content-Type": "application/json"},
)

with urllib.request.urlopen(req, timeout=600) as resp:
    chunk_events = 0
    compression_count = 0
    token_count = 0
    errors = []

    while True:
        line = resp.readline().decode("utf-8")
        if not line:
            continue
        line = line.strip()
        if not line:
            continue
        if not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        status = data.get("status", "")
        now = time.time()

        if status == "analyzing":
            chunk_events += 1
            curr = data.get("current", 0)
            total = data.get("total", 0)
            print(f"[{now - start:.1f}s] Analyzing chunk {curr}/{total}", flush=True)
        elif status == "compressing":
            compression_count += 1
            curr = data.get("current", 0)
            total = data.get("total", 0)
            print(f"[{now - start:.1f}s] Compressing at chunk {curr}/{total}", flush=True)
        elif status == "done":
            print(f"[{now - start:.1f}s] DONE - card saved: {json.dumps(data, ensure_ascii=False)[:200]}", flush=True)
        elif "error" in data:
            errors.append(data)
            print(f"[{now - start:.1f}s] ERROR: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
        elif "token" in data:
            token_count += len(data.get("token", ""))

    elapsed = time.time() - start
    print(f"---")
    print(f"Total: {elapsed:.1f}s, Chunks: {chunk_events}, Compressions: {compression_count}, Tokens: {token_count}, Errors: {len(errors)}")
    if errors:
        for e in errors:
            print(f"Error: {json.dumps(e, ensure_ascii=False)[:300]}")
