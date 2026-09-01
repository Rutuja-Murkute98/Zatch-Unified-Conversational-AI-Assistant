"""
WHAT:
    Request/response shapes for the /chat and /health endpoints.

WHY THIS APPROACH:
    Pydantic validates incoming requests automatically - a malformed
    request (missing message, empty session_id) gets rejected with a
    clear 422 error before it ever reaches our orchestration logic,
    matching Phase 9.3's requirement.
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1, max_length=200)


class ChatResponse(BaseModel):
    reply: str
    session_id: str