"""Abstract storage interface."""

from __future__ import annotations

import time
import uuid as _uuid
from abc import ABC, abstractmethod


def _rebuild_store_error(op: str, message: str) -> "StoreError":
    """StoreError 的 pickle 还原口（缺陷 18）。

    不能走默认路径：BaseException.__reduce__ 用 self.args（已格式化的 message）回调
    cls(*args)，而 __init__ 要的是 (op, exc) —— 反序列化炸成 TypeError，把「查询失败」
    变成另一个异常。更关键的是原始 exc（如驱动层的 OperationalError）未必可序列化，
    不该被打包；故只带 op + 已渲染的 message 过河，重建时绕开 __init__ 的格式化。
    """
    obj = StoreError.__new__(StoreError)
    RuntimeError.__init__(obj, message)
    obj.op = op
    return obj


class StoreError(RuntimeError):
    """store 层的**可辨失败语义** —— 「查询/写入失败」，与「无数据」互斥。

    **不变量**：store 方法的空返回值只表示「无数据」，永不表示「失败」。任何失败一律经
    此异常上抛，由 `web/server.py` 的全局异常处理器记 traceback 并回 500 —— 失败可见。

    存在理由（缺陷 21，第七个同族形态）：此前两个 store 里有 154 处
    `except Exception: print(...); return <空值>`，让「查到了、结果是空」与「查询失败了」
    在返回值上**不可分辨**。于是 SQLite 新库缺 `remote_user_profiles` 表时，
    `get_conversations` 把 `OperationalError` 吞成空列表 —— 私信收件箱恒为空，
    不报错、不 500，日志里只有一行 print。前六个同族形态都是因为「只修出问题那处」
    才长出来的，故本轮全量收敛到这一个类型。

    「无数据」不走这里：查不到行是**正常返回**（None / 空列表），不是异常。
    """

    def __init__(self, op: str, exc: BaseException) -> None:
        """记下出错的 store 方法名；原始异常由调用点的 `from exc` 链上。"""
        super().__init__(f"storage operation {op!r} failed: {exc}")
        self.op = op

    def __reduce__(self):
        return (_rebuild_store_error, (self.op, str(self)))


def new_review_id() -> str:
    """Monotonic review_log id: ms-since-epoch prefix + random tail.

    ``review_log`` has no sequence column, so "latest decision" for a card is
    resolved by ``ORDER BY id DESC`` — the fixed-width ms prefix keeps ids
    lexically ordered without relying on the second-resolution created_at.
    """
    return f"{int(time.time() * 1000):013d}-{_uuid.uuid4().hex[:8]}"


class StorageBase(ABC):
    """Defines storage methods for texts, cards, sessions and messages."""

    @abstractmethod
    async def ping(self) -> None:
        """证明「后端此刻答得上话」，否则抛异常。成功即静默返回。

        语义：取一条连接**并在它上面执行一条语句**。连接失败、认证失败、语句失败
        一律原样上抛 —— 就绪要的是「库真的答复了」，不是「没抛错」。

        **为什么不能只取连接**（缺陷 41）：池可能交回一条已失效的连接（服务端重启过、
        socket 半开、凭据已轮换），而「取到了」这条路径本身完全正常。于是
        「拿到一条连接」与「库答得上话」是两个命题，只有后者是就绪 —— 探针取前者时，
        它在所有需要它红的场合都是绿的，与 `pg_isready` 只看端口同型。

        **语句必须触及存储本体** —— 「答得上话」的证明力全在这里，而两个后端够到本体的
        方式不同，故写法不同：

        - PG 的语句由客户端**发到服务端执行**，往返本身（连接有效、认证通过、服务端在跑）
          就是要证的东西，一句常量足够 —— 故 `SELECT 1`。
        - SQLite 是**进程内**库，没有「发出去」这一步。不带 FROM 的 SELECT 不读文件、
          不取锁、不开读事务，**结构上不可能失败**（实测：另一连接持 `BEGIN EXCLUSIVE`
          时它照样返回）—— 那句常量什么也没证明。故 SQLite 读 `sqlite_master`，
          那是一句真要去翻库文件的查询。

        同一个契约、两边语句不同，差异只在「怎样才算真的够到了存储」；判定标准是
        「这句语句是否可能失败」，不是「两个后端的 SQL 长得一样」。
        """

    @abstractmethod
    async def save_text(
        self, id: str, filename: str, content: str,
        *,
        title: str = "", description: str = "", text_type: str = "story",
        original_char_count: int | None = None, user_id: str = "",
    ) -> dict:
        """Save text content and return the stored record."""

    @abstractmethod
    async def get_text_unscoped(self, id: str) -> dict | None:
        """Get a text record by ID, with no ownership filter.

        Only for callers with no user context (storage internals, migration,
        background cleanup). Anything reachable from a logged-in request must
        use get_text_owned instead.
        """

    @abstractmethod
    async def get_text_owned(self, id: str, user_id: str) -> dict | None:
        """Get a text record by ID only if it belongs to user_id.

        Ownership is filtered in SQL. Returns None both when the text does not
        exist and when it belongs to someone else — the caller decides whether
        that becomes 404 or 403.
        """

    @abstractmethod
    async def list_texts(self, user_id: str = "") -> list[dict]:
        """List all text records."""

    @abstractmethod
    async def delete_text(self, id: str, keep_cards: bool = False) -> bool:
        """Soft-delete a text record by ID.

        When keep_cards=True, cards are detached (text_id → NULL) in the same
        transaction, so they and their chat sessions survive the soft-delete.
        """

    @abstractmethod
    async def detach_text_cards(self, id: str) -> int:
        """Detach all cards from a text by setting text_id to ''.

        Returns the number of cards detached. Cards become standalone
        characters with their chat sessions intact.
        """

    @abstractmethod
    async def hard_delete_text(self, id: str, keep_cards: bool = False) -> bool:
        """Permanently delete a text.

        When keep_cards=True, cards are detached (text_id → NULL) so they and
        their chat sessions survive the text deletion. When False (default),
        cards and their sessions are cascade-deleted.
        """

    @abstractmethod
    async def save_card(self, id: str, text_id: str, name: str, card_json: str, user_id: str = "") -> dict:
        """Save a character card and return the stored record."""

    @abstractmethod
    async def get_card_unscoped(self, id: str) -> dict | None:
        """Get a card record by ID, with no ownership filter.

        Only for callers with no user context (storage internals, admin
        cross-owner paths, public endpoints, MCP, scripts). Anything reachable
        from a logged-in request must use get_card_owned instead.
        """

    @abstractmethod
    async def get_card_owned(self, id: str, user_id: str) -> dict | None:
        """Get a card record by ID only if it belongs to user_id.

        Ownership is filtered in SQL. Returns None both when the card does not
        exist and when it belongs to someone else — the caller decides whether
        that becomes 404 or 403.
        """

    @abstractmethod
    async def list_cards(self, text_id: str, user_id: str = "") -> list[dict]:
        """List cards under a text ID."""

    @abstractmethod
    async def update_card(self, card_id: str, card_json: dict) -> dict:
        """Update a card's JSON and return the updated record."""

    # ── Card domain (market / fork / versions) ────────────
    #
    # 卡片域契约的其余部分（market / 版本 / fork / 举报 / 精选 / 跨境界）。
    # 判据是「两个实现都有 ∧ routers 或 core 调用」—— 由本文件声明后，
    # tests/test_storage_contract_shape.py 的形参表锁自动覆盖它们（判据面现算自
    # `__abstractmethods__`），不再需要另建一份方法名单。

    # ── Card avatars ──────────────────────────────────────

    @abstractmethod
    async def save_card_avatar(self, card_id: str, user_id: str, avatar_data: str) -> None:
        """Save a card's avatar. `user_id` is the caller's identity, not a filter hint."""

    @abstractmethod
    async def get_card_avatar_owned(self, card_id: str, user_id: str) -> str | None:
        """Get a card's avatar only if the card belongs to user_id."""

    @abstractmethod
    async def get_card_avatar_unscoped(self, card_id: str) -> str | None:
        """Get a card's avatar with no ownership filter.

        无身份读：仅 `fork_card` 深拷贝公开卡时用（原卡已验 public）。

        本方法是本节判据（「两个实现都有 ∧ routers/core 调用」）的唯一放宽项：它没有
        routers / core 调用点，只有 `fork_card` 在用。收进来的代价是它没有外部调用方
        背书 —— 将来若 `fork_card` 不再深拷贝头像，这条声明会先变成事实上的死契约。
        """

    # ── Card detail / listings ────────────────────────────

    @abstractmethod
    async def get_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Card detail with author info; works for market and non-market cards."""

    @abstractmethod
    async def get_market_card_detail(self, card_id: str, user_id: str) -> dict | None:
        """Market card detail (author info + publish metadata)."""

    @abstractmethod
    async def get_card_author_id(self, card_id: str) -> str | None:
        """Return the owner of a card, or None when it does not exist."""

    @abstractmethod
    async def get_author_cards(self, user_id: str, include_private: bool = False) -> list[dict]:
        """List a user's cards. Private cards are included only when asked for."""

    @abstractmethod
    async def list_standalone_cards(self, user_id: str) -> list[dict]:
        """List a user's cards that are attached to no text."""

    @abstractmethod
    async def list_deleted_cards(self, user_id: str) -> list[dict]:
        """List a user's soft-deleted cards (recycle bin)."""

    @abstractmethod
    async def get_liked_card_ids(self, user_id: str) -> list[str]:
        """Return the card IDs a user has liked."""

    @abstractmethod
    async def get_public_cards_by_text_id(self, text_id: str) -> list[dict]:
        """List the public cards under a text ID (no ownership filter — public face)."""

    # ── Market listings / search ──────────────────────────

    @abstractmethod
    async def list_public_cards(self, page: int = 1, page_size: int = 20, sort: str = "new", tag: str = "") -> list[dict]:
        """List public cards for the market (public face, no ownership filter)."""

    @abstractmethod
    async def list_public_cards_total(self, tag: str = "") -> int:
        """Count of public cards matching `list_public_cards`'s filters."""

    @abstractmethod
    async def search_public_cards(self, keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
        """Search public cards by keyword (public face)."""

    @abstractmethod
    async def search_public_cards_total(self, keyword: str) -> int:
        """Count of public cards matching `search_public_cards`."""

    @abstractmethod
    async def get_card_forks(self, card_id: str) -> list[dict]:
        """List users' public forks of a card.

        注意这不是「发布副本」关系：用户 fork 由 `forked_from` 承载，与作者自己的
        发布副本（`published_from`）是两列两关系。
        """

    # ── Publish / versions ────────────────────────────────

    @abstractmethod
    async def publish_card(self, card_id: str, user_id: str, description: str, tags: str, message: str, card_json_snapshot: str) -> str | None:
        """Publish a card to market by creating the author's own published copy.

        The copy is written as a separate public card whose `published_from` points at
        `card_id`; re-publishing reuses the existing copy in place. Returns the copy's
        card id, or None on failure. 他人对同一张卡的公开 fork 不是发布副本，不会被复用。
        """

    @abstractmethod
    async def update_published_card(self, card_id: str, user_id: str, card_json: str, description: str, tags: str, message: str, old_json: str) -> dict | None:
        """Update an already-published card, write the next version, return that version."""

    @abstractmethod
    async def get_card_versions_owned(self, card_id: str, user_id: str) -> list[dict]:
        """List a card's published versions only if the card belongs to user_id."""

    @abstractmethod
    async def update_card_version(self, card_id: str, version_id: str, publish_message: str) -> bool:
        """Update one version's message. Returns False when the version does not exist."""

    @abstractmethod
    async def delete_card_version(self, card_id: str, version_id: str) -> bool:
        """Delete one version. Returns False when it does not exist."""

    # ── Fork / visibility / lifecycle ─────────────────────

    @abstractmethod
    async def fork_card(self, card_id: str, new_id: str, new_user_id: str, new_text_id: str = "") -> dict | None:
        """Create a user's independent copy of a public card.

        Writes `forked_from` (the fork relation), never `published_from`. Returns the
        new card, or the existing fork when this user already forked that card+text.
        """

    @abstractmethod
    async def update_card_visibility(self, card_id: str, visibility: str) -> bool:
        """Set a card to 'public' or 'private'. Returns False on an invalid value.

        身份检查在路由层（调用点先取 `get_card_owned`）—— 本原语不做属主过滤。
        """

    @abstractmethod
    async def delete_card(self, card_id: str) -> bool:
        """Soft-delete a card (move to trash). Returns False when it does not exist."""

    @abstractmethod
    async def restore_card(self, card_id: str) -> bool:
        """Restore a soft-deleted card."""

    @abstractmethod
    async def purge_card(self, card_id: str) -> bool:
        """Permanently delete a card (irreversible).

        删除会连带清掉指向它的发布副本的 `published_from`（由 cards 上的
        BEFORE DELETE 触发器完成），副本本身存活。
        """

    @abstractmethod
    async def takedown_card(self, card_id: str) -> bool:
        """Admin takedown: hide a public card from the market."""

    @abstractmethod
    async def takedown_card_and_resolve_reports(self, card_id: str, resolver_id: str) -> bool:
        """Admin takedown that also resolves the card's pending reports."""

    # ── Card reports ──────────────────────────────────────

    @abstractmethod
    async def add_card_report(self, card_id: str, reporter_id: str, reason: str) -> bool:
        """Record a report against a card. Returns False when it is a duplicate."""

    @abstractmethod
    async def get_card_reports_grouped(self, status: str = "pending") -> list[dict]:
        """List card reports grouped by card (admin face, no ownership filter)."""

    @abstractmethod
    async def resolve_all_card_reports(self, card_id: str, resolver_id: str) -> bool:
        """Resolve every pending report on a card (admin face)."""

    # ── Featured cards ────────────────────────────────────

    @abstractmethod
    async def add_featured_card(self, card_id: str) -> str | None:
        """Feature a card on the market home. Returns the new row id, or None."""

    @abstractmethod
    async def remove_featured_card(self, id: str) -> bool:
        """Unfeature by featured-row id."""

    @abstractmethod
    async def get_featured_cards(self) -> list[dict]:
        """List featured cards in display order (public face)."""

    @abstractmethod
    async def reorder_featured_cards(self, ids: list[str]) -> None:
        """Persist a new display order for the given featured-row ids."""

    # ── Admin / cross-border sync / groups ────────────────

    @abstractmethod
    async def list_all_cards_admin(self) -> list[dict]:
        """List every card for the admin console (no ownership filter)."""

    @abstractmethod
    async def mark_card_synced(self, card_id: str) -> None:
        """Flag a card as already propagated across regions."""

    @abstractmethod
    async def mark_card_unsynced(self, card_id: str) -> None:
        """Flag a card as needing cross-region propagation."""

    @abstractmethod
    async def get_unsynced_cross_border_cards_unscoped(self, limit: int = 100) -> list[dict]:
        """Cross-border worker path: cards not yet propagated (no identity context)."""

    @abstractmethod
    async def upsert_remote_card(self, card_id: str, origin_region: str, user_id: str, name: str, card_json: str, avatar_data: str, market_description: str, market_tags: str, origin_created_at: str) -> None:
        """Upsert a card mirrored in from another region (worker path)."""

    @abstractmethod
    async def update_group_card_ids(self, id: str, card_ids: list[str]) -> None:
        """Replace a group session's card membership list."""

    @abstractmethod
    async def save_session(
        self, id: str, card_id: str, user_role: str, avatar_data: str, user_id: str = ""
    ) -> dict:
        """Save a chat session and return the stored record."""

    @abstractmethod
    async def get_session_unscoped(self, id: str) -> dict | None:
        """Get a session record by ID, with no ownership filter.

        Only for callers with no user context. Anything reachable from a
        logged-in request must use get_session_owned instead.
        """

    @abstractmethod
    async def get_session_owned(self, id: str, user_id: str) -> dict | None:
        """Get a session record by ID only if it belongs to user_id.

        Ownership is filtered in SQL. Returns None both when the session does
        not exist and when it belongs to someone else.
        """

    @abstractmethod
    async def update_session_avatar(self, session_id: str, user_id: str, avatar_data: str) -> bool:
        """Update session-level user avatar. Returns False if ownership check fails."""

    @abstractmethod
    async def get_group_session_unscoped(self, id: str) -> dict | None:
        """Get a group session by ID, with no ownership filter.

        **仅供管理员逃生口**：`core/authz.fetch_for_actor` 在属主取不到、且调用方是管理员
        （`core.roles.is_admin`）时才落到这里。任何登录用户可达的路径都该用
        `get_group_session_owned`。

        （`get_group_session_owned` 本身未在基类声明 —— 群会话原语整体只落在两个
        实现里；`_unscoped` 这一支进基类，是为了让「两后端签名必须逐格相同」那条锁
        管到它。改名/改签名漂移正是 trash_service 那次静默 9 天的成因。）
        """

    @abstractmethod
    async def update_group_avatar(self, group_id: str, user_id: str, avatar_data: str) -> bool:
        """Update group-level user avatar. Returns False if ownership check fails."""

    @abstractmethod
    async def list_sessions(
        self, keyword: str, character: str, text_id: str, page: int, page_size: int, user_id: str = "", card_id: str = ""
    ) -> dict:
        """List sessions with filters and pagination."""

    @abstractmethod
    async def delete_session(self, id: str) -> bool:
        """Soft-delete a session record by ID."""

    @abstractmethod
    async def clear_all_sessions(self, user_id: str = "") -> int:
        """Soft-delete all non-deleted sessions."""

    @abstractmethod
    async def list_trash_sessions(self, user_id: str = "") -> list[dict]:
        """List soft-deleted sessions (in trash)."""

    @abstractmethod
    async def restore_session(self, id: str) -> bool:
        """Restore a soft-deleted session."""

    @abstractmethod
    async def purge_trash(self, user_id: str = "") -> int:
        """Permanently delete all soft-deleted sessions."""

    @abstractmethod
    async def hard_delete_session(self, id: str) -> bool:
        """Permanently delete one session (hard delete)."""

    @abstractmethod
    async def save_message(
        self, session_id: str, role: str, content: str, rag_context: str,
        *,
        reply_to_id: int | None = None, reply_to_preview: str = "",
        retracted: bool = False,
        evidence: str | None = None,
        client_key: str | None = None,
    ) -> dict:
        """Save one message and return the stored record.

        ``evidence`` 是本条消息关联的检索来源快照 —— 列值即 ``core.schema.
        evidence_to_json`` 的产出（**唯一编码出口**；storage 只当它是文本列，不认它的
        结构，也就不引 storage → core 的反向依赖）。带默认值的关键字参数是本路径的开闭
        手法：老调用点（用户消息 / 摘要 / 群聊）零改动，老消息读回来是 ``None``，
        语义与加列前一致。

        ``client_key`` 是幂等键：同一个 ``(session_id, client_key)`` 再来一次，返回
        已有那行、不新增。补写队列（``core.message_outbox``）重复执行同一笔写时靠它收
        敛成一行 —— 没有它，重试成功一次就会多出一条重复消息。``None`` 表示不做幂等
        （老调用点原样）。

        ``*`` 之后全 keyword-only：这四个可选参数类型相近（一个 bool、一对 int/str、
        一个 JSON 文本），按位置传错位不会报错，只会静默把值装进邻近的参数。本条约束
        的由来是原先本声明的顺序与两个实现不同 —— 照本声明写第三个实现，调用点上两个
        位置实参就会静默互换。锁在 ``tests/test_storage_contract_shape.py``。
        """

    @abstractmethod
    async def get_messages(self, session_id: str) -> list[dict]:
        """List all messages in one session."""

    @abstractmethod
    async def delete_messages_after(self, session_id: str, message_id: int) -> int:
        """Delete messages after and including message_id."""

    @abstractmethod
    async def export_session(self, session_id: str, format: str) -> str:
        """Export one session in json or txt format."""

    @abstractmethod
    async def create_user(
        self, id: str, username: str, password_hash: str,
        *,
        email: str = "", home_region: str = "",
    ) -> dict:
        """Create a new user.

        ``email`` / ``home_region`` 是 keyword-only：两者都是 str 且相邻，按位置传
        错位不会报错 —— ``home_region`` 的值会静默落进 ``email``。本条约束的由来是
        原先本声明没有 ``email`` 这一格，而两个实现在第 5 位插入了它；**已经有人在
        按位置传那一格**（``tests/perf/`` 两处 ``create_user(uid, uid, "x", "...@t.local")``
        —— 按实现是 ``email``，按本声明是 ``home_region``）。锁在
        ``tests/test_storage_contract_shape.py``。
        """

    @abstractmethod
    async def get_user_by_username(self, username: str) -> dict | None:
        """Get a user by username."""

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Get a user by ID."""

    @abstractmethod
    async def get_all_users(self) -> list[dict]:
        """List all users (admin)."""

    @abstractmethod
    async def get_all_users_admin_fields(self) -> list[dict]:
        """List all users with only admin-safe fields (no secrets, for cross-border export)."""

    @abstractmethod
    async def upsert_remote_user_profile(self, id: str, username: str, home_region: str, avatar_data: str = "") -> None:
        """Create or update a remote user profile (received from peer node)."""

    @abstractmethod
    async def get_remote_user_profile(self, id: str) -> dict | None:
        """Get a remote user profile by ID."""

    @abstractmethod
    async def set_user_role(self, user_id: str, role: str) -> None:
        """Set a user's role. Role must be one of `core.roles.ROLES`; unknown id raises."""

    @abstractmethod
    async def set_user_disabled(self, user_id: str, is_disabled: bool) -> None:
        """Disable or enable a user account."""

    @abstractmethod
    async def reset_user_password(self, user_id: str, password_hash: str) -> bool:
        """Reset a user's password (admin action)."""

    @abstractmethod
    async def get_user_api_config(self, user_id: str) -> dict:
        """Get a user's API config (api_key decrypted, base_url, model, embedding_key, embedding_region)."""

    @abstractmethod
    async def update_user_api_config(self, user_id: str, api_key: str, base_url: str, model: str, embedding_key: str = "", embedding_region: str = "cn") -> None:
        """Update a user's API config. api_key and embedding_key are encrypted before storage."""

    @abstractmethod
    async def delete_user(self, user_id: str) -> dict:
        """Cascade-delete a user and all their data. Returns dict with deleted counts."""

    @abstractmethod
    async def get_user_card_ids(self, user_id: str) -> list[str]:
        """Get all card IDs owned by a user (for Mem0 cleanup)."""

    @abstractmethod
    async def create_invite_code(self, code: str, created_by: str) -> dict:
        """Create an invite code."""

    @abstractmethod
    async def get_invite_code(self, code: str) -> dict | None:
        """Get an invite code record by code string."""

    @abstractmethod
    async def use_invite_code(self, code: str, used_by: str) -> None:
        """Mark an invite code as used by a user."""

    @abstractmethod
    async def list_invite_codes(self) -> list[dict]:
        """List all invite codes."""

    @abstractmethod
    async def delete_invite_code(self, code: str) -> bool:
        """Delete a single invite code by its code string."""

    @abstractmethod
    async def delete_used_invites(self) -> int:
        """Delete all used invite codes, return count deleted."""

    @abstractmethod
    async def save_refresh_token(self, token_hash: str, user_id: str, expires_at: str, replaced_by: str = "") -> None:
        """Save a refresh token hash."""

    @abstractmethod
    async def get_refresh_token(self, token_hash: str) -> dict | None:
        """Get a refresh token record by hash."""

    @abstractmethod
    async def mark_refresh_token_used(self, token_hash: str, replaced_by: str = "") -> None:
        """Mark a refresh token as used (rotation) and record the replacing token hash."""

    @abstractmethod
    async def delete_user_refresh_tokens(self, user_id: str) -> None:
        """Delete all refresh tokens for a user (logout)."""

    @abstractmethod
    async def record_usage(self, user_id: str, action: str, prompt_tokens: int, completion_tokens: int, model: str = "", is_estimated: bool = False, chunk_count: int | None = None) -> None:
        """Record a usage stat entry.

        ``chunk_count``：本条记录聚合了几次 LLM 调用（Map 阶段按阶段汇总时填）。
        **不是文本分片数** —— 续跑命中/重试会让二者不一致。非聚合记录留 ``None``。
        """

    @abstractmethod
    async def get_usage_stats(self, user_id: str) -> dict:
        """Get usage stats for a user: totals, by_day, by_action."""

    @abstractmethod
    async def get_all_usage_summary(self) -> list[dict]:
        """Get usage summary for all users (admin)."""

    @abstractmethod
    async def get_usage_quality_stats(self) -> dict:
        """Get today's usage quality stats: total, estimated count, ratio."""

    @abstractmethod
    async def get_session_affinity_unscoped(self, session_id: str) -> dict | None:
        """Get affinity scores for a session, with no ownership filter.

        The affinity engine reads its own session_id and has no user context.
        Anything reachable from a logged-in request must not call this.
        """

    @abstractmethod
    async def save_affinity_state(self, session_id: str, state_json: str) -> None:
        """Persist full affinity state JSON and set affinity_initialized=1."""

    @abstractmethod
    async def load_affinity_state_unscoped(self, session_id: str) -> tuple[str, bool]:
        """Return (state_json, initialized) for a session, with no ownership filter.

        Called by the affinity engine (no user context). Anything reachable from
        a logged-in request must not call this.

        state_json may be empty if never persisted in new format.
        initialized is True if affinity_initialized=1.
        """

    @abstractmethod
    async def update_group_affinity(
        self, group_id: str, card_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = ""
    ) -> None:
        """Upsert affinity scores for a (group, card) pair."""

    @abstractmethod
    async def get_group_affinity(self, group_id: str, card_id: str) -> dict | None:
        """Get affinity scores for a (group, card) pair."""

    @abstractmethod
    async def update_user_banner(self, user_id: str, banner_data: str) -> None:
        """Update user banner image data."""

    @abstractmethod
    async def get_user_banner(self, user_id: str) -> str:
        """Get user banner image data (returns empty string if none)."""

    @abstractmethod
    async def update_user_bio(self, user_id: str, bio: str) -> None:
        """Update user bio text."""

    @abstractmethod
    async def update_user_nickname(self, user_id: str, nickname: str) -> None:
        """Update a user's display nickname."""

    @abstractmethod
    async def update_user_timezone(self, user_id: str, tz: str) -> None:
        """Update a user's last-known IANA timezone (`''` = unknown)."""

    @abstractmethod
    async def record_geo_block(self, user_id: str, ip: str, base_url: str, reason: str) -> None:
        """Record a geo-blocking event for compliance audit trail."""

    @abstractmethod
    async def record_user_consent(self, user_id: str, terms_version: str, privacy_version: str, ip: str) -> None:
        """Record user's consent to legal agreements for compliance audit trail."""

    @abstractmethod
    async def get_reactions_after_unscoped(self, session_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a session, with no ownership filter.

        Polled by the chat engine via its own session_id (no user context).

        Returns list of {reaction_id, emoji, msg_content, user_id}, ordered by
        reaction_id ascending.  Scoped to single-chat messages table.
        """

    @abstractmethod
    async def get_group_reactions_after_unscoped(self, group_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a group session, with no ownership filter.

        Polled by the group SSE stream via group_id; group-session ownership is
        checked upstream, rows carry no per-user owner semantics.

        Returns list of {reaction_id, emoji, msg_content, speaker_card_id},
        ordered by reaction_id ascending.  Scoped to group_messages table,
        only returns reactions on assistant (character) messages.
        """

    # ── Delete propagation outbox ─────────────────────────

    @abstractmethod
    async def enqueue_delete_propagation(self, op_type: str, target_id: str, payload: str = "") -> None:
        """Idempotent enqueue of a delete propagation intent for cross-border sync.

        op_type: 'card_delete' | 'dm_retract' | 'user_purge'
        Idempotent: same (op_type, target_id) pair is silently ignored.
        """

    @abstractmethod
    async def get_pending_delete_propagations(self, limit: int = 100) -> list[dict]:
        """Return unsynced (synced=0) delete propagations, oldest first."""

    @abstractmethod
    async def remove_delete_propagation(self, id: int) -> None:
        """Delete an outbox row once the peer acknowledged it.

        Delete, not mark: nothing reads a finished row (`get_pending_...` only
        looks at `synced = 0`), so keeping it would grow the table forever.
        """

    @abstractmethod
    async def delete_remote_card(self, card_id: str) -> None:
        """Delete a remote card replica by ID. Idempotent: no-op if not found."""

    @abstractmethod
    async def purge_remote_user_data(self, user_id: str) -> dict:
        """Delete all remote card replicas + DM copies for a user.
        Returns dict with deleted counts for auditing.
        """

    @abstractmethod
    async def retract_dm_message(self, message_id: str) -> None:
        """Set retracted=1 on a direct message. Idempotent: no-op if already retracted or not found."""

    @abstractmethod
    async def get_text_deletion_impact(self, text_id: str, user_id: str) -> dict:
        """Count cards, sessions, and messages that would be affected by deleting a text.

        Returns {"card_count": int, "session_count": int, "message_count": int}.
        Cards with shared sessions are counted once.
        """

    @abstractmethod
    async def set_announcement_active(self, announcement_id: str, active: bool) -> bool:
        """Set announcement active/inactive. When activating, all others are deactivated first."""

    # ── Distill task persistence ────────────────

    @abstractmethod
    async def create_distill_task(
        self, task_id: str, user_id: str, text_id: str,
        *,
        character: str = "", status: str = "queued", progress_pct: int = 0,
        message: str = "", card_id: str = "", awakening: str = "",
        chunk_size: int | None = None, overlap: int | None = None,
        text_fingerprint: str = "",
    ) -> dict | None:
        """Insert a NEW distillation task row. Returns the stored row.

        INSERT-only — deliberately no upsert. A duplicate task_id is a real error
        and raises: task_id is a freshly minted id here, so a conflict is a bug,
        not "please update the existing row". Keeping create and update separate
        is what makes a deleted row stay deleted — a background thread that
        outlives its text can only UPDATE, and UPDATE never inserts.

        card_id/awakening are stored but normally empty at creation; they are
        filled in later via update_distill_task.

        chunk_size/overlap/text_fingerprint are the task-level chunking checkpoint
        (resume's task gate). overlap is always None today — _split_chunks has no
        overlap concept. Later checkpoint refreshes (a reused resume row) go
        through update_distill_task, not here.
        """

    @abstractmethod
    async def get_distill_task_unscoped(self, task_id: str) -> dict | None:
        """Return one distillation task row by task_id, with no ownership filter.

        Row includes chunk_size/overlap/text_fingerprint for the resume task gate.
        Tests read back with this; anything reachable from a logged-in request
        must use get_distill_task_owned instead.
        """

    @abstractmethod
    async def get_distill_task_owned(self, task_id: str, user_id: str) -> dict | None:
        """Return one distillation task row by task_id only if it belongs to user_id.

        Ownership is filtered in SQL. Returns None both when the task does not
        exist and when it belongs to someone else.
        """

    @abstractmethod
    async def find_resumable_distill(self, user_id: str, text_id: str, character: str) -> dict | None:
        """Return the newest resumable distill task for (user, text, character), or None.

        Resume discovery. Two terminal states are resumable: 'interrupted' (boot
        reconcile leaves a dead process's running rows as such) and 'error' (the
        run failed after some chunks were already checkpointed — retrying should
        redo only the missing chunks, not all of them). 'done' is not resumable:
        distilling again means "I want a fresh version", which reruns everything.
        /start reuses the found task_id + chunk checkpoint instead of minting a
        fresh task. Exact character match — a shared text must not let two
        characters reuse each other's cached chunks.
        """

    @abstractmethod
    async def list_distill_tasks(self, limit: int = 200) -> list[dict]:
        """Return distillation task rows, newest-updated first, capped at ``limit``.

        Ops/admin listing. Deliberately bounded: the table grows without bound
        (no cascade today), so an unbounded scan is a foot-gun. Ordering by
        updated_at DESC surfaces what ops cares about first — a live task
        refreshes it on every progress write, and boot reconcile stamps it when
        flipping orphaned running rows to interrupted.
        """

    @abstractmethod
    async def count_distill_tasks(self) -> int:
        """Return the total number of distill task rows, unfiltered and uncapped.

        Exists so the capped admin listing can report truncation honestly: without
        a total the admin cannot tell the list was cut. COUNT(*) only — no row
        materialization.
        """

    @abstractmethod
    async def update_distill_task(self, task_id: str, *, status: str | None = None, progress_pct: int | None = None, message: str | None = None, card_id: str | None = None, awakening: str | None = None, chunk_size: int | None = None, text_fingerprint: str | None = None) -> int:
        """Patch only the non-None fields of a distillation task row. UPDATE-only.

        Returns the number of rows affected. 0 means the row is gone (e.g. its text
        was deleted while a background thread was still mid-flight) — callers that
        must have a row (the resume re-stamp) treat 0 as fatal; progress writers
        ignore it, because 0 is exactly "row deleted, don't write". That is the
        race closure: nobody has to notify the background thread.

        Never upserts and never inserts on a missing row. chunk_size/text_fingerprint
        carry the task-level chunking checkpoint and are re-stamped here when a
        resume reuses an existing row. overlap is intentionally absent — the chunker
        has no overlap concept.
        """

    @abstractmethod
    async def save_distill_chunk(self, task_id: str, chunk_index: int, result: str, fingerprint: str = "") -> None:
        """Persist one finished map chunk. Idempotent: re-saving the same chunk_index is a no-op.

        fingerprint is sha256 of the chunk's raw text (utf-8 bytes). Persisted with
        the chunk; the resume triple-gate compares it against a recomputed hash so a
        changed chunk boundary never reuses a stale result.
        """

    @abstractmethod
    async def get_distill_chunks(self, task_id: str) -> list[dict]:
        """Return finished chunks of a task ordered by chunk_index asc.

        Each row: {task_id, chunk_index, result, chunk_fingerprint, created_at}.
        result is the raw chunk output (JSON-encoded by caller).
        """

    @abstractmethod
    async def count_running_distills(self, user_id: str, window_minutes: int | None = None) -> int:
        """Count a user's non-terminal distill tasks (status queued/running) = slot occupancy.

        ``window_minutes``: only count rows whose updated_at is within the last N
        minutes. A live task refreshes updated_at on every progress write, so a
        stale running row is a ghost (its thread died without a terminal write)
        and must not block the user forever. None = no age filter.
        """

    @abstractmethod
    async def mark_interrupted_distills(self, message: str = "服务重启，任务已中断，等待自动恢复") -> int:
        """Boot-time reconcile: flip every status='running' row to 'interrupted'.

        Runs once at process start when the in-memory worker set is empty, so a
        running row can only belong to a dead previous process. Returns the
        number of rows transitioned.
        """

    @abstractmethod
    async def cancel_distills_by_text_id(self, text_id: str, message: str = "文本已删除，任务已取消") -> int:
        """Set every non-terminal distill row for a text to error.

        Called when the text is deleted so no running/interrupted row keeps
        occupying a slot or gets resumed against a gone text. Returns the
        number of rows updated.
        """
