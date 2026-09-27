#!/usr/bin/env python3
"""WP15：把本地验收产出的书（原文 + 名单）与卡片搬进目标节点的演示账号。

两个子命令，都**经 storage 层现有写入口**落库（不写裸 SQL）：

  export（本地，只读）
    python scripts/demo_seed.py export --text-id <id> --cards 宝玉,刘姥姥 --out bundle.json
    读原文与元数据、名单（含 characters_version）、指定几张卡的 card_json，打成一个 JSON
    包，附条数与 sha256。

  import（在目标节点的 app 容器内跑，默认只试运行）
    python scripts/demo_seed.py import --bundle bundle.json              # 只看将写入什么
    python scripts/demo_seed.py import --bundle bundle.json --apply      # 真写
    python scripts/demo_seed.py import --bundle bundle.json --apply \\
        --soft-delete-text-ids cb455edd7ce6,b42f3d89ace4

    目标账号只从环境变量 `TARGET_USERNAME` 来（先例 `scripts/migrate_data.py`），不写死
    id —— 账号名不进代码库。

零模型调用、不搬向量：向量由演示账号各开一次会话时现生成（蓝图 WP15）。

库由环境变量选，与 web 侧同一套（见 `storage.get_store`）：STORAGE_BACKEND / DATABASE_URL。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import HTTPException  # noqa: E402

from core.distiller import Distiller, text_fingerprint  # noqa: E402
from core.trash_service import soft_delete  # noqa: E402
from storage import get_store  # noqa: E402

BUNDLE_SCHEMA = 1
DEFAULT_BUNDLE_NAME = "demo-seed-bundle.json"


class DemoSeedError(RuntimeError):
    """前置条件不成立 —— 停下报告，不猜、不兜底。"""


# ── 包的指纹 ────────────────────────────────────────────────────────────────

def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bundle_sha256(bundle: dict) -> str:
    """包的指纹 = 除 `sha256` 自身外全部内容的规范化 JSON 的 sha256。

    覆盖全文而不是抽样：包要在三台机器之间搬，被截断的包必须当场认出来，而不是导入半本。
    """
    payload = {k: v for k, v in bundle.items() if k != "sha256"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _card_equal(a: str, b: str) -> bool:
    """两张卡是否同内容。解析成对象再比 —— 键序 / 空白的差异不该判成「不一致」。"""
    try:
        return json.loads(a) == json.loads(b)
    except (TypeError, ValueError):
        return a == b


# ── export ──────────────────────────────────────────────────────────────────

async def build_bundle(storage, text_id: str, card_names: list[str]) -> dict:
    """本地只读：把一本的原文、名单、指定几张卡打成一个包。"""
    text = await storage.get_text_unscoped(text_id)
    if not text:
        raise DemoSeedError(f"文本不存在：{text_id}")
    if text.get("deleted_at"):
        raise DemoSeedError(f"文本已在回收站，不导出：{text_id}")
    owner = text.get("user_id") or ""
    if not owner:
        raise DemoSeedError(f"文本没有归属（user_id 为空），不导出：{text_id}")

    # 版本用本机运行代码的常量取，不写死数字：导入端核对的是同一个常量，两边一起变。
    version = Distiller.IDENTIFY_VERSION
    characters = await storage.get_characters_owned(text_id, owner, version=version)
    if characters is None:
        raise DemoSeedError(
            f"{text_id} 在版本 {version} 下读不到名单：要么没识别过，要么名单是别的版本。"
            f"包里必须带本机代码认得的名单 —— 否则导入后会被当无缓存、一开会话就重新识别。")

    cards = await storage.list_cards(text_id, owner)
    by_name = {c["name"]: c for c in cards}
    missing = [n for n in card_names if n not in by_name]
    if missing:
        raise DemoSeedError(
            f"{text_id} 名下没有这些卡：{missing}（名下现有：{sorted(by_name)}）")

    content = text.get("content") or ""
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "identify_version": version,
        "source": {
            "text_id": text_id,
            "user_id": owner,
            "filename": text.get("filename") or "",
            "title": text.get("title") or "",
            "description": text.get("description") or "",
            "text_type": text.get("text_type") or "story",
            "original_char_count": text.get("original_char_count"),
        },
        "text": content,
        "text_fingerprint": text_fingerprint(content),
        "characters": characters,
        "cards": [{"name": n, "card_json": by_name[n]["card_json"]} for n in card_names],
    }
    bundle["counts"] = {
        "characters": len(characters),
        "cards": len(bundle["cards"]),
        "chars": len(content),
    }
    bundle["sha256"] = bundle_sha256(bundle)
    return bundle


# ── import ──────────────────────────────────────────────────────────────────

async def _find_text_by_fingerprint(storage, uid: str, fingerprint: str) -> dict | None:
    """目标账号名下的同一原文（按内容指纹）。第二次导入靠它复用，而不是再建一本。

    `list_texts` 不含正文，故逐行 `get_text_owned` 取正文算指纹。演示账号名下文本很少，
    不值得为此给 storage 加一个只有本脚本用的按指纹查询。
    """
    for row in await storage.list_texts(uid):
        rec = await storage.get_text_owned(row["id"], uid)
        if rec and text_fingerprint(rec.get("content") or "") == fingerprint:
            return rec
    return None


async def _write_roster(storage, text_id: str, uid: str, version: int, characters: list) -> None:
    """写名单前先按属主取一次。

    `save_characters` 自己不校验归属（`storage/postgres_store.py:379` 只有 `WHERE id = $2`），
    单独调它会把名单写到别人的 text 行上。这里的校验不依赖调用方「已经确认过」。
    """
    if not await storage.get_text_owned(text_id, uid):
        raise DemoSeedError(f"名单归属校验失败：{text_id} 不在 {uid} 名下，不写")
    await storage.save_characters(text_id, characters, version=version)


async def _apply_soft_deletes(storage, ids: list[str], uid: str, *, apply: bool) -> list[dict]:
    """软删旧副本。

    归属门在 `core.trash_service.soft_delete` 里（`core/authz.py:21`：先按属主取，非属主
    拿不到记录即 404），**不直接调 `storage.delete_text`** —— 那个方法没有身份过滤，会删掉
    任何人的行。试运行只用同一判据（`get_text_owned`）预测写什么，一个字节都不动。
    """
    plan: list[dict] = []
    for tid in ids:
        if apply:
            try:
                await soft_delete("text", tid, {"id": uid, "role": "user"}, storage)
                plan.append({"id": tid, "action": "soft-delete", "reason": ""})
            except HTTPException as exc:
                plan.append({"id": tid, "action": "refused", "reason": str(exc.detail)})
            continue
        rec = await storage.get_text_owned(tid, uid)
        if not rec:
            plan.append({"id": tid, "action": "refused", "reason": "不属于目标账号（或不存在）"})
        elif rec.get("deleted_at"):
            plan.append({"id": tid, "action": "refused", "reason": "已在回收站"})
        else:
            plan.append({"id": tid, "action": "soft-delete", "reason": ""})
    return plan


async def run_import(storage, bundle: dict, *, target_username: str, apply: bool = False,
                     soft_delete_text_ids: list[str] | None = None) -> dict:
    """把包写进目标账号。默认只试运行，`apply=True` 才落库。"""
    soft_delete_ids = list(soft_delete_text_ids or [])

    if bundle.get("sha256") != bundle_sha256(bundle):
        raise DemoSeedError("包指纹对不上（传输中被截断或改过），不使用")

    user = await storage.get_user_by_username(target_username)
    if not user:
        raise DemoSeedError(f"目标账号不存在：{target_username}")
    uid = user["id"]

    bundle_version = bundle.get("identify_version")
    if bundle_version != Distiller.IDENTIFY_VERSION:
        raise DemoSeedError(
            f"版本不等：包里 {bundle_version}，本节点运行代码 {Distiller.IDENTIFY_VERSION}。"
            f"`get_characters_owned` 按版本精确比对，不等的名单会被当无缓存、一开会话就重新"
            f"识别覆盖 —— 停下，先让两端版本一致。")

    content = bundle.get("text") or ""
    fingerprint = text_fingerprint(content)
    existing = await _find_text_by_fingerprint(storage, uid, fingerprint)
    text_id = existing["id"] if existing else uuid.uuid4().hex[:12]

    # 复用的那一行就是要软删的那一行 = 包里的原文和旧副本是同一本。再往下走会先写名单再
    # 把它删掉，等于导入个空。属于「撞车」，停下人工定：旧副本先删、或者从参数里去掉。
    clash = [tid for tid in soft_delete_ids if tid == text_id]
    if clash:
        raise DemoSeedError(
            f"要软删的 id 与被复用的文本是同一条：{clash} —— 包里的原文与目标账号名下的旧副本"
            f"内容一致。若直接执行，刚导入的书会被删掉。先确认旧副本怎么处理再跑。")

    existing_by_name = {c["name"]: c for c in await storage.list_cards(text_id, uid)}
    card_plan = []
    for card in bundle.get("cards") or []:
        name = card["name"]
        old = existing_by_name.get(name)
        if old is None:
            card_plan.append({"name": name, "action": "create",
                              "id": uuid.uuid4().hex[:12], "card_json": card["card_json"]})
        elif _card_equal(old["card_json"], card["card_json"]):
            card_plan.append({"name": name, "action": "skip", "id": old["id"]})
        else:
            raise DemoSeedError(
                f"同名卡内容不一致：{name}（已有 {old['id']}）。不覆盖、不合并 —— 先人工比对。")

    report = {
        "dry_run": not apply,
        "target": {"username": user.get("username") or target_username, "id": uid},
        "text": {"action": "reuse" if existing else "create", "id": text_id,
                 "fingerprint": fingerprint, "chars": len(content)},
        "roster": {"action": "write", "version": bundle_version,
                   "count": len(bundle.get("characters") or [])},
        "cards": card_plan,
        "soft_delete": [],
    }

    if not apply:
        report["soft_delete"] = await _apply_soft_deletes(storage, soft_delete_ids, uid, apply=False)
        return report

    src = bundle.get("source") or {}
    await storage.save_text(
        text_id, src.get("filename") or "", content,
        title=src.get("title") or "", description=src.get("description") or "",
        text_type=src.get("text_type") or "story",
        original_char_count=src.get("original_char_count"), user_id=uid,
    )
    await _write_roster(storage, text_id, uid, bundle_version, bundle.get("characters") or [])
    for entry in card_plan:
        if entry["action"] == "create":
            await storage.save_card(entry["id"], text_id, entry["name"], entry["card_json"],
                                    user_id=uid)
    # 软删放在最后：写盘万一失败，旧副本还在，重跑即可。
    report["soft_delete"] = await _apply_soft_deletes(storage, soft_delete_ids, uid, apply=True)
    return report


# ── CLI ─────────────────────────────────────────────────────────────────────

def _parse_names(raw: str) -> list[str]:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


async def _run_export(args) -> int:
    bundle = await build_bundle(get_store(), args.text_id, _parse_names(args.cards))
    out = Path(args.out)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "identify_version": bundle["identify_version"],
                      "counts": bundle["counts"], "sha256": bundle["sha256"]},
                     ensure_ascii=False))
    return 0


async def _run_import(args) -> int:
    username = (os.environ.get("TARGET_USERNAME") or "").strip()
    if not username:
        raise DemoSeedError("缺少环境变量 TARGET_USERNAME（目标账号名）—— 账号名不写回脚本")
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    report = await run_import(get_store(), bundle, target_username=username, apply=args.apply,
                              soft_delete_text_ids=_parse_names(args.soft_delete_text_ids))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["dry_run"]:
        print("[demo-seed] 试运行，未写库。确认无误后加 --apply。", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="WP15：验收产出的书与卡片在两个节点落地")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="本地只读：打出一个 JSON 包")
    p_exp.add_argument("--text-id", required=True)
    p_exp.add_argument("--cards", required=True, help="逗号分隔的卡名，如 宝玉,刘姥姥")
    p_exp.add_argument("--out", default=DEFAULT_BUNDLE_NAME)

    p_imp = sub.add_parser("import", help="在目标节点的 app 容器内跑；默认只试运行")
    p_imp.add_argument("--bundle", required=True)
    p_imp.add_argument("--apply", action="store_true", help="真写库；不加只报告将写入什么")
    p_imp.add_argument("--soft-delete-text-ids", default="",
                       help="逗号分隔的旧副本 id；只软删属于目标账号的那几行")

    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_export(args) if args.cmd == "export" else _run_import(args))
    except DemoSeedError as exc:
        print(f"[demo-seed] 停下：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
