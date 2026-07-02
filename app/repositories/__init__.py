"""Repository layer: the only place that talks to the database.

Repositories contain persistence logic exclusively — no business rules. Services
compose them within a transactional session (Unit of Work). This isolates the ORM
so a storage swap never ripples outward.
"""

from app.repositories.billing import BillingRepository
from app.repositories.logs import EventLogRepository
from app.repositories.partners import PartnerRepository
from app.repositories.tasks import TaskRepository
from app.repositories.uploads import UploadRepository

__all__ = [
    "TaskRepository",
    "PartnerRepository",
    "UploadRepository",
    "BillingRepository",
    "EventLogRepository",
]
