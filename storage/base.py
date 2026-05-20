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
    async def list_sessions(
        self, keyword: str, character: str, text_id: str, page: int, page_size: int, user_id: str = ""
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
        self, session_id: str, role: str, content: str, rag_context: str
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
    async def create_user(self, id: str, username: str, password_hash: str) -> dict:
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
    async def set_user_admin(self, user_id: str, is_admin: bool) -> None:
        """Promote or demote a user to/from admin."""

    @abstractmethod
    async def set_user_disabled(self, user_id: str, is_disabled: bool) -> None:
        """Disable or enable a user account."""

    @abstractmethod
    async def reset_user_password(self, user_id: str, password_hash: str) -> bool:
        """Reset a user's password (admin action)."""

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
