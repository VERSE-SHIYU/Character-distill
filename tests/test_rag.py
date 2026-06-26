"""RAG 引擎冒烟脚本。"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from core.rag import RAGEngine


def main():
    config = {"chunk_size": 200, "chunk_overlap": 30, "top_k": 2}
    rag = RAGEngine(config)
    text = "张三在公司是出了名的冷面总监。他常说做事要讲效率。但私下他会偷偷给流浪猫买罐头。李四是他唯一的朋友，两人大学就认识。张三的前妻王芳两年前离开了他。"
    rag.index(text)
    results = rag.query("张三和李四的关系")
    print("检索结果：", results)


if __name__ == "__main__":
    main()
