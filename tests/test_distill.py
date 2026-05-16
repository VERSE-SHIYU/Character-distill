"""蒸馏引擎冒烟脚本。"""
from adapters.llm_adapter import LLMAdapter
from core.distiller import Distiller

llm = LLMAdapter()
d = Distiller(llm)

test_text = """
张三是个沉默寡言的人，但每次提到女儿小雨就会变得滔滔不绝。
他在公司是出了名的冷面总监，开会时从不给人留面子，说话像刀子一样。
但私下他会偷偷给楼下的流浪猫买罐头，还给它们取了名字。
他常说"做事要讲效率，别跟我扯没用的"，但自己的办公桌永远乱得像垃圾场。
李四是他唯一的朋友，两人大学就认识。张三嘴上说"李四那个蠢货"，但每次李四出事他第一个到。
上周李四问他借钱，他骂了半小时最后转了两万。
张三的前妻王芳两年前离开了他。他从不提这件事，但办公室抽屉里还放着结婚照。
"""

# 测试角色识别
chars = d.identify_characters(test_text)
print("识别到的角色：", chars)

# 测试蒸馏
card = d.distill(test_text, "张三")
print("\n角色卡：")
print(card.model_dump_json(indent=2, ensure_ascii=False))
