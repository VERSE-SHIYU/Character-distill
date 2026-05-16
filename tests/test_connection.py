"""DeepSeek 连接冒烟脚本。"""
from adapters.llm_adapter import LLMAdapter

llm = LLMAdapter()
print(llm.chat("你是一个测试助手", [{"role": "user", "content": "说一句话证明连接成功"}]))
