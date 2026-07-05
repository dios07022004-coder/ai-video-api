"""Project-wide constants and canonical enumerations of magic values.

Nothing here is a business decision that a partner would tune — those live in
`Settings` (env) or in mode/model JSON. This file only holds protocol-level
constants that the code itself depends on.
"""

from __future__ import annotations

from enum import StrEnum

# ── Placeholder tokens understood by the Workflow Engine ─────────────────────
# The engine is generic (any {{UPPER_SNAKE}} token works); these are the
# well-known ones documented for workflow authors and validated against modes.
PLACEHOLDER_PATTERN = r"\{\{\s*([A-Z0-9_]+)\s*\}\}"

WELL_KNOWN_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "IMAGE",
        "PROMPT",
        "NEGATIVE",
        "WIDTH",
        "HEIGHT",
        "SEED",
        "CFG",
        "STEPS",
        "SAMPLER",
        "SCHEDULER",
        "FPS",
        "FRAMES",
        "MODEL",
        "LORA",
        "LORA_STRENGTH",
        "VAE",
        "CONTROLNET",
        "IPADAPTER",
        "CONTROL_VIDEO",
        "DENOISE",
        "BATCH",
        "CLIP_SKIP",
        "GUIDANCE",
        "LENGTH_SECONDS",
    }
)


class TaskType(StrEnum):
    """Kind of generation the mode produces."""

    VIDEO = "video"
    IMAGE = "image"


class ModelType(StrEnum):
    """Classifies an entry in models.json for path resolution + VRAM planning."""

    CHECKPOINT = "checkpoint"
    UNET = "unet"
    DIFFUSION = "diffusion"
    LORA = "lora"
    VAE = "vae"
    CLIP = "clip"
    CLIP_VISION = "clip_vision"
    CONTROLNET = "controlnet"
    IPADAPTER = "ipadapter"
    UPSCALE = "upscale"
    EMBEDDING = "embedding"


class BillingEntryType(StrEnum):
    """Ledger entry semantics."""

    HOLD = "hold"          # credits reserved at enqueue
    CHARGE = "charge"      # hold committed on success
    REFUND = "refund"      # hold released on failure/cancel
    TOPUP = "topup"        # admin credit grant
    ADJUST = "adjust"      # manual correction


# ── Error codes (stable public contract) ─────────────────────────────────────
class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    MODE_NOT_FOUND = "MODE_NOT_FOUND"
    MODE_DISABLED = "MODE_DISABLED"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PLACEHOLDER_UNRESOLVED = "PLACEHOLDER_UNRESOLVED"
    INVALID_WORKFLOW = "INVALID_WORKFLOW"
    UPLOAD_INVALID = "UPLOAD_INVALID"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    AGE_VERIFICATION_FAILED = "AGE_VERIFICATION_FAILED"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    PARAM_INVALID = "PARAM_INVALID"
    COMFY_UNAVAILABLE = "COMFY_UNAVAILABLE"
    COMFY_EXECUTION_FAILED = "COMFY_EXECUTION_FAILED"
    GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CONFLICT = "CONFLICT"


# ComfyUI protocol
COMFY_CLIENT_ID_PREFIX = "aivideo"
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".webm", ".gif", ".mov", ".mkv"})
IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# Idempotency window: identical (partner, request_id) reuse the same task.
IDEMPOTENCY_HEADER = "Idempotency-Key"
