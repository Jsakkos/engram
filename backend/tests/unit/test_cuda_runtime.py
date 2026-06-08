"""Unit tests for the on-demand CUDA runtime downloader/registrar.

These exercise the pure logic — platform selection, wheel-member filtering, SHA256
verification, manifest-gated presence, and atomic install — without touching the network or
a real GPU. The download path is driven by monkeypatching the per-wheel fetch to return
in-memory fake wheel zips.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.matcher import cuda_runtime as cr


def _make_wheel_zip(members: dict[str, bytes]) -> bytes:
    """Build an in-memory wheel (zip) with the given member paths → contents."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ------------------------------------------------------------------ platform selection ----


def test_platform_key_maps_known_platforms(monkeypatch):
    monkeypatch.setattr(cr.sys, "platform", "win32")
    monkeypatch.setattr(cr.platform, "machine", lambda: "AMD64")
    assert cr.platform_key() == "win_amd64"

    monkeypatch.setattr(cr.sys, "platform", "linux")
    monkeypatch.setattr(cr.platform, "machine", lambda: "x86_64")
    assert cr.platform_key() == "linux_x86_64"

    monkeypatch.setattr(cr.platform, "machine", lambda: "aarch64")
    assert cr.platform_key() == "linux_aarch64"


def test_platform_key_none_on_macos(monkeypatch):
    monkeypatch.setattr(cr.sys, "platform", "darwin")
    monkeypatch.setattr(cr.platform, "machine", lambda: "arm64")
    assert cr.platform_key() is None
    assert cr.is_supported_platform() is False


def test_every_pinned_platform_has_two_wheels():
    # cuBLAS + cuDNN, cuBLAS first so it's on disk before cuDNN (which depends on it).
    for key, wheels in cr._WHEELS.items():
        assert len(wheels) == 2, key
        assert "cublas" in wheels[0].url
        assert "cudnn" in wheels[1].url


# --------------------------------------------------------------------- member filtering ----


@pytest.mark.parametrize(
    "name,expected",
    [
        ("nvidia/cublas/bin/cublas64_12.dll", True),
        ("nvidia/cudnn/bin/cudnn64_9.dll", True),
        ("nvidia/cudnn/lib/libcudnn.so.9", True),
        ("nvidia/cublas/lib/libcublasLt.so.12", True),
        ("nvidia/cudnn/__init__.py", False),
        ("nvidia_cudnn_cu12-9.19.0.56.dist-info/RECORD", False),
        ("nvidia/cudnn/include/cudnn.h", False),
        ("nvidia/cudnn/bin/", False),
    ],
)
def test_is_lib_member(name, expected):
    assert cr._is_lib_member(name) is expected


def test_extract_libs_flattens_and_skips_non_libs(tmp_path):
    wheel = _make_wheel_zip(
        {
            "nvidia/cublas/bin/cublas64_12.dll": b"DLL1",
            "nvidia/cublas/bin/cublasLt64_12.dll": b"DLL2",
            "nvidia/cublas/__init__.py": b"# not a lib",
            "nvidia_cublas_cu12-12.9.1.4.dist-info/RECORD": b"junk",
        }
    )
    extracted = cr._extract_libs(wheel, tmp_path)
    assert sorted(extracted) == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert (tmp_path / "cublas64_12.dll").read_bytes() == b"DLL1"
    assert not (tmp_path / "__init__.py").exists()


# ------------------------------------------------------------------------ verification ----


def test_download_verified_rejects_hash_mismatch(monkeypatch):
    payload = b"some bytes"
    wheel = cr._Wheel(url="https://example.test/x.whl", sha256="deadbeef", size=len(payload))

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            data, self._done = (payload, True) if not getattr(self, "_done", False) else (b"", True)
            return data

    monkeypatch.setattr(cr.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(cr.CudaRuntimeError, match="SHA256 mismatch"):
        cr._download_verified(wheel)


# ----------------------------------------------------------------------- presence gate ----


def test_is_cuda_runtime_present_requires_complete_manifest_and_files(tmp_path):
    d = tmp_path / "cuda"
    d.mkdir()
    (d / "libcudnn.so.9").write_bytes(b"x")

    # No manifest yet.
    assert cr.is_cuda_runtime_present(d) is False

    # Incomplete manifest.
    (d / cr._MANIFEST_NAME).write_text(json.dumps({"files": ["libcudnn.so.9"], "complete": False}))
    assert cr.is_cuda_runtime_present(d) is False

    # Complete, but a listed file is missing.
    (d / cr._MANIFEST_NAME).write_text(
        json.dumps({"files": ["libcudnn.so.9", "libcublas.so.12"], "complete": True})
    )
    assert cr.is_cuda_runtime_present(d) is False

    # Complete and all files present.
    (d / "libcublas.so.12").write_bytes(b"y")
    assert cr.is_cuda_runtime_present(d) is True


# ----------------------------------------------------------------------- full download ----


def test_download_cuda_runtime_installs_and_is_present(tmp_path, monkeypatch):
    cache = tmp_path / "cuda" / cr.RUNTIME_VERSION
    monkeypatch.setattr(cr, "cuda_cache_dir", lambda: cache)
    monkeypatch.setattr(cr, "platform_key", lambda: "linux_x86_64")

    fake_cublas = _make_wheel_zip({"nvidia/cublas/lib/libcublas.so.12": b"B"})
    fake_cudnn = _make_wheel_zip({"nvidia/cudnn/lib/libcudnn.so.9": b"D"})
    payloads = iter([fake_cublas, fake_cudnn])
    monkeypatch.setattr(cr, "_download_verified", lambda wheel, *a, **k: next(payloads))

    result = cr.download_cuda_runtime()
    assert result == cache
    assert cr.is_cuda_runtime_present(cache) is True
    assert (cache / "libcublas.so.12").read_bytes() == b"B"
    assert (cache / "libcudnn.so.9").read_bytes() == b"D"

    # Idempotent: a second call returns without re-fetching (iterator is exhausted).
    assert cr.download_cuda_runtime() == cache


def test_download_cuda_runtime_unsupported_platform_raises(monkeypatch):
    monkeypatch.setattr(cr, "platform_key", lambda: None)
    with pytest.raises(cr.CudaRuntimeError, match="not available on this platform"):
        cr.download_cuda_runtime()


def test_failed_extraction_leaves_no_partial_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cuda" / cr.RUNTIME_VERSION
    monkeypatch.setattr(cr, "cuda_cache_dir", lambda: cache)
    monkeypatch.setattr(cr, "platform_key", lambda: "linux_x86_64")

    def _boom(wheel, *a, **k):
        raise cr.CudaRuntimeError("network died")

    monkeypatch.setattr(cr, "_download_verified", _boom)
    with pytest.raises(cr.CudaRuntimeError):
        cr.download_cuda_runtime()
    # No cache dir, and no leftover .staging-* siblings.
    assert not cache.exists()
    assert list((tmp_path / "cuda").glob(".staging-*")) == []


def test_register_cuda_runtime_false_when_nothing_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "cuda_cache_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(cr, "_pip_nvidia_available", lambda: False)
    assert cr.register_cuda_runtime() is False
