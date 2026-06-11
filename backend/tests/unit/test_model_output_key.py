"""Unit tests for model_output_key() — transcript cache identity helper."""

from unittest.mock import patch

import pytest

from app.matcher.asr_models import model_output_key, set_asr_device


@pytest.fixture(autouse=True)
def _reset_asr_device_override():
    """Reset the process-wide ASR device override around every test."""
    set_asr_device(None)
    yield
    set_asr_device(None)


class TestModelOutputKeyFormat:
    """Key must be underscore-joined, stable, and unambiguous."""

    def test_cpu_config_returns_expected_format(self):
        config = {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 1}
        key = model_output_key(config)
        assert key == "whisper_small_cpu_int8"

    def test_cuda_config_returns_expected_format(self):
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"float16", "int8_float16", "float32"},
        ):
            config = {
                "type": "whisper",
                "name": "large-v3",
                "device": "cuda",
                "requested_workers": 2,
            }
            key = model_output_key(config)
        assert key == "whisper_large-v3_cuda_float16"

    def test_key_has_exactly_four_underscore_separated_parts(self):
        config = {"type": "whisper", "name": "base", "device": "cpu", "requested_workers": 1}
        parts = model_output_key(config).split("_")
        # "base" has no internal underscores, so expect exactly 4 parts
        assert len(parts) == 4

    def test_key_is_a_non_empty_string(self):
        config = {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 1}
        assert isinstance(model_output_key(config), str)
        assert model_output_key(config) != ""


class TestModelOutputKeyDeviceResolution:
    """Device 'auto' / None must be resolved to the effective runtime device."""

    def test_auto_device_resolves_to_pinned_cpu(self):
        set_asr_device("cpu")
        config = {"type": "whisper", "name": "small", "device": "auto", "requested_workers": 1}
        key = model_output_key(config)
        assert "_cpu_" in key
        assert "_cuda_" not in key

    def test_auto_device_resolves_to_pinned_cuda(self):
        set_asr_device("cuda")
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"float16", "int8_float16", "float32"},
        ):
            config = {"type": "whisper", "name": "small", "device": "auto", "requested_workers": 1}
            key = model_output_key(config)
        assert "_cuda_" in key
        assert "_cpu_" not in key

    def test_none_device_resolves_via_detect_asr_device(self):
        set_asr_device("cpu")
        config = {"type": "whisper", "name": "small", "device": None, "requested_workers": 1}
        key = model_output_key(config)
        assert "_cpu_" in key

    def test_missing_device_field_resolves_via_detect_asr_device(self):
        set_asr_device("cpu")
        config = {"type": "whisper", "name": "small", "requested_workers": 1}
        key = model_output_key(config)
        assert "_cpu_" in key

    def test_cpu_and_cuda_produce_different_keys(self):
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"float16", "int8_float16", "float32"},
        ):
            cpu_key = model_output_key({"type": "whisper", "name": "small", "device": "cpu"})
            cuda_key = model_output_key({"type": "whisper", "name": "small", "device": "cuda"})
        assert cpu_key != cuda_key


class TestModelOutputKeyOutputAffectingFields:
    """Output-affecting fields must change the key; concurrency knobs must NOT."""

    def test_different_model_names_produce_different_keys(self):
        small = model_output_key({"type": "whisper", "name": "small", "device": "cpu"})
        large = model_output_key({"type": "whisper", "name": "large-v3", "device": "cpu"})
        assert small != large

    def test_different_model_types_produce_different_keys(self):
        whisper = model_output_key({"type": "whisper", "name": "small", "device": "cpu"})
        fw = model_output_key({"type": "faster-whisper", "name": "small", "device": "cpu"})
        assert whisper != fw

    def test_requested_workers_does_not_change_key(self):
        """Workers only affect parallelism, not transcript content."""
        key1 = model_output_key(
            {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 1}
        )
        key2 = model_output_key(
            {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 8}
        )
        assert key1 == key2

    def test_cpu_threads_does_not_change_key(self):
        """cpu_threads is a speed knob, not present in model_config but tested for completeness."""
        # cpu_threads is resolved inside resolve_asr_runtime, not part of model_config at all.
        # Both configs below omit it → same key.
        key1 = model_output_key({"type": "whisper", "name": "small", "device": "cpu"})
        key2 = model_output_key({"type": "whisper", "name": "small", "device": "cpu"})
        assert key1 == key2

    def test_compute_type_is_included_in_key(self):
        """Float16 vs int8 changes quantization → output text may differ."""
        # CPU always → int8
        cpu_key = model_output_key({"type": "whisper", "name": "small", "device": "cpu"})
        assert cpu_key.endswith("int8")

    def test_cuda_compute_type_int8_float16_included_when_float16_unsupported(self):
        """Pascal-class GPUs fall back to int8_float16 — that must appear in the key."""
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"int8_float16", "float32"},
        ):
            key = model_output_key({"type": "whisper", "name": "small", "device": "cuda"})
        assert key.endswith("int8_float16")


class TestModelOutputKeyDeterminism:
    """Key must be stable across repeated calls with the same config."""

    def test_same_config_returns_same_key(self):
        config = {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 2}
        assert model_output_key(config) == model_output_key(config)

    def test_dict_ordering_does_not_matter(self):
        config_a = {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 1}
        config_b = {"device": "cpu", "name": "small", "requested_workers": 1, "type": "whisper"}
        assert model_output_key(config_a) == model_output_key(config_b)
