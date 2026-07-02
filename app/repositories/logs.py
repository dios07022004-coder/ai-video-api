"""Event-log persistence (admin-visible audit trail)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.database.tables import EventLog
from app.repositories.base import BaseRepository


class EventLogRepository(BaseRepository[EventLog]):
    model = EventLog

    async def record(
        self,
        event: str,
        *,
        level: str = "INFO",
        source: str = "api",
        task_id: str | None = None,
        partner_id: int | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> EventLog:
        row = EventLog(
            event=event,
            level=level,
            source=source,
            task_id=task_id,
            partner_id=partner_id,
            message=message,
            data=data,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def recent(self, *, limit: int = 200, task_id: str | None = None) -> Sequence[EventLog]:
        stmt = select(EventLog).order_by(EventLog.created_at.desc()).limit(limit)
        if task_id is not None:
            stmt = stmt.where(EventLog.task_id == task_id)
        return (await self.session.execute(stmt)).scalars().all()
