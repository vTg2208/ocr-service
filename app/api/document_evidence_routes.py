"""Restricted, audited source pixels and field location metadata for reviewers."""
import hashlib
import math
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.auth import AuthenticatedUser, require_reviewer
from app.api.fra_archive_routes import _record_or_404
from app.config import get_settings
from app.db.document_evidence_models import OCRPageArtifact
from app.db.fra_completion_models import FRAExtractionRun
from app.db.session import get_db
from app.services.audit import record_audit
from app.services.storage import create_storage

router=APIRouter(prefix='/api/fra/archive',tags=['Document evidence overlays'])


def extraction(db,record_id,run_id,user):
    record=_record_or_404(db,record_id,user)
    run=db.get(FRAExtractionRun,run_id)
    if run is None or run.archive_record_id!=record.id:raise HTTPException(404,'Extraction not found.')
    return record,run


@router.get('/records/{record_id}/extractions/{run_id}/pages')
def pages(record_id:uuid.UUID,run_id:uuid.UUID,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    record,run=extraction(db,record_id,run_id,user)
    artifacts=db.scalars(select(OCRPageArtifact).where(OCRPageArtifact.extraction_run_id==run_id).order_by(OCRPageArtifact.page_number)).all()
    fields={}
    for name,evidence in (run.field_evidence_json or {}).items():
        if not isinstance(evidence,dict):continue
        box=evidence.get('source_bounding_box') or evidence.get('bounding_box')
        if not isinstance(box,list) or len(box)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in box):box=None
        fields[name]={'page':evidence.get('source_page'),'box':box,'text':evidence.get('text') or evidence.get('raw_value') or ''}
    prefix=f'/api/fra/archive/records/{record_id}/extractions/{run_id}/pages'
    record_audit(db,actor_id=user.id,action='ocr_page_metadata_viewed',entity_type='fra_extraction_run',entity_id=run.id)
    db.commit()
    return JSONResponse({'items':[{'page':p.page_number,'width':p.width,'height':p.height,
        'image_url':f'{prefix}/{p.page_number}/image','sha256':p.sha256,'preprocessing':p.preprocessing_json} for p in artifacts],
        'fields':fields,'original_url':f'/api/fra/archive/records/{record_id}/source-document',
        'note':'Boxes use the saved processed OCR frame, not the unprocessed scan. Older extractions may need reprocessing.'},headers={'Cache-Control':'no-store'})


@router.get('/records/{record_id}/extractions/{run_id}/pages/{page_number}/image')
def image(record_id:uuid.UUID,run_id:uuid.UUID,page_number:int,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    _record,run=extraction(db,record_id,run_id,user)
    page=db.scalar(select(OCRPageArtifact).where(OCRPageArtifact.extraction_run_id==run.id,OCRPageArtifact.page_number==page_number))
    if page is None:raise HTTPException(404,'Saved OCR page not found.')
    try:content=create_storage(get_settings()).read(page.storage_key)
    except (OSError,ValueError) as error:raise HTTPException(404,'Saved OCR page is unavailable.') from error
    if hashlib.sha256(content).hexdigest()!=page.sha256:raise HTTPException(503,'Evidence integrity check failed.')
    record_audit(db,actor_id=user.id,action='ocr_page_image_viewed',entity_type='fra_extraction_run',entity_id=run.id,after={'page':page_number})
    db.commit()
    return Response(content,media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/records/{record_id}/source-document')
def source_document(record_id:uuid.UUID,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    record=_record_or_404(db,record_id,user)
    try:content=create_storage(get_settings()).read(record.document.storage_key)
    except (OSError,ValueError) as error:raise HTTPException(404,'Source document is unavailable.') from error
    record_audit(db,actor_id=user.id,action='archive_source_document_viewed',entity_type='fra_archive_record',entity_id=record.id)
    db.commit()
    return Response(content,media_type=record.document.content_type,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Disposition':f'attachment; filename="source-{record.id}"'})
