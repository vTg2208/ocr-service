import unittest
import uuid

from app.db.fra_completion_models import ModelVersion
from app.services.fra_adapter_factory import create_entity_extractor
from app.services.fra_entity_extraction import TamilNaduFRAExtractor
from app.services.model_gateway import ManifestFRAEntityExtractor, ModelRegistrationError


def model(adapter_type, *, configuration=None, status="active", version="1.2.0"):
    return ModelVersion(
        id=uuid.uuid4(),
        task="entity_extraction",
        adapter_type=adapter_type,
        name=f"tn-{adapter_type}",
        version=version,
        status=status,
        configuration_json={"ready": True, **(configuration or {})},
        label_map_json={},
        metrics_json={"status": "evaluated"},
        registered_by=uuid.uuid4(),
    )


class FRAAdapterFactoryTests(unittest.TestCase):
    def test_factory_creates_manifest_and_allowlisted_local_adapters(self):
        manifest = create_entity_extractor(
            model("manifest", configuration={"synthetic": True})
        )
        local = create_entity_extractor(
            model("local_python", configuration={"runner": "tamil_nadu_fra_regex_v1"})
        )
        self.assertIsInstance(manifest, ManifestFRAEntityExtractor)
        self.assertIsInstance(local, TamilNaduFRAExtractor)
        self.assertEqual(local.version, "1.2.0")

    def test_rest_adapter_uses_injected_transport_and_checks_registered_version(self):
        calls = []

        def transport(endpoint, payload, timeout):
            calls.append((endpoint, payload, timeout))
            return {
                "fields": {"village": "Kottur"},
                "field_evidence": {"village": {"text": "Village: Kottur"}},
                "confidence": 0.8,
                "model_version": "1.2.0",
                "processing_time_ms": 12,
                "provenance": {"provider": "attached-model"},
            }

        extractor = create_entity_extractor(
            model("rest", configuration={
                "endpoint": "https://models.example.org/fra/extract",
                "allowed_hosts": ["models.example.org"],
                "timeout_seconds": 8,
            }),
            rest_transport=transport,
        )
        result = extractor.extract("private-record-1", {"raw_text": "Village: Kottur"})
        self.assertEqual(result.fields["village"], "Kottur")
        self.assertEqual(calls[0][0], "https://models.example.org/fra/extract")
        self.assertNotIn("private-record-1", calls[0][1])

        mismatch = create_entity_extractor(
            model("rest", configuration={
                "endpoint": "https://models.example.org/fra/extract",
                "allowed_hosts": ["models.example.org"],
            }),
            rest_transport=lambda *_args: {
                "fields": {}, "field_evidence": {}, "confidence": None,
                "model_version": "9.9.9", "processing_time_ms": 1,
            },
        )
        with self.assertRaisesRegex(ModelRegistrationError, "version mismatch"):
            mismatch.extract("record", {"raw_text": "text"})

    def test_factory_rejects_unready_unsupported_and_arbitrary_python_adapters(self):
        with self.assertRaisesRegex(ModelRegistrationError, "not ready"):
            create_entity_extractor(model("local_python", configuration={"ready": False}))
        with self.assertRaisesRegex(ModelRegistrationError, "Unsupported adapter"):
            create_entity_extractor(model("pytorch"))
        with self.assertRaisesRegex(ModelRegistrationError, "allow-listed"):
            create_entity_extractor(
                model("local_python", configuration={"runner": "some.package.CustomModel"})
            )


if __name__ == "__main__":
    unittest.main()
