import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_default_application_name_uses_the_aranyasetu_brand(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.app_name, "AranyaSetu")

    def test_default_llm_model_is_a_supported_groq_production_model(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.llm_model_name, "openai/gpt-oss-120b")

    def test_worker_heartbeat_must_be_shorter_than_its_lease(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValidationError):
            Settings(_env_file=None, job_lease_seconds=30, job_heartbeat_seconds=30)


if __name__ == "__main__":
    unittest.main()
