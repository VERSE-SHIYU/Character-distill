"""WeChat Work (企业微信) message encryption/decryption and API client."""

from __future__ import annotations

import hashlib
import struct
import time
from base64 import b64decode, b64encode

import httpx
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class WeChatCrypto:
    """WeChat Work message encryption/decryption per official spec.

    Protocol: random16 + msg_len(4 bytes) + msg + corp_id, AES-256-CBC, PKCS7.
    Reference: https://developer.work.weixin.qq.com/document/path/90968
    """

    BLOCK_SIZE = 32

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str) -> None:
        self._token = token
        self._key = b64decode(encoding_aes_key + "=")
        self._corp_id = corp_id.encode()

    def verify_signature(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> bool:
        """Verify msg_signature matches SHA1(token, timestamp, nonce, echostr)."""
        params = sorted([self._token, timestamp, nonce, echostr])
        raw = "".join(params).encode()
        computed = hashlib.sha1(raw).hexdigest()
        return computed == msg_signature

    def decrypt(self, encrypted: str) -> str:
        """Decrypt an encrypted message from WeChat.

        Returns the plaintext XML string.
        """
        cipher = AES.new(self._key, AES.MODE_CBC, iv=self._key[:16])
        raw = cipher.decrypt(b64decode(encrypted))

        # PKCS7 unpad
        pad = raw[-1]
        raw = raw[:-pad]

        # Parse: random16 + msg_len(4) + msg + corp_id
        content = raw[16:]
        msg_len = struct.unpack(">I", content[:4])[0]
        msg = content[4:4 + msg_len].decode("utf-8")
        corp_id = content[4 + msg_len:].decode("utf-8")

        if corp_id != self._corp_id.decode():
            raise ValueError(f"CorpID mismatch: expected {self._corp_id.decode()}, got {corp_id}")

        return msg

    def encrypt(self, msg: str) -> str:
        """Encrypt a reply message for WeChat. Returns base64 string."""
        random16 = get_random_bytes(16)
        msg_bytes = msg.encode("utf-8")
        msg_len = struct.pack(">I", len(msg_bytes))
        raw = random16 + msg_len + msg_bytes + self._corp_id

        # PKCS7 pad
        pad = self.BLOCK_SIZE - len(raw) % self.BLOCK_SIZE
        raw += bytes([pad] * pad)

        cipher = AES.new(self._key, AES.MODE_CBC, iv=self._key[:16])
        return b64encode(cipher.encrypt(raw)).decode()

    def make_signature(self, timestamp: str, nonce: str, encrypt_msg: str) -> str:
        """Generate msg_signature for an outgoing encrypted message."""
        params = sorted([self._token, timestamp, nonce, encrypt_msg])
        return hashlib.sha1("".join(params).encode()).hexdigest()


class WeChatAPI:
    """WeChat Work API client with access_token caching."""

    def __init__(self, corp_id: str, agent_id: str, secret: str) -> None:
        self._corp_id = corp_id
        self._agent_id = agent_id
        self._secret = secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def get_access_token(self) -> str:
        """Get access_token from WeChat API with 7000s cache."""
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token

        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
            f"?corpid={self._corp_id}&corpsecret={self._secret}"
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(url)
            data = resp.json()

        if data.get("errcode", -1) != 0:
            raise ConnectionError(f"WeChat token failed: {data.get('errmsg', 'unknown')}")

        self._token = data["access_token"]
        self._token_expires_at = now + 7000  # Cache 7000s (official 7200s)
        return self._token

    async def send_text(self, openid: str, content: str) -> dict:
        """Send a text message to a WeChat user via application API.

        Uses POST /cgi-bin/message/send with touser=openid.
        """
        token = await self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        body = {
            "touser": openid,
            "msgtype": "text",
            "agentid": int(self._agent_id),
            "text": {"content": content},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(url, json=body)
            return resp.json()
