import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import RequestContextMiddleware
from app.services.malware import ClamAVScanner, MalwareScannerUnavailable
from app.services.storage import LocalPrivateStorage


class PrivateStorageTests(unittest.TestCase):
    def test_local_storage_uses_opaque_key_and_can_remove_failed_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalPrivateStorage(temp)
            key = storage.put(b"private", ".pdf")
            self.assertNotIn("patta", key)
            self.assertEqual((Path(temp) / key).read_bytes(), b"private")
            storage.delete(key)
            self.assertFalse((Path(temp) / key).exists())


class MalwareScanningTests(unittest.TestCase):
    def test_required_scanning_fails_closed_when_not_configured(self):
        with self.assertRaises(MalwareScannerUnavailable):
            ClamAVScanner("", 3310, required=True).scan(b"document")

    def test_optional_scanning_allows_local_development_without_daemon(self):
        ClamAVScanner("", 3310, required=False).scan(b"document")


class RateLimitTests(unittest.TestCase):
    def test_protected_write_endpoint_is_rate_limited_with_request_id(self):
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware, requests=1, window_seconds=60)

        @app.post("/api/claims")
        async def claim():
            return {"ok": True}

        client = TestClient(app)
        first = client.post("/api/claims", headers={"Authorization": "Bearer token"})
        second = client.post("/api/claims", headers={"Authorization": "Bearer token"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("X-Request-ID", second.headers)


if __name__ == "__main__":
    unittest.main()
