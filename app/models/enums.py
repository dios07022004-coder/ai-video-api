"""Task lifecycle state machine.

The public API exposes exactly the states required by the spec. Transitions are
enforced by the service layer so a task can never jump illegally (e.g. from
``completed`` back to ``running``).
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    LOADING = "loading"
    PREPARING = "preparing"
    RUNNING = "running"
    ENCODING = "encoding"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

# Allowed forward transitions. Terminal states have no outgoing edges.
TASK_STATE_MACHINE: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.LOADING, TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.LOADING: frozenset({TaskStatus.PREPARING, TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.PREPARING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.ENCODING, TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}),
    TaskStatus.ENCODING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

# Coarse progress floor per state, so a poll always shows forward motion even if
# ComfyUI hasn't emitted a fine-grained progress event yet.
STATE_PROGRESS_FLOOR: dict[TaskStatus, int] = {
    TaskStatus.QUEUED: 0,
    TaskStatus.LOADING: 5,
    TaskStatus.PREPARING: 10,
    TaskStatus.RUNNING: 15,
    TaskStatus.ENCODING: 90,
    TaskStatus.COMPLETED: 100,
    TaskStatus.FAILED: 0,
    TaskStatus.CANCELLED: 0,
}


def is_terminal(status: TaskStatus | str) -> bool:
    return TaskStatus(status) in _TERMINAL


def can_transition(current: TaskStatus | str, target: TaskStatus | str) -> bool:
    return TaskStatus(target) in TASK_STATE_MACHINE[TaskStatus(current)]
