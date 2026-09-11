"""Test search API directly."""
import asyncio
import os
import httpx

BASE = "http://localhost:7860"

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
