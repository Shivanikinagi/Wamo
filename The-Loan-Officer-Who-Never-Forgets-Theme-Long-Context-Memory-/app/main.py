from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
import os
import logging
from pathlib import Path

from app.database import init_db
from app.routes import calls

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ENV_FILE = PROJECT_ROOT / ".env"
STATIC_DIR = APP_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

load_dotenv(dotenv_path=ENV_FILE)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BrainBack.AI", description="Loan Officer Memory System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    logger.info("BrainBack.AI started")


@app.get("/api/config")
async def get_config():
    return {
        "publicKey": os.getenv("VAPI_PUBLIC_KEY", ""),
        "assistantId": os.getenv("VAPI_ASSISTANT_ID", ""),
    }


app.include_router(calls.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse(str(INDEX_FILE))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
