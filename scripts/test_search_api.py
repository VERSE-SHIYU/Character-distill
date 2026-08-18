"""Test search API directly."""
import asyncio
import httpx

BASE = "http://localhost:7860"


async def main():
    async with httpx.AsyncClient(base_url=BASE) as c:
        # Login
        r = await c.post("/api/auth/login", json={
            "username": "testadmin", "password": "test1234"
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
