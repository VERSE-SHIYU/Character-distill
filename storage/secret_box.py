# -*- coding: utf-8 -*-
"""API key 加解密的**唯一**实现（缺陷 56 的同族：一件事两份实现）。

两个 store 原先各写一份私有的「取 Fernet 实例」函数（连密钥派生都逐字相同），各自的解密闭包也各写一份。
两份实现的代价不是「多几行」：任何一侧改了算法或派生方式，另一侧读出来的就是乱码，而这种
分叉在测试里只表现为「解密失败」，看不出是哪一侧错了。收成一份之后，
`git grep "Fernet(" -- storage` 只命中本文件 —— 判据见 AGENTS.md 缺陷 56。

密钥派生：`FERNET_KEY` 优先；没有就用 `JWT_SECRET` 经 sha256 派生一个。两个都没有时
**拒绝**（抛 RuntimeError，点名变量），不退到任何内置常量。

本模块只管「值 ↔ 密文」。空值短路、把失败包成 `StoreError` 是各 store 的事，它们对
错误的分类不同，故留在调用方。
"""

from __future__ import annotations

import base64
import os
from hashlib import sha256


def _key():
    key = os.getenv("FERNET_KEY")
    if not key:
        secret = os.getenv("JWT_SECRET")
        if not secret:
            raise RuntimeError("FERNET_KEY 或 JWT_SECRET 必须设置才能加解密 API key，拒绝使用不安全默认值")
        key = base64.urlsafe_b64encode(sha256(secret.encode()).digest())
    return key


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(_key())


def encrypt_secret(value: str) -> str:
    """明文 → 可落库的密文。"""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """密文 → 明文。"""
    return _fernet().decrypt(value.encode()).decode()
