"""FastAPI application entry point for Engram."""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api import manager as ws_manager
from app.api import router as api_router
from app.api import test_router
from app.api.validation import router as validation_router
from app.config import settings
from app.core.logging import setup_logging
from app.database import init_db
from app.services import job_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Startup
    setup_logging()
    logger.info("Starting Engram Backend...")

    await init_db()
    logger.info("Database initialized")

    # Auto-detect tools and populate config
    from app.api.validation import detect_ffmpeg, detect_makemkv
    from app.services.config_service import get_config, update_config

    config = await get_config()

    # Auto-detect MakeMKV if path is empty
    if not config.makemkv_path:
        makemkv_result = detect_makemkv()
        if makemkv_result.found:
            await update_config(makemkv_path=makemkv_result.path)
            logger.info(f"Auto-detected MakeMKV: {makemkv_result.path} ({makemkv_result.version})")
        else:
            logger.warning(f"MakeMKV not found: {makemkv_result.error}")
            logger.warning("Please install MakeMKV or configure path in Settings")
    else:
        # Validate existing configured path
        makemkv_result = detect_makemkv()
        if makemkv_result.found:
            # Update DB if stored path doesn't match the detected path
            if makemkv_result.path != config.makemkv_path:
                await update_config(makemkv_path=makemkv_result.path)
                logger.info(
                    f"MakeMKV path corrected: {config.makemkv_path!r} -> {makemkv_result.path}"
                )
            logger.info(f"MakeMKV validated: {makemkv_result.version}")
        else:
            logger.warning(f"Configured MakeMKV path not working: {makemkv_result.error}")

    # Auto-detect FFmpeg if path is empty
    if not config.ffmpeg_path:
        ffmpeg_result = detect_ffmpeg()
        if ffmpeg_result.found:
            await update_config(ffmpeg_path=ffmpeg_result.path)
            logger.info(f"Auto-detected FFmpeg: {ffmpeg_result.path} ({ffmpeg_result.version})")
        else:
            logger.warning(f"FFmpeg not found: {ffmpeg_result.error}")
            logger.warning("Please install FFmpeg or configure path in Settings")
    else:
        ffmpeg_result = detect_ffmpeg()
        if ffmpeg_result.found:
            if ffmpeg_result.path != config.ffmpeg_path:
                await update_config(ffmpeg_path=ffmpeg_result.path)
                logger.info(
                    f"FFmpeg path corrected: {config.ffmpeg_path!r} -> {ffmpeg_result.path}"
                )
            logger.info(f"FFmpeg validated: {ffmpeg_result.version}")
        else:
            logger.warning(f"Configured FFmpeg path not working: {ffmpeg_result.error}")

    await job_manager.start()
    logger.info("Job manager started")

    yield

    # Shutdown
    logger.info("Shutting down Engram Backend...")
    await job_manager.stop()
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Engram API",
    description="Glass-Box automation for disc ripping and organization",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router)
app.include_router(test_router)
app.include_router(validation_router, prefix="/api", tags=["validation"])


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, handle any incoming messages
            data = await websocket.receive_text()
            logger.debug(f"Received WebSocket message: {data}")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await ws_manager.disconnect(websocket)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# Serve bundled frontend in production/PyInstaller builds
# In frozen builds, _MEIPASS is the bundle root and static files are at app/static/
# In dev, __file__ is inside app/ so we just append "static"
if getattr(sys, "_MEIPASS", None):
    _static_dir = os.path.join(sys._MEIPASS, "app", "static")
else:
    _static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

if os.path.isdir(_static_dir):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # Mount static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the SPA frontend — catch-all for client-side routing."""
        file_path = os.path.join(_static_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_static_dir, "index.html"))

else:

    @app.get("/")
    async def root():
        """Root endpoint - API status (dev mode only, no bundled frontend)."""
        return {
            "name": "Engram",
            "version": "0.1.0",
            "status": "running",
        }


if __name__ == "__main__":
    import threading
    import webbrowser

    import uvicorn

    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        # Open browser after a short delay to let the server bind the port
        url = f"http://{settings.host}:{settings.port}"
        threading.Timer(1.5, webbrowser.open, args=[url]).start()

    try:
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            # reload is incompatible with passing app object directly
            # and also incompatible with frozen PyInstaller bundles
            reload=False if is_frozen else settings.debug,
            factory=False,
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        if is_frozen:
            print(f"\nFatal error: {e}")
            input("Press Enter to exit...")
            sys.exit(1)
