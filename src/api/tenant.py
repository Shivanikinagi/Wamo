# src/api/tenant.py
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


@dataclass
class TenantContext:
    bank_id: str
    branch_id: Optional[str] = None


class TenantMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces X-Bank-ID header on every request."""

    async def dispatch(self, request: Request, call_next):
        bank_id = request.headers.get("X-Bank-ID")
        if not bank_id:
            return JSONResponse(
                status_code=400,
                content={"detail": "X-Bank-ID header required"},
            )
        branch_id = request.headers.get("X-Branch-ID")
        request.state.tenant = TenantContext(bank_id=bank_id, branch_id=branch_id)
        return await call_next(request)


def get_tenant(request: Request) -> TenantContext:
    """FastAPI dependency — returns the TenantContext set by TenantMiddleware."""
    return request.state.tenant
