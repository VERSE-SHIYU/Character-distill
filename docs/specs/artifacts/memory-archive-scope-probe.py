"""真 mem0ai==2.0.20 + 本地 qdrant + 假 embedder：核实 run_id 的隔离、列举上限、删除、改标。不联网、不调模型。"""
import hashlib, os, sys, tempfile, json
os.environ.setdefault("MEM0_TELEMETRY", "False")
from qdrant_client import QdrantClient
from mem0 import Memory

class FakeEmb:
    def embed(self, text, *a, **k):
        h = hashlib.sha256(text.encode()).digest()
        v = [(h[i % 32] - 127.5) / 127.5 for i in range(1024)]
        return v
    def embed_batch(self, texts, *a, **k):
        return [self.embed(t) for t in texts]
class NoLLM:
    def generate_response(self, *a, **k):
        raise RuntimeError("LLM must not be called")

d = tempfile.mkdtemp()
client = QdrantClient(path=d + "/q")
mem = Memory.from_config({
    "vector_store": {"provider": "qdrant", "config": {"client": client, "embedding_model_dims": 1024}},
    "llm": {"provider": "openai", "config": {"model": "x", "api_key": "unused"}},
    "embedder": {"provider": "openai", "config": {"model": "x", "api_key": "unused", "embedding_dims": 1024}},
    "history_db_path": d + "/h.db",
})
mem.llm = NoLLM(); mem.embedding_model = FakeEmb()

def texts(res):
    res = res.get("results", res) if isinstance(res, dict) else res
    return sorted(m["memory"] for m in res)

CARD = "card1"
mem.add("旧记忆：没有存档标记", user_id=CARD, infer=False)
mem.add("A 存档里说的事", user_id=CARD, run_id="sessA", infer=False)
mem.add("B 存档里说的事", user_id=CARD, run_id="sessB", infer=False)
mem.add("群里说的事", user_id=CARD, run_id="group:g1", infer=False)
mem.add("别的卡的事", user_id="card2", run_id="sessA", infer=False)

print("1 get_all card only      :", texts(mem.get_all(filters={"user_id": CARD})))
print("2 get_all card+sessA     :", texts(mem.get_all(filters={"user_id": CARD, "run_id": "sessA"})))
print("3 get_all card+sessB     :", texts(mem.get_all(filters={"user_id": CARD, "run_id": "sessB"})))
print("4 search card+sessA      :", texts(mem.search("A 存档里说的事", filters={"user_id": CARD, "run_id": "sessA"}, top_k=10, threshold=0.0)))
print("5 search card+sessB q=A  :", texts(mem.search("A 存档里说的事", filters={"user_id": CARD, "run_id": "sessB"}, top_k=10, threshold=0.0)))
print("6 search card only  q=A  :", texts(mem.search("A 存档里说的事", filters={"user_id": CARD}, top_k=10, threshold=0.0)))

# 列举上限
for i in range(30):
    mem.add(f"批量 {i:02d}", user_id="cardN", run_id="s", infer=False)
print("7 get_all default top_k  :", len(mem.get_all(filters={"user_id": "cardN"})["results"]), "of 30")
print("7b get_all top_k=1000    :", len(mem.get_all(filters={"user_id": "cardN"}, top_k=1000)["results"]), "of 30")

# 改标：公开 update 不改身份键；向量库层 partial update 可以吗
old = [m for m in mem.get_all(filters={"user_id": CARD})["results"] if m["memory"].startswith("旧记忆")][0]
print("8 old payload keys       :", sorted(k for k in old.keys()), "run_id=", old.get("run_id"), "created_at=", old.get("created_at"))
try:
    mem.update(old["id"], metadata={"run_id": "sessA"})
    after = mem.get(old["id"])
    print("9 public update run_id   :", after.get("run_id"))
except Exception as e:
    print("9 public update raised   :", type(e).__name__, str(e)[:120])
try:
    mem.vector_store.update(vector_id=old["id"], payload={"run_id": "sessA"})
    after = mem.get(old["id"])
    print("10 vector_store.update   : run_id=", after.get("run_id"), "| memory kept=", after.get("memory"), "| created_at kept=", after.get("created_at") == old.get("created_at"))
    print("10b now in sessA         :", texts(mem.get_all(filters={"user_id": CARD, "run_id": "sessA"})))
    print("10c search sessA finds it:", "旧记忆：没有存档标记" in texts(mem.search("旧记忆：没有存档标记", filters={"user_id": CARD, "run_id": "sessA"}, top_k=10, threshold=0.0)))
except Exception as e:
    print("10 vector_store.update raised:", type(e).__name__, str(e)[:200])

# 删除
mem.delete_all(user_id=CARD, run_id="sessA")
print("11 after delete sessA    :", texts(mem.get_all(filters={"user_id": CARD})))
print("11b card2 untouched      :", texts(mem.get_all(filters={"user_id": "card2"})))
mem.delete_all(user_id=CARD)
print("12 after delete card     :", texts(mem.get_all(filters={"user_id": CARD})), "| card2:", texts(mem.get_all(filters={"user_id": "card2"})))
