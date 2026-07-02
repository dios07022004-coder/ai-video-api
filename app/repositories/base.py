"""Generic async repository base."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Holds the session. Subclasses add typed query methods.

    A repository never commits — the surrounding ``session_scope`` / request
    transaction owns commit/rollback (Unit of Work).
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()  # assign PK / defaults without committing
        return entity

    async def get(self, pk: object) -> ModelT | None:
        return await self.session.get(self.model, pk)

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
