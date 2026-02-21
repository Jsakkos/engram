# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Engram.

Bundles the FastAPI backend + React frontend into a single distributable directory.
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect ALL app.* submodules so PyInstaller doesn't miss any
app_hidden = collect_submodules("app")

# Third-party modules loaded dynamically (invisible to static analysis)
third_party_hidden = [
    # SQLAlchemy loads the dialect driver from the connection URL string
    "aiosqlite",
    # Uvicorn internal plugin system
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # Pydantic ecosystem
    "pydantic_settings",
    # Async support
    "asyncio",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    # Audio/ML dependencies
    "librosa",
    "soundfile",
    "sklearn",
    "sklearn.utils._cython_blas",
    "sklearn.neighbors._typedefs",
    "sklearn.neighbors._partition_nodes",
    # Logging
    "loguru",
    # HTTP client
    "httpx",
    "httpcore",
    # Other
    "chardet",
    "rapidfuzz",
    "psutil",
    "beautifulsoup4",
    "bs4",
]

all_hidden = app_hidden + third_party_hidden

# Data files that must be included
datas = []

# Bundle frontend static files if they exist (CI copies them to app/static/)
static_dir = os.path.join("app", "static")
if os.path.isdir(static_dir):
    datas.append((static_dir, os.path.join("app", "static")))

# Collect data files for ML models
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")

a = Analysis(
    ["app/main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="engram",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="engram",
)
