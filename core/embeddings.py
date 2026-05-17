import os
import torch
from sentence_transformers import SentenceTransformer
from chromadb.api.types import EmbeddingFunction

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("ACCELERATE_CPU_DEVICE", "true")

class SafeEmbedding(EmbeddingFunction):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name, device="cpu")
        self.model.eval()
        for p in self.model.parameters():
            p.requires_grad = False

    def __call__(self, input: list[str]) -> list[list[float]]:
        if next(self.model.parameters()).device.type != "cpu":
            self.model.to("cpu")
        embeddings = self.model.encode(input, convert_to_numpy=True, device="cpu")
        return embeddings.tolist()

def create_safe_embedding_fn(model_name: str = "all-MiniLM-L6-v2") -> SafeEmbedding:
    return SafeEmbedding(model_name)
