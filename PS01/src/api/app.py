"""
FastAPI application for PS-01 (Loan Officer Who Never Forgets).

Routes:
  - /session/* — Session management (Phase 5)
  - /feedback/* — Officer feedback (Phase 6)
  - /demo/* — Demo management (Phase 6)
  - /branch/* — Branch management (Phase 7)
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from src.api.middleware import ConsentMiddleware
from src.api.session import router as session_router
from src.api.session import memory_router as memory_router
from src.api.feedback import router as feedback_router
from src.api.demo import router as demo_router
from src.api.branch import router as branch_router

# Initialize FastAPI app
app = FastAPI(
    title="PS-01: The Loan Officer Who Never Forgets",
    description="On-premise banking memory system",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Consent middleware (enforces consent for non-demo routes)
app.add_middleware(ConsentMiddleware)

# Register routers
app.include_router(session_router)
app.include_router(memory_router)
app.include_router(feedback_router)
app.include_router(demo_router)
app.include_router(branch_router)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = PROJECT_ROOT / "The-Loan-Officer-Who-Never-Forgets-Theme-Long-Context-Memory-" / "app" / "static"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "PS-01"}


@app.get("/")
async def serve_frontend():
    """Serve the local demo frontend from the existing theme UI."""
    if FRONTEND_INDEX.exists():
        return FileResponse(str(FRONTEND_INDEX))
    return {"status": "ok", "message": "Frontend not found", "service": "PS-01"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
