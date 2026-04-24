"""Unit tests for MIME type registration at startup (Issue #110).

On Windows, mimetypes reads Content-Type mappings from HKEY_CLASSES_ROOT.
Certain software (old Node.js, some IDEs) corrupts the .css entry to
text/plain. Browsers silently refuse to apply CSS with a non-CSS Content-Type,
producing a blank white page even though React renders successfully.

The fix in app/main.py calls mimetypes.add_type() before StaticFiles mounts
to override any bad Registry values.
"""

import mimetypes

import pytest


class TestMimeTypeRegistration:
    @pytest.fixture(autouse=True)
    def restore_mime_types(self):
        """Save and restore mimetypes.types_map so tests don't leak into each other."""
        mimetypes.init()
        saved = dict(mimetypes.types_map)
        yield
        mimetypes.types_map.clear()
        mimetypes.types_map.update(saved)

    def test_add_type_overrides_corrupted_css_registry(self):
        """mimetypes.add_type() must override a bad Windows Registry value for .css."""
        mimetypes.types_map[".css"] = "text/plain"
        assert mimetypes.guess_type("style.css")[0] == "text/plain"  # confirm corruption

        mimetypes.add_type("text/css", ".css")

        assert mimetypes.guess_type("style.css")[0] == "text/css"

    def test_add_type_overrides_corrupted_js_registry(self):
        """mimetypes.add_type() must override a bad Windows Registry value for .js."""
        mimetypes.types_map[".js"] = "text/plain"

        mimetypes.add_type("application/javascript", ".js")

        assert mimetypes.guess_type("bundle.js")[0] == "application/javascript"

    def test_all_critical_types_fixed_after_corruption(self):
        """Applying the same registrations as app/main.py fixes all critical types."""
        mimetypes.types_map.update(
            {".css": "text/plain", ".js": "text/plain", ".mjs": "text/plain", ".svg": "text/plain"}
        )

        mimetypes.add_type("text/css", ".css")
        mimetypes.add_type("application/javascript", ".js")
        mimetypes.add_type("application/javascript", ".mjs")
        mimetypes.add_type("image/svg+xml", ".svg")

        assert mimetypes.guess_type("styles.css")[0] == "text/css"
        assert mimetypes.guess_type("bundle.js")[0] == "application/javascript"
        assert mimetypes.guess_type("bundle.mjs")[0] == "application/javascript"
        assert mimetypes.guess_type("icon.svg")[0] == "image/svg+xml"

    def test_app_main_registers_types_on_import(self):
        """Importing app.main leaves all critical MIME types correctly set."""
        import app.main  # noqa: F401 — imported for side effects

        assert mimetypes.guess_type("styles.css")[0] == "text/css"
        assert mimetypes.guess_type("bundle.js")[0] == "application/javascript"
        assert mimetypes.guess_type("bundle.mjs")[0] == "application/javascript"
        assert mimetypes.guess_type("icon.svg")[0] == "image/svg+xml"
