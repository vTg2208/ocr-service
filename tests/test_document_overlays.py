"""Exact OCR frames, field coordinates, access control and artifact integrity."""
from io import BytesIO
import hashlib
import unittest
from unittest.mock import patch
import uuid

import cv2
import numpy as np
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.config import get_settings
from app.db.document_evidence_models import OCRPageArtifact
from app.services.fra_job_handlers import get_job_handler
from app.services.image_processor import ImageProcessor
from app.services.ocr_engine import OCRLineResult, OCRPageAnalysis
from app.services.storage import LocalPrivateStorage
from tests import test_community_portal as fixture
from tests import test_fra_job_handlers as jobs


class EvidenceOverlayTests(unittest.TestCase):
    setUp=fixture.CommunityWorkflowTests.setUp
    tearDown=fixture.CommunityWorkflowTests.tearDown
    clear_limits=fixture.CommunityWorkflowTests.clear_limits
    account=fixture.CommunityWorkflowTests.account
    build_job=jobs.FRAArchiveJobHandlerTests.build_job

    def test_frame_matches_preprocessing_and_private_routes_preserve_integrity(self):
        image=Image.new('RGB',(500,400),'white')
        ImageDraw.Draw(image).text((10,10),'MOCK OCR FRAME - NOT A REAL CLAIM',fill='black')
        output=BytesIO();image.save(output,format='PNG');content=output.getvalue()
        prepared=ImageProcessor().preprocess(ImageProcessor.decode(content))
        class OCR:
            def extract_page(self,pixels):
                return OCRPageAnalysis('Claim No: TN-MOCK-1',90,[OCRLineResult('Claim No: TN-MOCK-1',.9,[20,30,300,60])])
        storage=LocalPrivateStorage(self.temp.name)
        with patch.object(get_settings(),'secure_upload_dir',self.temp.name),patch.object(get_settings(),'upload_storage_backend','local'):
            with self.factory() as db:
                _actor,document,record,job=self.build_job(db)
                document.original_filename='mock.png';document.content_type='image/png'
                document.storage_key=storage.put(content,'.png');document.sha256=hashlib.sha256(content).hexdigest()
                with patch('app.services.fra_job_handlers._ocr_engine',return_value=OCR()):
                    result=get_job_handler('archive_extract')(db,job)
                db.commit();record_id=str(record.id);run_id=result['extraction_run_id']
                artifact=db.scalar(select(OCRPageArtifact).where(OCRPageArtifact.extraction_run_id==uuid.UUID(run_id)))
                key=artifact.storage_key
            prefix=f'/api/fra/archive/records/{record_id}/extractions/{run_id}/pages'
            self.assertEqual(self.alice.get(prefix).status_code,403)
            response=self.staff.get(prefix);self.assertEqual(response.status_code,200,response.text)
            metadata=response.json();self.assertNotIn(key,response.text)
            self.assertEqual(metadata['items'][0]['width'],prepared.shape[1])
            self.assertEqual(metadata['fields']['claim_number']['box'],[20,30,300,60])
            image_url=metadata['items'][0]['image_url']
            response=self.staff.get(image_url);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')
            decoded=cv2.imdecode(np.frombuffer(response.content,dtype='uint8'),cv2.IMREAD_GRAYSCALE)
            np.testing.assert_array_equal(decoded,prepared)
            self.assertEqual(self.bob.get(image_url).status_code,403)
            original=self.staff.get(metadata['original_url'])
            self.assertEqual(original.content,content)
            (storage.root/key).write_bytes(b'tampered artifact')
            self.assertEqual(self.staff.get(image_url).status_code,503)
