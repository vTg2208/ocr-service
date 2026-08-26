import tempfile
import unittest

from app.services.storage import LocalPrivateStorage


class PrivateStorageTests(unittest.TestCase):
    def test_local_storage_reads_back_private_content(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalPrivateStorage(temp)
            key = storage.put(b"patta-content", ".png")
            self.assertEqual(storage.read(key), b"patta-content")

    def test_local_storage_rejects_path_traversal_on_read(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalPrivateStorage(temp)
            with self.assertRaises(ValueError):
                storage.read("../outside.png")


if __name__ == "__main__":
    unittest.main()
