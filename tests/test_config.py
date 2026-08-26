import os
import unittest
from unittest.mock import patch

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_default_llm_model_is_a_supported_groq_production_model(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.llm_model_name, "openai/gpt-oss-120b")


if __name__ == "__main__":
    unittest.main()
