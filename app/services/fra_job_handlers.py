"""Registry for FRA background job handlers."""

from collections.abc import Callable
from typing import Any


JobHandler = Callable[[Any, Any], dict]
JOB_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(task_type: str, handler: JobHandler) -> None:
    normalized = task_type.strip()
    if not normalized:
        raise ValueError("A task type is required.")
    JOB_HANDLERS[normalized] = handler


def get_job_handler(task_type: str) -> JobHandler | None:
    return JOB_HANDLERS.get(task_type)
