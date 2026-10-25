"""Job event log (drives progress streams and the job timeline)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import JobEvent


def emit(db: Session, job_id: uuid.UUID, event_type: str, message: str, level: str = "info", **data: Any) -> None:
    db.add(JobEvent(job_id=job_id, event_type=event_type, level=level, message=message[:2000], data=data))
