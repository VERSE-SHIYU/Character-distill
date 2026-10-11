# -*- coding: utf-8 -*-
"""「再次分享」的返回 · 目标检查。

现状（main 4fa3e9c）：首次发布返回里有广场卡的 `card_id`，更新发布（界面上的「再次分享」）
只返回 `version`。界面上这两个按钮是同一段代码，按拿没拿到 `card_id` 判成功
（`web/frontend/src/components/CharCard.jsx` 里「成功判据是拿到 card_id」那一行），所以再次
分享实际已经更新，界面却显示「发布失败」。

目标：先分享、再分享同一张卡，两次返回里的 `card_id` 都是那张广场卡的 id。

走真实路由与真 PG，夹具用 `test_market_publish_policy` 的那一套，不另抄。
"""
from __future__ import annotations

import json

from conftest import PG_ENV
from test_market_publish_policy import (  # noqa: F401  （fixture 靠名字注入）
    _CARD, _card, _clean_review, _client, _publish, owner, store,
)

pytestmark = PG_ENV.skipif("再次分享的返回用例")


async def test_sharing_again_returns_the_card_id_the_first_share_returned(store, owner):
    cid = await _card(store, owner)
    first = await _publish(store, owner, cid)
    assert first.status_code == 200, first.text
    market_id = first.json()["card_id"]

    async with _client(store, owner) as c:
        again = await c.put(f"/api/market/{market_id}/publish", json={
            "card_json": json.dumps({**_CARD, "identity": "改过的"}, ensure_ascii=False),
            "publish_message": "第二次分享"})

    assert again.status_code == 200, again.text
    assert again.json()["card_id"] == market_id, "界面按 card_id 判成功，再次分享也得带"
    assert again.json()["version"], "版本号照旧返回"
