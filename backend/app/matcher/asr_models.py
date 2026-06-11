"""
ASR Model Abstraction Layer

This module provides a unified interface for different Automatic Speech Recognition models,
supporting OpenAI Whisper models via faster-whisper for efficient inference.
"""

import abc
import hashlib
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import ctranslate2
import librosa
import numpy as np
import psutil
import soundfile as sf
from loguru import logger
from rapidfuzz import fuzz

from app.matcher.srt_utils import clean_text

# Cache for loaded models to avoid reloading. Guarded by _model_cache_lock so that
# concurrent matching threads (N semaphore slots → N asyncio.to_thread workers) don't
# each build their own model on a cold cache — without the lock, N threads race past the
# membership check and construct N models with N workers each (N² threads).
_model_cache = {}
_model_cache_lock = threading.Lock()

# Conservative parallel-stream cap on GPU (VRAM auto-sizing is a future enhancement).
GPU_WORKER_CAP = 4

# Process-wide resolved ASR device, decided ONCE at startup (job_manager.start) after the
# CUDA runtime is registered. Before this is set, callers fall back to the raw driver probe.
# Centralizing the decision here is what keeps the semaphore sizing, the /api/asr-status
# badge, and the model loader from disagreeing — they all read detect_asr_device().
_asr_device_override: str | None = None


def set_asr_device(device: str | None) -> None:
    """Pin the effective ASR device for the rest of the process (``None`` re-enables probing)."""
    global _asr_device_override
    _asr_device_override = device


def gpu_detected() -> bool:
    """True when an NVIDIA CUDA device is visible to the driver — ignores libs and override.

    This is the hardware-capability probe (does a GPU exist?), distinct from
    ``detect_asr_device()`` which reports the *usable* device. Used to decide whether to even
    offer the GPU-acceleration toggle.
    """
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 — any probe failure means no usable GPU
        return False


def cuda_compute_type() -> str:
    """Pick the best CUDA compute type CTranslate2 reports as supported.

    Hardcoding ``float16`` fails on GPUs without Compute Capability ≥ 7.0 (e.g. Pascal), so
    fall back to ``int8_float16`` then ``float32``. Degrades to ``float16`` if the probe is
    unavailable (it always lists float16 on a working CUDA build).
    """
    try:
        supported = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:  # noqa: BLE001 — probe failure (no CUDA): caller only reaches here on cuda
        return "float16"
    for candidate in ("float16", "int8_float16", "float32"):
        if candidate in supported:
            return candidate
    return "float16"


@dataclass(frozen=True)
class AsrRuntime:
    """Resolved ASR execution parameters — the single source of truth for sizing.

    Consumed by the shared WhisperModel (num_workers/cpu_threads), the JobManager
    match semaphore (workers == admission slots, so the dashboard cannot overstate
    MATCHING), and the /api/asr-status endpoint.
    """

    device: str  # "cuda" | "cpu"
    compute_type: str  # "float16" (cuda) | "int8" (cpu)
    workers: int
    cpu_threads: int | None  # None on GPU (not applicable)


def detect_asr_device() -> str:
    """Return the effective ASR device ('cuda' only when actually usable, else 'cpu').

    Once ``set_asr_device()`` has run at startup this returns that resolved decision — which
    accounts for whether the user enabled the GPU *and* the CUDA math libraries are installed,
    not merely whether a GPU exists. Before startup it falls back to the raw driver probe.
    """
    if _asr_device_override is not None:
        return _asr_device_override
    return "cuda" if gpu_detected() else "cpu"


def resolve_asr_runtime(device: str, requested_workers: int | None) -> AsrRuntime:
    """Resolve (workers, cpu_threads) from a requested worker count, clamped to hardware.

    CPU: workers clamp to physical cores; cpu_threads = cores // workers so the total
    thread count stays ~= cores (avoids the oversubscription that makes naive
    parallelism slower). GPU: workers clamp to GPU_WORKER_CAP; cpu_threads is N/A.
    """
    requested = max(1, int(requested_workers or 1))
    if device == "cuda":
        return AsrRuntime(
            device="cuda",
            compute_type=cuda_compute_type(),
            workers=min(requested, GPU_WORKER_CAP),
            cpu_threads=None,
        )
    cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1
    workers = max(1, min(requested, cores))
    cpu_threads = max(1, cores // workers)
    return AsrRuntime(device="cpu", compute_type="int8", workers=workers, cpu_threads=cpu_threads)


class ASRModel(abc.ABC):
    """Abstract base class for ASR models."""

    def __init__(self, model_name: str, device: str | None = None):
        """
        Initialize ASR model.

        Args:
            model_name: Name/identifier of the model
            device: Device to run on ('cpu', 'cuda', or None for auto-detect)
        """
        self.model_name = model_name
        self.device = device or self._get_default_device()
        self._model = None

    def _get_default_device(self) -> str:
        """Get default device for this model type (honors the startup-resolved override)."""
        return detect_asr_device()

    @abc.abstractmethod
    def load(self):
        """Load the model. Should be called before transcription."""
        pass

    @abc.abstractmethod
    def transcribe(self, audio_path: str | Path) -> dict:
        """
        Transcribe audio file.

        Args:
            audio_path: Path to audio file

        Returns:
            Dictionary with at least 'text' key containing transcription
        """
        pass

    def calculate_match_score(self, transcription: str, reference: str) -> float:
        """
        Calculate similarity score between transcription and reference.

        Args:
            transcription: Transcribed text
            reference: Reference subtitle text

        Returns:
            Float score between 0.0 and 1.0
        """
        # Default implementation: Standard weights
        # Token sort ratio (70%) + Partial ratio (30%)
        token_weight = 0.7
        partial_weight = 0.3

        score = (
            fuzz.token_sort_ratio(transcription, reference) * token_weight
            + fuzz.partial_ratio(transcription, reference) * partial_weight
        ) / 100.0

        return score

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._model is not None

    def unload(self):
        """Unload model to free memory."""
        self._model = None


class FasterWhisperModel(ASRModel):
    """
    OpenAI Whisper ASR model implementation using faster-whisper.

    This uses CTranslate2 for efficient inference, providing:
    - Faster inference than original Whisper
    - Lower memory usage
    - Easy CPU and GPU support
    - No complex dependencies (unlike NVIDIA NeMo)
    """

    # Available model sizes with their approximate properties
    MODEL_SIZES = {
        "tiny": {"params": "39M", "vram": "~1GB", "speed": "fastest"},
        "tiny.en": {"params": "39M", "vram": "~1GB", "speed": "fastest"},
        "base": {"params": "74M", "vram": "~1GB", "speed": "fast"},
        "base.en": {"params": "74M", "vram": "~1GB", "speed": "fast"},
        "small": {"params": "244M", "vram": "~2GB", "speed": "medium"},
        "small.en": {"params": "244M", "vram": "~2GB", "speed": "medium"},
        "medium": {"params": "769M", "vram": "~5GB", "speed": "slow"},
        "medium.en": {"params": "769M", "vram": "~5GB", "speed": "slow"},
        "large-v3": {"params": "1550M", "vram": "~10GB", "speed": "slowest"},
    }

    def __init__(
        self, model_name: str = "small", device: str | None = None, requested_workers: int = 1
    ):
        """
        Initialize Faster Whisper model.

        Args:
            model_name: Whisper model size (tiny, base, small, medium, large-v3)
            device: Device to run on ('cpu', 'cuda', or None for auto-detect)
            requested_workers: Desired parallel ASR workers; resolve_asr_runtime
                clamps this to hardware at load time.
        """
        if model_name.startswith("openai/whisper-"):
            model_name = model_name.replace("openai/whisper-", "")

        self.requested_workers = max(1, int(requested_workers or 1))

        # Register the CUDA math libraries (downloaded cache or dev pip packages) so
        # CTranslate2 can dlopen them. Idempotent — startup already did this; this covers
        # any model built before/without that path.
        if device == "cuda" or (device is None and detect_asr_device() == "cuda"):
            _ensure_nvidia_libraries()

        super().__init__(model_name, device)

    def _get_compute_type(self) -> str:
        """Get optimal compute type for the device."""
        if self.device == "cuda":
            # Best CUDA type the hardware supports (float16, or int8_float16/float32 on
            # older GPUs) — must match resolve_asr_runtime so the cache key and badge agree.
            return cuda_compute_type()
        else:
            # Use int8 for CPU (good balance of speed and accuracy)
            return "int8"

    def load(self):
        """Load Faster Whisper model with caching."""
        if self.is_loaded:
            return

        runtime = resolve_asr_runtime(self.device, self.requested_workers)
        cache_key = (
            f"faster_whisper_{self.model_name}_{self.device}"
            f"_w{runtime.workers}_t{runtime.cpu_threads}"
        )

        if cache_key in _model_cache:
            self._model = _model_cache[cache_key]
            logger.debug(f"Using cached Faster Whisper model: {self.model_name} on {self.device}")
            return

        try:
            from faster_whisper import WhisperModel

            compute_type = self._get_compute_type()

            logger.info(
                f"Loading Faster Whisper model: {self.model_name} on {self.device} "
                f"(compute_type={compute_type})"
            )

            try:
                model_kwargs = {
                    "device": self.device,
                    "compute_type": compute_type,
                    "download_root": None,  # Use default cache location
                    "num_workers": runtime.workers,
                }
                if runtime.cpu_threads is not None:
                    model_kwargs["cpu_threads"] = runtime.cpu_threads
                self._model = WhisperModel(self.model_name, **model_kwargs)

                # Eagerly transcribe 1s of silence to surface missing CUDA
                # DLL errors here instead of on the first real transcription.
                if self.device == "cuda":
                    try:
                        logger.debug("Verifying CUDA availability by running dummy encoding...")
                        dummy_audio = np.zeros(16000, dtype=np.float32)
                        next(self._model.transcribe(dummy_audio, language="en")[0], None)
                    except Exception as e:
                        # If verifying fails, raise it to be caught by the outer try/except
                        logger.warning(f"CUDA verification failed: {e}")
                        raise RuntimeError(f"CUDA verification failed: {e}") from e

            except RuntimeError as e:
                # Fallback to CPU if CUDA libraries are missing
                if self.device == "cuda" and (
                    "Library" in str(e) or "verification failed" in str(e)
                ):
                    logger.warning(
                        f"Failed to load/run on CUDA due to missing libraries: {e}. "
                        "Falling back to CPU."
                    )
                    self.device = "cpu"
                    compute_type = "int8"
                    cpu_runtime = resolve_asr_runtime("cpu", self.requested_workers)
                    self._model = WhisperModel(
                        self.model_name,
                        device=self.device,
                        compute_type=compute_type,
                        download_root=None,
                        num_workers=cpu_runtime.workers,
                        cpu_threads=cpu_runtime.cpu_threads,
                    )
                    # Stored under the CPU key, not the stale CUDA key computed above.
                    cache_key = (
                        f"faster_whisper_{self.model_name}_{self.device}"
                        f"_w{cpu_runtime.workers}_t{cpu_runtime.cpu_threads}"
                    )
                else:
                    raise

            _model_cache[cache_key] = self._model
            logger.info(f"Loaded Faster Whisper model: {self.model_name} on {self.device}")

        except ImportError as e:
            raise ImportError(
                "faster-whisper not installed. Run: pip install faster-whisper"
            ) from e
        except Exception as e:
            logger.error(f"Failed to load Faster Whisper model {self.model_name}: {e}")
            raise

    @staticmethod
    def _preprocessed_path_for(audio_path: str | Path) -> Path:
        """Hash resolved source path into the filename so concurrent threads don't collide."""
        temp_dir = Path(tempfile.gettempdir()) / "whisper_preprocessed"
        resolved = str(Path(audio_path).resolve())
        src_hash = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]
        return temp_dir / f"preprocessed_{src_hash}_{Path(audio_path).stem}.wav"

    def _preprocess_audio(self, audio_path: str | Path) -> str:
        """
        Preprocess audio for Whisper model requirements.

        Args:
            audio_path: Path to input audio file

        Returns:
            Path to preprocessed audio file (or original if no preprocessing needed)
        """
        try:
            # Load audio with librosa
            audio, original_sr = librosa.load(str(audio_path), sr=None)

            # Target sample rate for Whisper models (16kHz)
            target_sr = 16000

            # Resample if necessary
            if original_sr != target_sr:
                audio = librosa.resample(audio, orig_sr=original_sr, target_sr=target_sr)
                logger.debug(f"Resampled audio from {original_sr}Hz to {target_sr}Hz")

            # Normalize audio to [-1, 1] range
            if np.max(np.abs(audio)) > 0:
                audio = audio / np.max(np.abs(audio))

            temp_audio_path = self._preprocessed_path_for(audio_path)
            temp_audio_path.parent.mkdir(exist_ok=True)

            # Save preprocessed audio
            sf.write(str(temp_audio_path), audio, target_sr)

            logger.debug(f"Preprocessed audio saved to {temp_audio_path}")
            return str(temp_audio_path)

        except Exception as e:
            logger.warning(f"Audio preprocessing failed, using original: {e}")
            return str(audio_path)

    def _clean_transcription_text(self, text: str) -> str:
        """
        Clean and normalize transcription text.

        Args:
            text: Raw transcription text

        Returns:
            Cleaned text
        """
        if not text:
            return ""

        return clean_text(text)

    def transcribe(self, audio_path: str | Path) -> dict:
        """
        Transcribe audio using Faster Whisper.

        Args:
            audio_path: Path to audio file

        Returns:
            Dictionary with 'text', 'raw_text', 'segments', and 'language'
        """
        if not self.is_loaded:
            self.load()

        preprocessed_audio = None
        try:
            logger.debug(f"Starting Faster Whisper transcription for {audio_path}")

            # Preprocess audio
            preprocessed_audio = self._preprocess_audio(audio_path)

            # Transcribe with faster-whisper
            segments, info = self._model.transcribe(
                preprocessed_audio,
                language="en",  # Force English for TV episode matching
                beam_size=5,
                best_of=5,
                temperature=0.0,  # Greedy decoding for consistency
                condition_on_previous_text=False,
                vad_filter=True,  # Filter out non-speech
            )

            # Collect all segment texts
            segment_list = []
            full_text_parts = []

            for segment in segments:
                segment_list.append(
                    {
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text,
                    }
                )
                full_text_parts.append(segment.text)

            raw_text = " ".join(full_text_parts).strip()
            cleaned_text = self._clean_transcription_text(raw_text)

            logger.debug(f"Raw transcription: '{raw_text}'")
            logger.debug(f"Cleaned transcription: '{cleaned_text}'")

            return {
                "text": cleaned_text,
                "raw_text": raw_text,
                "segments": segment_list,
                "language": info.language if hasattr(info, "language") else "en",
            }

        except Exception as e:
            logger.error(
                f"Faster Whisper transcription failed for {audio_path}: {type(e).__name__}: {e}"
            )
            import traceback

            traceback.print_exc()
            # Return empty result instead of raising to allow fallback
            return {"text": "", "raw_text": "", "segments": [], "language": "en"}
        finally:
            # Clean up preprocessed audio file
            if preprocessed_audio and preprocessed_audio != str(audio_path):
                try:
                    Path(preprocessed_audio).unlink(missing_ok=True)
                except Exception as e:
                    logger.debug(f"Failed to clean up preprocessed audio: {e}")


def model_output_key(model_config: dict) -> str:
    """Return a stable string identifying the *output-affecting* ASR model configuration.

    This key is designed for use as the ``model_key`` component of a persistent
    transcript-cache lookup (file_key, start_s, duration_s, model_key).

    **Assumption — caller owns device honesty.** The key is only as accurate as the
    config it receives.  An explicit ``device="cuda"`` is taken at face value; if the
    model actually loads on CPU (e.g. the CUDA→CPU fallback in
    ``FasterWhisperModel.__init__`` when cuDNN libraries are missing), the key will be
    stale.  Callers that build a config from a raw GPU probe must resolve the *effective*
    device (via ``set_asr_device`` / ``detect_asr_device()``) before calling this
    function.  "auto" / None / missing are safe — they are resolved here via
    ``detect_asr_device()``.

    **Included fields** (all change Whisper's output text):
    - ``type`` — model family/architecture (e.g. "whisper", "faster-whisper")
    - ``name`` — model size/checkpoint (e.g. "small", "large-v3"); different sizes have
      different vocabularies, encoder capacities, and word-error rates.
    - ``device`` — resolved effective device ("cpu" or "cuda"). Transcripts produced on
      CUDA with float16 differ from CPU int8 transcripts due to floating-point rounding.
      An unresolved value ("auto", None, or missing) is resolved via ``detect_asr_device()``
      so the key reflects the actual runtime, not the config placeholder.
    - ``compute_type`` — quantization scheme (e.g. "int8", "float16", "int8_float16").
      Quantization changes numeric precision throughout the encoder, directly affecting
      transcription output.  Derived from ``device`` via ``cuda_compute_type()`` /
      the fixed CPU value "int8".

    **Excluded fields** (affect only speed/parallelism, not transcript content):
    - ``requested_workers`` / ``num_workers`` — controls how many parallel transcription
      streams the WhisperModel object runs; does not change what any individual stream
      produces.
    - ``cpu_threads`` — thread-pool size for BLAS ops inside CTranslate2; same model
      weights, same output.

    Returns:
        ``"{type}_{name}_{device}_{compute_type}"`` — underscore-joined, deterministic
        across process restarts for the same effective configuration.
    """
    model_type = model_config.get("type", "")
    model_name = model_config.get("name", "")

    # Resolve the effective device: honor explicit "cpu"/"cuda", fall back to the
    # startup-pinned value for "auto", None, or missing so that a config placeholder
    # never produces a key that disagrees with the actual runtime.
    raw_device = model_config.get("device")
    if raw_device in ("cpu", "cuda"):
        device = raw_device
    else:
        device = detect_asr_device()

    # Derive compute_type from the resolved device, matching the logic in
    # FasterWhisperModel._get_compute_type() and resolve_asr_runtime().
    if device == "cuda":
        compute_type = cuda_compute_type()
    else:
        compute_type = "int8"

    return f"{model_type}_{model_name}_{device}_{compute_type}"


def create_asr_model(model_config: dict) -> ASRModel:
    """
    Factory function to create ASR models from configuration.

    Args:
        model_config: Dictionary with 'type' and 'name' keys

    Returns:
        Configured ASRModel instance

    Example:
        model_config = {"type": "whisper", "name": "small"}
        model = create_asr_model(model_config)
    """
    model_type = model_config.get("type", "").lower()
    model_name = model_config.get("name", "")
    device = model_config.get("device")
    requested_workers = model_config.get("requested_workers", 1)

    # Handle whisper and faster-whisper types
    if model_type in ("whisper", "faster-whisper", "openai-whisper"):
        if not model_name:
            model_name = "small"

        logger.info(f"Creating Faster Whisper model: {model_name}")
        return FasterWhisperModel(model_name, device, requested_workers=requested_workers)

    # Legacy parakeet support - redirect to whisper
    elif model_type == "parakeet":
        logger.warning(
            "Parakeet models are no longer supported. Using Whisper 'small' model instead."
        )
        return FasterWhisperModel("small", device, requested_workers=requested_workers)

    else:
        raise ValueError(
            f"Unsupported model type: {model_type}. Supported types: 'whisper', 'faster-whisper'"
        )


def get_cached_model(model_config: dict) -> ASRModel:
    """
    Get a cached model instance, creating it if necessary.

    Args:
        model_config: Dictionary with model configuration

    Returns:
        ASRModel instance (loaded and ready for use)
    """
    device = model_config.get("device") or detect_asr_device()
    runtime = resolve_asr_runtime(device, model_config.get("requested_workers", 1))
    cache_key = (
        f"{model_config.get('type', '')}_{model_config.get('name', '')}"
        f"_{device}_w{runtime.workers}_t{runtime.cpu_threads}"
    )

    # Hold the lock across check-create-store so exactly one thread builds the shared
    # model per cache_key; the rest block, then reuse it. First-load contention is the
    # whole point — it's what makes "one shared model with N workers" actually one model.
    with _model_cache_lock:
        if cache_key not in _model_cache:
            model = create_asr_model(model_config)
            model.load()  # Load immediately for caching
            _model_cache[cache_key] = model

        return _model_cache[cache_key]


def clear_model_cache():
    """Clear all cached models to free memory."""
    global _model_cache
    for model in _model_cache.values():
        if hasattr(model, "unload"):
            model.unload()
    _model_cache.clear()
    logger.info("Cleared ASR model cache")


def list_available_models() -> dict:
    """
    List available model types and their requirements.

    Returns:
        Dictionary with model types and their availability status
    """
    availability = {}

    # Check Faster Whisper availability
    try:
        import faster_whisper  # noqa: F401

        availability["whisper"] = {
            "available": True,
            "models": list(FasterWhisperModel.MODEL_SIZES.keys()),
            "default": "small",
            "description": "OpenAI Whisper models via faster-whisper (CTranslate2)",
        }
    except ImportError:
        availability["whisper"] = {
            "available": False,
            "error": "faster-whisper not installed. Run: pip install faster-whisper",
        }

    return availability


def _ensure_nvidia_libraries():
    """Register the CUDA math libraries (downloaded cache, else dev pip packages).

    Cross-platform: on Windows this adds the DLL directory; on Linux it preloads the .so
    files into the global symbol namespace. Delegates to ``cuda_runtime.register_cuda_runtime``
    so the on-demand download cache and the dev ``uv sync -E gpu`` packages are both honored.
    Safe to call when nothing is installed (no-op).
    """
    try:
        from app.matcher.cuda_runtime import register_cuda_runtime

        register_cuda_runtime()
    except Exception as e:  # noqa: BLE001 — registration is best-effort; load-time fallback covers it
        logger.warning(f"Failed to register CUDA libraries: {e}", exc_info=True)
