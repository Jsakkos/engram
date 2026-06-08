# Third-Party Licenses

Engram's distributable builds bundle the following third-party binaries.

> **NVIDIA CUDA libraries (cuDNN, cuBLAS)** are *not* bundled. When a user opts in to GPU
> acceleration, Engram downloads the official NVIDIA pip wheels at runtime (pinned + SHA256
> verified in `backend/app/matcher/cuda_runtime.py`) into `~/.engram/cuda/`. These libraries
> are NVIDIA redistributables governed by the
> [NVIDIA CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html); the user accepts that EULA
> before the download. The binaries are redistributed unmodified and are not stored in this
> repository or in Engram's build artifacts.

## Chromaprint (`fpcalc`)

- **Component:** `fpcalc`, the command-line audio fingerprinter from Chromaprint.
- **Version:** 1.5.1 (pinned in `backend/scripts/fetch_fpcalc.py`).
- **License:** GNU Lesser General Public License, version 2.1 or later (LGPL-2.1+).
- **Copyright:** © Lukáš Lalinský and the Chromaprint contributors.
- **Source:** https://github.com/acoustid/chromaprint (release `v1.5.1`).
- **Modifications:** None. The official release binary is redistributed unmodified.

The binary is fetched at build time from the official GitHub release and verified
against a pinned SHA256 (see `backend/scripts/fetch_fpcalc.py`); it is not stored
in this repository. Engram invokes `fpcalc` as a separate executable (it does not
link against libchromaprint), so no relinking is required to satisfy the LGPL. The
complete corresponding source for the bundled version is available at the URL above.
