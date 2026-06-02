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
    # SQLAlchemy's async engine imports greenlet lazily via a C extension, so
    # PyInstaller's static analysis misses it (notably on macOS) — without this
    # the frozen app crashes at startup with "No module named 'greenlet'".
    # greenlet is also pinned as a direct dep in pyproject.toml: SQLAlchemy's
    # own marker omits macOS arm64, so uv wouldn't otherwise install it there.
    "greenlet",
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
    "rich",
    "rich.console",
    "rich.text",
    # HTTP client
    "httpx",
    "httpcore",
    "requests",
    # Database migrations (optional at runtime, but declared dependency)
    "alembic",
    "alembic.config",
    "alembic.command",
    "alembic.runtime",
    "alembic.runtime.migration",
    "mako",
    "mako.template",
    # Other
    "chardet",
    "rapidfuzz",
    "psutil",
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

# TLS CA bundle. httpx/requests load certifi.where() (-> certifi/cacert.pem) for
# every HTTPS call. Collect it explicitly so the CA bundle can never silently drop
# out from PyInstaller hook / certifi-version drift — a missing cacert.pem makes
# ssl.create_default_context() raise FileNotFoundError and kills all networking.
datas += collect_data_files("certifi")

# Ship the third-party license notice (covers the bundled fpcalc) alongside the
# binary it describes, satisfying Chromaprint's LGPL redistribution terms.
_licenses = os.path.join("..", "THIRD_PARTY_LICENSES.md")
if os.path.isfile(_licenses):
    datas.append((_licenses, "."))

# Bundle fpcalc (fetched by scripts/fetch_fpcalc.py before the build) so end
# users get audio fingerprinting without installing Chromaprint themselves. It
# lands at <bundle>/bin/fpcalc[.exe], which detect_fpcalc() resolves via
# sys._MEIPASS. If absent (script not run), it simply isn't bundled and the app
# falls back to auto-detecting a system/PATH fpcalc.
binaries = []
_fpcalc_name = "fpcalc.exe" if os.name == "nt" else "fpcalc"
_fpcalc_path = os.path.join("app", "bin", _fpcalc_name)
if os.path.isfile(_fpcalc_path):
    binaries.append((_fpcalc_path, "bin"))

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
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
    icon=os.path.join(SPECPATH, "../frontend/public/brand/app-icons/windows/engram.ico"),
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
