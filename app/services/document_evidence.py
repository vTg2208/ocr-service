"""Preserve the processed frame, rather than guessing boxes on a different image."""
import hashlib
from app.config import get_settings
from app.db.document_evidence_models import OCRPageArtifact
from app.services.storage import create_storage


def persist_page_images(db, run, pages, *, storage=None):
    settings=get_settings();storage=storage or create_storage(settings)
    written=[]
    try:
        for number,content,width,height in pages:
            key=storage.put(content,'.png');written.append(key)
            db.add(OCRPageArtifact(extraction_run_id=run.id,page_number=number,width=width,height=height,
                storage_key=key,sha256=hashlib.sha256(content).hexdigest(),preprocessing_json={
                    'pipeline':'opencv-gray-resize-threshold-denoise-deskew-v1',
                    'minimum_width':settings.min_image_width,'pdf_dpi':settings.pdf_dpi,
                    'coordinate_frame':'processed_image_pixels','origin':'top_left'}))
        db.flush()
    except Exception:
        for key in written:storage.delete(key)
        raise
