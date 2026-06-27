"""Abstract storage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBase(ABC):
    """Defines storage methods for texts, cards, sessions and messages."""

    @abstractmethod
    async def save_text(self, id: str, filename: str, content: str, title: str = "", description: str = "", text_type: str = "story", original_char_count: int | None = None, user_id: str = "") -> dict:
        """Save text content and return the stored record."""

    @abstractmethod
    async def get_text(self, id: str) -> dict | None:
        """Get a text record by ID."""

    @abstractmethod
    async def list_texts(self, user_id: str = "") -> list[dict]:
        """List all text records."""

    @abstractmethod
    async def delete_text(self, id: str) -> bool:
        """Delete a text record by ID."""

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
    async def get_card(self, id: str) -> dict | None:
        """Get a card record by ID."""

    @abstractmethod
    async def list_cards(self, text_id: str, user_id: str = "") -> list[dict]:
        """List cards under a text ID."""

    @abstractmethod
    async def update_card(self, card_id: str, card_json: dict) -> dict:
        """Update a card's JSON and return the updated record."""

    @abstractmethod
    async def save_session(
        self, id: str, card_id: str, user_role: str, avatar_data: str, user_id: str = ""
    ) -> dict:
        """Save a chat session and return the stored record."""

    @abstractmethod
    async def get_session(self, id: str) -> dict | None:
        """Get a session record by ID."""

    @abstractmethod
    async def update_session_avatar(self, session_id: str, user_id: str, avatar_data: str) -> bool:
        """Update session-level user avatar. Returns False if ownership check fails."""

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
        retracted: bool = False,
        reply_to_id: int | None = None, reply_to_preview: str = "",
    ) -> dict:
        """Save one message and return the stored record."""

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
    async def create_user(self, id: str, username: str, password_hash: str, home_region: str = "") -> dict:
        """Create a new user."""

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
    async def set_user_admin(self, user_id: str, is_admin: bool) -> None:
        """Promote or demote a user to/from admin."""

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
    async def save_refresh_token(self, token_hash: str, user_id: str, expires_at: str) -> None:
        """Save a refresh token hash."""

    @abstractmethod
    async def get_refresh_token(self, token_hash: str) -> dict | None:
        """Get a refresh token record by hash."""

    @abstractmethod
    async def mark_refresh_token_used(self, token_hash: str) -> None:
        """Mark a refresh token as used (rotation)."""

    @abstractmethod
    async def delete_user_refresh_tokens(self, user_id: str) -> None:
        """Delete all refresh tokens for a user (logout)."""

    @abstractmethod
    async def record_usage(self, user_id: str, action: str, prompt_tokens: int, completion_tokens: int, model: str = "", is_estimated: bool = False) -> None:
        """Record a usage stat entry."""

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
    async def update_session_affinity(
        self, session_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = ""
    ) -> None:
        """Update affinity scores for a session."""

    @abstractmethod
    async def get_session_affinity(self, session_id: str) -> dict | None:
        """Get affinity scores for a session."""

    @abstractmethod
    async def update_group_affinity(
        self, group_id: str, card_id: str, affinity: int, trust: int, mood: str, guard: int, reason: str = ""
    ) -> None:
        """Upsert affinity scores for a (group, card) pair."""

    @abstractmethod
    async def get_group_affinity(self, group_id: str, card_id: str) -> dict | None:
        """Get affinity scores for a (group, card) pair."""

    @abstractmethod
    async def cleanup_empty_cards(self, text_id: str, user_id: str) -> int:
        """Soft-delete cards with empty card_json (cleanup after failed distillation)."""

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
    async def record_geo_block(self, user_id: str, ip: str, base_url: str, reason: str) -> None:
        """Record a geo-blocking event for compliance audit trail."""

    @abstractmethod
    async def record_user_consent(self, user_id: str, terms_version: str, privacy_version: str, ip: str) -> None:
        """Record user's consent to legal agreements for compliance audit trail."""

    @abstractmethod
    async def get_reactions_after(self, session_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a session.

        Returns list of {reaction_id, emoji, msg_content, user_id}, ordered by
        reaction_id ascending.  Scoped to single-chat messages table.
        """

    @abstractmethod
    async def get_group_reactions_after(self, group_id: str, after_reaction_id: int) -> list[dict]:
        """Return reactions with id > after_reaction_id for a group session.

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
    async def mark_delete_propagated(self, id: int) -> None:
        """Mark a delete propagation outbox row as synced (synced=1)."""

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
