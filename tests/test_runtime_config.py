import tempfile
import unittest
from pathlib import Path

from app.config.runtime import load_runtime_key, save_runtime_key


class RuntimeConfigTests(unittest.TestCase):
    def test_save_preserves_other_values_and_replaces_only_runtime_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\nOTHER=value\nCONTROL_PLANE_API_KEY=old\n", encoding="utf-8"
            )
            save_runtime_key(path, "synthetic-key")
            self.assertEqual(load_runtime_key(path), "synthetic-key")
            self.assertIn("OTHER=value", path.read_text(encoding="utf-8"))
            self.assertIn("# comment", path.read_text(encoding="utf-8"))
            self.assertNotIn("=old", path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_name(".env.tmp").exists())

    def test_quotes_bom_and_empty_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            self.assertEqual(load_runtime_key(path), "")
            path.write_text(
                'CONTROL_PLANE_API_KEY="synthetic-key"\n', encoding="utf-8-sig"
            )
            self.assertEqual(load_runtime_key(path), "synthetic-key")

    def test_invalid_key_is_not_saved_or_exposed_in_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for key in ("", "secret\nOTHER=injected", "secret value"):
                with self.assertRaises(ValueError) as error:
                    save_runtime_key(path, key)
                self.assertNotIn("secret", str(error.exception))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
