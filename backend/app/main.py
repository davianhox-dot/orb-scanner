"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.logging_config import configure_logging
from app.services.scheduler import start_scheduler, stop_scheduler

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (env=%s, provider=%s)", settings.APP_NAME, settings.ENV, settings.DATA_PROVIDER)
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Automated pre-market scanner for high-probability micro-cap and "
        "small-cap day trading setups (ORB, First Pullback, VWAP, Momentum "
        "Breakout)."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
async def root():
    return {"app": settings.APP_NAME, "status": "running", "docs": "/docs"}
