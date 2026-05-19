"""Abstract storage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBase(ABC):
    """Defines storage methods for texts, cards, sessions and messages."""

    @abstractmethod
    async def save_text(self, id: str, filename: str, content: str, title: str = "", description: str = "") -> dict:
        """Save text content and return the stored record."""

    @abstractmethod
    async def get_text(self, id: str) -> dict | None:
        """Get a text record by ID."""

    @abstractmethod
    async def list_texts(self) -> list[dict]:
        """List all text records."""

    @abstractmethod
    async def delete_text(self, id: str) -> bool:
        """Delete a text record by ID."""

    @abstractmethod
    async def save_card(self, id: str, text_id: str, name: str, card_json: str) -> dict:
        """Save a character card and return the stored record."""

    @abstractmethod
    async def get_card(self, id: str) -> dict | None:
        """Get a card record by ID."""

    @abstractmethod
    async def list_cards(self, text_id: str) -> list[dict]:
        """List cards under a text ID."""

    @abstractmethod
    async def update_card(self, card_id: str, card_json: dict) -> dict:
        """Update a card's JSON and return the updated record."""

    @abstractmethod
    async def save_session(
        self, id: str, card_id: str, user_role: str, avatar_data: str
    ) -> dict:
        """Save a chat session and return the stored record."""

    @abstractmethod
    async def get_session(self, id: str) -> dict | None:
        """Get a session record by ID."""

    @abstractmethod
    async def list_sessions(
        self, keyword: str, character: str, text_id: str, page: int, page_size: int
    ) -> dict:
        """List sessions with filters and pagination."""

    @abstractmethod
    async def delete_session(self, id: str) -> bool:
        """Delete a session record by ID."""

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
