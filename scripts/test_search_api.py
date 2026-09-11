"""Test search API directly."""
import asyncio
import os
import httpx

BASE = "http://localhost:7860"

# 守卫在流程入口：本模块体即流程（尾部 asyncio.run(main())，无 __main__ 门），
# 无凭据无关的可导入面，故「用凭据的那一刻」就是脚本启动那一刻。
if "TEST_PASSWORD" not in os.environ:
    raise SystemExit("缺少环境变量 TEST_PASSWORD —— 请先 export 后再跑（口令不得写入仓库）")


async def main():
    async with httpx.AsyncClient(base_url=BASE) as c:
        # Login
        r = await c.post("/api/auth/login", json={
            "username": "testadmin", "password": os.environ["TEST_PASSWORD"]
        })
        data = r.json()
        token = data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Search 1 via global-search
        r2 = await c.get("/api/market/global-search", params={"q": "testadmin"}, headers=headers)
        print(f"global-search 'testadmin': {r2.status_code}")
        print(r2.text[:600])

        # Search 2
        r3 = await c.get("/api/market/global-search", params={"q": "吴"}, headers=headers)
        print(f"\nglobal-search '吴': {r3.status_code}")
        print(r3.text[:600])

        # Search 3 - user with nickname
        r4 = await c.get("/api/market/global-search", params={"q": "测试昵称Alpha"}, headers=headers)
        print(f"\nglobal-search '测试昵称Alpha': {r4.status_code}")
        print(r4.text[:600])


asyncio.run(main())
