"""聊天记录三层清洗管线 — 专为微信/QQ聊天记录格式设计。

输入格式: ``[YYYY-MM-DD] 发言人: 内容``（由 text_manager._parse_wechat_json 产出）
"""

from __future__ import annotations

import re


class ChatPreprocessor:
    """三层清洗：格式清洗 → 质量过滤 → 角色上下文提取。"""

    # 纯事务性短语 — 不体现人格特征
    TRANSACTIONAL = {
        "到了", "好的", "收到", "在哪", "嗯", "哈哈", "哦", "好", "行",
        "知道了", "明白", "ok", "OK", "来了", "走了", "拜拜", "再见",
        "谢谢", "不客气", "没事", "没关系", "对不起", "抱歉",
        "早", "晚安", "中午好", "下午好", "晚上好", "你好", "嗨",
        "是的", "对", "没错", "可以", "都行", "随便", "再说吧",
        "等一下", "稍等", "马上", "快了", "到了说",
    }

    # 短消息但体现人格的观点词
    OPINION_MARKERS = {
        "我觉得", "我喜欢", "我讨厌", "我恨", "我爱", "我想", "我希望",
        "烦死了", "不想", "讨厌", "开心", "难过", "生气", "害怕",
        "担心", "后悔", "羡慕", "嫉妒", "感动", "失望", "无语",
        "受不了", "太棒了", "真香", "绝了", "离谱", "恶心",
        "佩服", "崇拜", "看不起", "嫌弃",
        "不觉得", "不同意", "反对", "支持", "赞成",
        "想你", "爱你", "恨", "烦", "怕", "累",
    }

    # 系统消息匹配模式
    SYSTEM_PATTERNS = [
        re.compile(r"撤回了一条消息"),
        re.compile(r"邀请.*加入群聊"),
        re.compile(r"加入了群聊"),
        re.compile(r"退出了群聊"),
        re.compile(r"修改群名为"),
        re.compile(r"移出了群聊"),
        re.compile(r"开启了群禁言"),
        re.compile(r"关闭了群禁言"),
        re.compile(r"被设置为管理员"),
        re.compile(r"取消.*管理员"),
        re.compile(r"发起群语音"),
        re.compile(r"发起群视频"),
        re.compile(r"发起语音通话"),
        re.compile(r"发起视频通话"),
        re.compile(r"通话时长"),
        re.compile(r"已结束"),
        re.compile(r"对方已拒绝"),
        re.compile(r"发送了一个"),
        re.compile(r"红包"),
        re.compile(r"转账"),
        re.compile(r"对方正在输入"),
        re.compile(r"拍了拍"),
    ]

    # 纯标点/无内容模式
    PUNCTUATION_ONLY = re.compile(r'^[？?！!…\.\,，。、\s]+$')

    # 消息行格式: [YYYY-MM-DD] 或 [YYYY-MM-DD HH:MM] 发言人: 内容
    MSG_LINE = re.compile(r'^\[(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2})?\]\s*([^:：]+)[：:]\s*(.*)')
    # 无日期前缀的备用格式: 发言人: 内容
    MSG_LINE_NO_DATE = re.compile(r'^([^:：]+)[：:]\s*(.*)')

    @classmethod
    def _parse_msg(cls, line: str):
        """Parse a message line, returning (date_or_None, speaker, content) or None."""
        m = cls.MSG_LINE.match(line)
        if m:
            return m.group(1), m.group(2).strip(), m.group(3).strip()
        m = cls.MSG_LINE_NO_DATE.match(line)
        if m:
            return None, m.group(1).strip(), m.group(2).strip()
        return None

    # 图片/语音/视频/表情标记
    MEDIA_MARKERS = [
        "[图片]", "[语音]", "[视频]", "[表情包]", "[表情]",
        "[文件]", "[链接]", "[小程序]", "[动画表情]",
        "[Video]", "[Image]", "[Voice]", "[File]",
        "<图片>", "<语音>", "<视频>",
    ]

    def preprocess(self, text: str, target_character: str = "") -> str:
        """三层清洗，返回清洗后的文本。"""
        orig_lines = len(text.split("\n"))
        text = self._layer0_format_clean(text)
        text = self._layer1_quality_filter(text)
        if target_character:
            text = self._layer2_character_context(text, target_character)
        new_lines = len(text.split("\n")) if text else 0
        ratio = f"{new_lines / orig_lines * 100:.1f}%" if orig_lines > 0 else "0%"
        print(f"[ChatPreprocessor] {orig_lines} lines → {new_lines} lines ({ratio})")
        return text

    # ------------------------------------------------------------------
    # Layer 0: 日期去重 + 删系统消息 + 删媒体标记 + 删纯标点
    # ------------------------------------------------------------------

    def _layer0_format_clean(self, text: str) -> str:
        lines = text.split("\n")
        cleaned: list[str] = []
        seen_dates: set[str] = set()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 纯标点/空白 → 删除
            if self.PUNCTUATION_ONLY.match(stripped):
                continue

            # 长度 ≤ 1 的行 → 删除
            if len(stripped) <= 1:
                continue

            # 系统消息 → 删除
            if self._is_system_message(stripped):
                continue

            # 媒体标记 → 删除整行
            if self._is_media_only(stripped):
                continue

            # 解析消息行
            parsed = self._parse_msg(stripped)
            if parsed:
                date_str, speaker, content = parsed

                # 清除内容中的媒体标记
                for marker in self.MEDIA_MARKERS:
                    content = content.replace(marker, "")
                content = content.strip()
                if not content:
                    continue

                # 日期去重：同一天只保留第一条
                if date_str and date_str in seen_dates:
                    cleaned.append(f"{speaker}: {content}")
                else:
                    if date_str:
                        seen_dates.add(date_str)
                        cleaned.append(f"[{date_str}] {speaker}: {content}")
                    else:
                        cleaned.append(f"{speaker}: {content}")
            else:
                # 非标准格式行仍保留
                cleaned.append(stripped)

        return "\n".join(cleaned)

    def _is_system_message(self, line: str) -> bool:
        parsed = self._parse_msg(line)
        if not parsed:
            return False
        for pat in self.SYSTEM_PATTERNS:
            if pat.search(line):
                return True
        return False

    def _is_media_only(self, line: str) -> bool:
        parsed = self._parse_msg(line)
        if not parsed:
            return False
        content = parsed[2]
        if not content:
            return False
        for marker in self.MEDIA_MARKERS:
            content = content.replace(marker, "")
        return content.strip() == ""

    # ------------------------------------------------------------------
    # Layer 1: 保留有人格信息的消息
    # ------------------------------------------------------------------

    def _layer1_quality_filter(self, text: str) -> str:
        lines = text.split("\n")
        msg_entries: list[dict] = []

        for i, line in enumerate(lines):
            parsed = self._parse_msg(line)
            if not parsed:
                continue
            _, speaker, content = parsed

            is_opinion = self._has_opinion(content)
            is_question = content.rstrip().endswith("?") or content.rstrip().endswith("？")
            is_transactional = self._is_transactional(content)
            is_long = len(content) >= 5

            msg_entries.append({
                "line_index": i,
                "speaker": speaker,
                "content": content,
                "is_opinion": is_opinion,
                "is_question": is_question,
                "is_transactional": is_transactional,
                "is_long": is_long,
                "keep": False,  # to be decided
                "line": line,
            })

        # Pass 1: mark keepers
        for j, entry in enumerate(msg_entries):
            if entry["is_long"] or entry["is_opinion"]:
                entry["keep"] = True

        # Pass 2: keep Q&A pairs (question + next reply)
        for j in range(len(msg_entries) - 1):
            if msg_entries[j]["is_question"]:
                msg_entries[j]["keep"] = True
                msg_entries[j + 1]["keep"] = True

        # Pass 3: delete pure transactional (only if not already kept)
        for entry in msg_entries:
            if entry["is_transactional"] and not entry["is_opinion"] and not entry["is_long"]:
                entry["keep"] = False

        return "\n".join(e["line"] for e in msg_entries if e["keep"])

    def _has_opinion(self, content: str) -> bool:
        for marker in self.OPINION_MARKERS:
            if marker in content:
                return True
        return False

    def _is_transactional(self, content: str) -> bool:
        c = content.strip()
        if c in self.TRANSACTIONAL:
            return True
        # 也匹配 "xxx到了" / "xxx好的" 等变体
        if len(c) <= 3 and any(t in c for t in self.TRANSACTIONAL):
            return True
        return False

    # ------------------------------------------------------------------
    # Layer 2: 只保留目标角色相关的对话 + 前后3条上下文
    # ------------------------------------------------------------------

    def _layer2_character_context(self, text: str, target: str) -> str:
        if not target:
            return text

        lines = text.split("\n")
        msg_entries: list[dict] = []

        for i, line in enumerate(lines):
            parsed = self._parse_msg(line)
            if parsed:
                _, speaker, content = parsed
                msg_entries.append({
                    "line_index": i,
                    "speaker": speaker,
                    "content": content,
                    "line": line,
                })
            else:
                msg_entries.append({
                    "line_index": i,
                    "speaker": "",
                    "content": line,
                    "line": line,
                })

        keep_indices: set[int] = set()

        for j, entry in enumerate(msg_entries):
            speaker = entry["speaker"]
            content = entry["content"]

            # 目标角色本人的发言
            if speaker == target:
                keep_indices.add(j)
                # 前后各3条上下文
                for offset in range(-3, 4):
                    ctx_idx = j + offset
                    if 0 <= ctx_idx < len(msg_entries):
                        keep_indices.add(ctx_idx)

            # 别人提到 target 名字
            if speaker != target and target in content:
                keep_indices.add(j)
                # 前后各1条上下文
                for offset in range(-1, 2):
                    ctx_idx = j + offset
                    if 0 <= ctx_idx < len(msg_entries):
                        keep_indices.add(ctx_idx)

        return "\n".join(msg_entries[i]["line"] for i in sorted(keep_indices))
