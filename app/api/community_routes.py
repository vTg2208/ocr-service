"""Community portal APIs. Staff actions require stored reviewer/admin roles."""
import hashlib
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.api.user_portal_routes import own_plot, PlotInput, validated_boundary
from app.config import get_settings
from app.db.session import get_db
from app.db.models import User, utcnow
from app.db.user_plot_models import UserPlot
from app.db.community_models import CommunityProfile, ContactOTP, PlotSubmission, PortalPublication, UserResource, SupportTicket, TicketMessage, TicketAttachment
from app.models.community_schemas import ProfileInput, RevisionInput, ReviewInput, TicketInput, MessageInput, PublicationInput, PublicationUpdate, ResourceInput, ResourceUpdate, OTPRequest, OTPVerify, ItemsResponse, AtlasResponse, DashboardResponse
from app.services import community_portal as service
from app.services.audit import record_audit
from app.services.storage import create_storage
from app.services.malware import ClamAVScanner, MalwareDetectedError, MalwareScannerUnavailable
from app.utils.file_validation import validate_upload, FileValidationError

router=APIRouter(tags=['Community portal v1'])
def audit(db,user,action,entity,identifier,after):
    record_audit(db,actor_id=user.id,action=action,entity_type=entity,entity_id=identifier,after=after)

def reserve(db,model,identifier,revision,values):
    result=db.execute(update(model).where(model.id==identifier,model.revision==revision).values(**values,revision=model.revision+1))
    if result.rowcount!=1:
        db.rollback();raise HTTPException(409,'This record changed. Refresh before trying again.')

@router.get('/api/user/dashboard',response_model=DashboardResponse)
def user_dashboard(user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.dashboard(db,user)

@router.get('/api/user/profile',response_model=dict)
def get_profile(user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.profile_json(db,user)

@router.put('/api/user/profile',response_model=dict)
def save_profile(payload:ProfileInput,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    profile=db.get(CommunityProfile,user.id)
    values=payload.model_dump(exclude={'display_name','expected_revision'})
    if profile:
        for contact in ('email','phone'):
            if getattr(profile,contact)!=values[contact]:
                values[contact+'_verified_at']=None
                values[contact+'_verification_method']=None
                db.execute(update(ContactOTP).where(ContactOTP.user_id==user.id,ContactOTP.channel==contact,ContactOTP.consumed_at.is_(None)).values(consumed_at=utcnow()))
        result=db.execute(update(CommunityProfile).where(CommunityProfile.user_id==user.id,CommunityProfile.revision==payload.expected_revision).values(**values,revision=CommunityProfile.revision+1,updated_at=utcnow()))
        if result.rowcount!=1:db.rollback();raise HTTPException(409,'Profile changed. Refresh before saving.')
    else:
        if payload.expected_revision!=0:raise HTTPException(409,'Profile changed. Refresh before saving.')
        db.add(CommunityProfile(user_id=user.id,**values))
    db.get(User,user.id).display_name=payload.display_name
    audit(db,user,'community_profile_saved','user',user.id,{'revision':payload.expected_revision+1})
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'Profile changed. Refresh before saving.')
    db.expire_all();return service.profile_json(db,user)

@router.get('/api/user/atlas',response_model=AtlasResponse)
def user_atlas(user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.atlas(db,user)

@router.post('/api/user/overlap-check',response_model=dict)
def overlap_check(payload:PlotInput,plot_id:uuid.UUID|None=None,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    if plot_id:own_plot(db,plot_id,user)
    geometry,_=validated_boundary(payload.coordinates)
    return service.overlap(db,geometry,user,exclude_plot=plot_id)

@router.get('/api/user/submissions',response_model=ItemsResponse)
def my_submissions(user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return {'items':[service.submission_json(s) for s in db.scalars(select(PlotSubmission).where(PlotSubmission.owner_id==user.id).order_by(PlotSubmission.created_at.desc()))]}

@router.post('/api/user/plots/{plot_id}/submit',response_model=dict,status_code=201)
def submit_plot(plot_id:uuid.UUID,payload:RevisionInput,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    plot=db.scalar(select(UserPlot).where(UserPlot.id==plot_id,UserPlot.owner_id==user.id).with_for_update())
    if plot is None:raise HTTPException(404,'Plot not found.')
    if plot.revision!=payload.expected_revision:raise HTTPException(409,'Plot changed. Reopen it before submitting.')
    old=db.scalar(select(PlotSubmission).where(PlotSubmission.plot_id==plot.id,PlotSubmission.plot_revision==plot.revision))
    if old:return service.submission_json(old)
    profile=db.get(CommunityProfile,user.id)
    s=PlotSubmission(plot_id=plot.id,owner_id=user.id,plot_revision=plot.revision,name=plot.name,village=plot.village,
        geometry=plot.geometry,area_sqm=plot.area_sqm,public_label=profile.public_label if profile else '',
        community_name=profile.community_name if profile else '',share_label=profile.share_label if profile else False)
    db.add(s)
    try:
        db.flush();audit(db,user,'plot_submitted','plot_submission',s.id,{'plot_revision':plot.revision});db.commit()
    except IntegrityError:
        db.rollback();existing=db.scalar(select(PlotSubmission).where(PlotSubmission.plot_id==plot_id,PlotSubmission.plot_revision==payload.expected_revision))
        if existing:return service.submission_json(existing)
        raise
    return service.submission_json(s)

@router.get('/api/user/publications',response_model=ItemsResponse)
def user_publications(kind:str|None=None,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    if kind and kind not in {'announcement','scheme'}:raise HTTPException(422,'Unknown publication kind.')
    return {'items':[service.publication_json(p) for p in service.publications(db,kind)]}

@router.get('/api/user/resources',response_model=ItemsResponse)
def my_resources(user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return {'items':[service.resource_json(r) for r in db.scalars(select(UserResource).where(UserResource.owner_id==user.id,UserResource.verification_state=='verified').order_by(UserResource.observed_at.desc()))]}

@router.post('/api/user/tickets',response_model=dict,status_code=201)
def create_ticket(payload:TicketInput,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    if payload.category == 'plot_mapping':raise HTTPException(422, 'Use the document mapping form and attach your evidence.')
    if payload.plot_id:own_plot(db,payload.plot_id,user)
    if payload.scheme_id:
        available={p.id for p in service.publications(db,'scheme')}
        if payload.scheme_id not in available:raise HTTPException(404,'Published scheme not found.')
    t=SupportTicket(owner_id=user.id,category=payload.category,subject=payload.subject,plot_id=payload.plot_id,scheme_id=payload.scheme_id)
    db.add(t);db.flush();db.add(TicketMessage(ticket_id=t.id,author_id=user.id,author_role='user',body=payload.message))
    audit(db,user,'ticket_created','support_ticket',t.id,{'category':t.category});db.commit();return service.ticket_json(db,t,True)

@router.get('/api/user/tickets',response_model=ItemsResponse)
def my_tickets(user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return {'items':[service.ticket_json(db,t) for t in db.scalars(select(SupportTicket).where(SupportTicket.owner_id==user.id).order_by(SupportTicket.updated_at.desc()))]}

@router.get('/api/user/tickets/{ticket_id}',response_model=dict)
def my_ticket(ticket_id:uuid.UUID,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return service.ticket_json(db,service.ticket_for_actor(db,ticket_id,user),True)

def add_message(db,t,payload,user,staff):
    if not staff and payload.status is not None:raise HTTPException(403,'Only staff may set a ticket status.')
    status=payload.status if staff and payload.status else ('in_progress' if staff else 'open')
    reserve(db,SupportTicket,t.id,payload.expected_revision,{'status':status,'updated_at':utcnow()})
    db.add(TicketMessage(ticket_id=t.id,author_id=user.id,author_role=user.role if staff else 'user',body=payload.message))
    audit(db,user,'ticket_replied','support_ticket',t.id,{'status':status});db.commit();db.expire_all()
    return service.ticket_json(db,db.get(SupportTicket,t.id),True)

@router.post('/api/user/tickets/{ticket_id}/messages',response_model=dict,status_code=201)
def user_reply(ticket_id:uuid.UUID,payload:MessageInput,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    return add_message(db,service.ticket_for_actor(db,ticket_id,user),payload,user,False)

@router.post('/api/community/tickets/{ticket_id}/attachments',response_model=dict,status_code=201)
async def attach_file(ticket_id:uuid.UUID,file:UploadFile=File(...),expected_revision:int=Form(...),user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    staff=user.role in {'reviewer','admin'}
    t=service.ticket_for_actor(db,ticket_id,user,staff)
    settings=get_settings();content=await file.read(min(settings.max_file_size_bytes,10*1024*1024)+1)
    await file.close()
    if len(content)>min(settings.max_file_size_bytes,10*1024*1024):raise HTTPException(413,'Attachments must be at most 10 MB.')
    try:
        checked=validate_upload(file.filename or 'upload',content)
        ClamAVScanner(settings.clamav_host,settings.clamav_port,required=settings.malware_scan_required).scan(content)
    except (FileValidationError,MalwareDetectedError) as error:raise HTTPException(422,str(error))
    except MalwareScannerUnavailable as error:raise HTTPException(503,str(error))
    # Lock and bump the ticket before writing, serializing attachment count/revisions.
    reserve(db,SupportTicket,t.id,expected_revision,{'updated_at':utcnow()})
    attachments=db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id==t.id)).all()
    digest=hashlib.sha256(content).hexdigest()
    if len(attachments)>=10:db.rollback();raise HTTPException(422,'A ticket supports at most ten attachments.')
    if any(a.sha256==digest for a in attachments):db.rollback();raise HTTPException(409,'This document is already attached.')
    storage=create_storage(settings);key=storage.put(content,'.'+checked.extension)
    try:
        db.add(TicketAttachment(ticket_id=t.id,uploaded_by=user.id,filename=checked.safe_filename,storage_key=key,sha256=digest))
        audit(db,user,'ticket_document_attached','support_ticket',t.id,{'filename':checked.safe_filename});db.commit()
    except Exception:db.rollback();storage.delete(key);raise
    db.expire_all();return service.ticket_json(db,db.get(SupportTicket,t.id),True)

@router.get('/api/community/tickets/{ticket_id}/attachments/{attachment_id}')
def download_attachment(ticket_id:uuid.UUID,attachment_id:uuid.UUID,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    service.ticket_for_actor(db,ticket_id,user,user.role in {'reviewer','admin'})
    a=db.get(TicketAttachment,attachment_id)
    if a is None or a.ticket_id!=ticket_id:raise HTTPException(404,'Attachment not found.')
    content=create_storage(get_settings()).read(a.storage_key)
    audit(db,user,'ticket_document_read','support_ticket',ticket_id,{'attachment_id':str(a.id)});db.commit()
    return Response(content,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{a.filename}"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@router.get('/api/staff/portal/submissions',response_model=ItemsResponse)
def staff_submissions(user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return {'items':[service.submission_review_json(db,s) for s in db.scalars(select(PlotSubmission).order_by(PlotSubmission.created_at.desc()))]}

@router.post('/api/staff/portal/submissions/{submission_id}/review',response_model=dict)
def review_submission(submission_id:uuid.UUID,payload:ReviewInput,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    s=db.get(PlotSubmission,submission_id)
    if s is None:raise HTTPException(404,'Submission not found.')
    # One plot lock orders concurrent review of different snapshots.
    db.scalar(select(UserPlot).where(UserPlot.id==s.plot_id).with_for_update())
    db.refresh(s)
    if s.status not in {'submitted','under_review'}:raise HTTPException(409,'This review is final. The owner must edit and resubmit a new revision.')
    if payload.status=='approved':
        published=db.scalars(select(PlotSubmission).where(PlotSubmission.plot_id==s.plot_id,PlotSubmission.status=='approved')).all()
        if any(p.plot_revision>s.plot_revision for p in published):raise HTTPException(409,'A newer boundary is already approved.')
        older=db.scalars(select(PlotSubmission).where(PlotSubmission.plot_id==s.plot_id,
            PlotSubmission.plot_revision<s.plot_revision,PlotSubmission.status.in_(['approved','submitted','under_review']))).all()
        for p in older:
            p.status='superseded';p.revision+=1;p.reviewed_at=utcnow();p.reviewed_by=user.id
            p.review_note=(p.review_note+' ' if p.review_note else '')+'Superseded by an approved newer plot revision.'
    reserve(db,PlotSubmission,s.id,payload.expected_revision,{'status':payload.status,'review_note':payload.note,'reviewed_by':user.id,'reviewed_at':utcnow()})
    audit(db,user,'plot_submission_reviewed','plot_submission',s.id,{'status':payload.status,'note':payload.note})
    # Keep document-request conversations in sync with the linked review.
    for ticket in db.scalars(select(SupportTicket).where(SupportTicket.plot_id==s.plot_id,SupportTicket.category=='plot_mapping')):
        db.execute(update(SupportTicket).where(SupportTicket.id==ticket.id).values(
            revision=SupportTicket.revision+1, updated_at=utcnow(),
            status='resolved' if payload.status=='approved' else ('awaiting_user' if payload.status=='changes_requested' else 'in_progress')))
        db.add(TicketMessage(ticket_id=ticket.id,author_id=user.id,author_role=user.role,
            body=f'Plot review: {payload.status.replace("_", " ")}. {payload.note}'))
    db.commit();db.expire_all();return service.submission_review_json(db,db.get(PlotSubmission,s.id))

@router.get('/api/staff/portal/tickets',response_model=ItemsResponse)
def staff_tickets(user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return {'items':[service.staff_ticket_json(db,t) for t in db.scalars(select(SupportTicket).order_by(SupportTicket.updated_at.desc()))]}

@router.get('/api/staff/portal/tickets/{ticket_id}',response_model=dict)
def staff_ticket(ticket_id:uuid.UUID,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return service.staff_ticket_json(db,service.ticket_for_actor(db,ticket_id,user,True),True)

@router.post('/api/staff/portal/tickets/{ticket_id}/messages',response_model=dict,status_code=201)
def staff_reply(ticket_id:uuid.UUID,payload:MessageInput,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return add_message(db,service.ticket_for_actor(db,ticket_id,user,True),payload,user,True)

@router.get('/api/staff/portal/publications',response_model=ItemsResponse)
def staff_publications(user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return {'items':[service.publication_json(p) for p in service.publications(db,staff=True)]}

@router.post('/api/staff/portal/publications',response_model=dict,status_code=201)
def create_publication(payload:PublicationInput,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    p=PortalPublication(created_by=user.id,**payload.model_dump());db.add(p);db.flush()
    audit(db,user,'publication_created','portal_publication',p.id,{'status':p.status,'kind':p.kind});db.commit();return service.publication_json(p)

@router.put('/api/staff/portal/publications/{publication_id}',response_model=dict)
def edit_publication(publication_id:uuid.UUID,payload:PublicationUpdate,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    p=db.get(PortalPublication,publication_id)
    if p is None:raise HTTPException(404,'Publication not found.')
    reserve(db,PortalPublication,p.id,payload.expected_revision,payload.model_dump(exclude={'expected_revision'}))
    audit(db,user,'publication_updated','portal_publication',p.id,{'status':payload.status});db.commit();db.expire_all();return service.publication_json(db.get(PortalPublication,publication_id))

@router.get('/api/staff/portal/plots',response_model=ItemsResponse)
def staff_plots(user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return {'items':[{'id':str(p.id),'owner_id':str(p.owner_id),'name':p.name,'village':p.village} for p in db.scalars(select(UserPlot).order_by(UserPlot.created_at.desc()))]}

@router.get('/api/staff/portal/resources',response_model=ItemsResponse)
def staff_resources(user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    return {'items':[service.resource_json(r) for r in db.scalars(select(UserResource).order_by(UserResource.observed_at.desc()))]}

@router.post('/api/staff/portal/resources',response_model=dict,status_code=201)
def create_resource(payload:ResourceInput,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    plot=db.get(UserPlot,payload.plot_id)
    if plot is None:raise HTTPException(404,'Plot not found.')
    resource=UserResource(owner_id=plot.owner_id,recorded_by=user.id,**payload.model_dump());db.add(resource);db.flush()
    audit(db,user,'user_resource_recorded','user_resource',resource.id,{'state':resource.verification_state});db.commit();return service.resource_json(resource)

@router.patch('/api/staff/portal/resources/{resource_id}',response_model=dict)
def review_resource(resource_id:uuid.UUID,payload:ResourceUpdate,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    r=db.get(UserResource,resource_id)
    if r is None:raise HTTPException(404,'Resource not found.')
    r.verification_state=payload.verification_state
    audit(db,user,'user_resource_reviewed','user_resource',r.id,{'state':r.verification_state});db.commit();return service.resource_json(r)

@router.get('/api/staff/portal/submissions/{submission_id}/overlaps',response_model=dict)
def staff_submission_overlaps(submission_id:uuid.UUID,user:AuthenticatedUser=Depends(require_reviewer),db:Session=Depends(get_db)):
    s=db.get(PlotSubmission,submission_id)
    if s is None:raise HTTPException(404,'Submission not found.')
    return service.overlap(db,s.geometry,user,exclude_plot=s.plot_id)

@router.post('/api/user/profile/otp/request',response_model=dict)
def request_contact_otp(payload:OTPRequest,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    from app.services.contact_otp import request_code
    return request_code(db,user,payload.channel)

@router.post('/api/user/profile/otp/verify',response_model=dict)
def verify_contact_otp(payload:OTPVerify,user:AuthenticatedUser=Depends(get_current_user),db:Session=Depends(get_db)):
    from app.services.contact_otp import verify_code
    result=verify_code(db,user,payload)
    audit(db,user,'contact_verified','user',user.id,{'channel':payload.channel});db.commit()
    return result


@router.post('/api/user/plot-requests', response_model=dict, status_code=201)
async def request_staff_mapping(
    subject: str = Form(..., min_length=3, max_length=180),
    message: str = Form(..., min_length=1, max_length=6000),
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Create request, message and required evidence in one transaction."""
    if len(subject.strip()) < 3 or not message.strip():
        raise HTTPException(422, 'Enter a request name and describe your land.')
    payload = TicketInput(category='plot_mapping', subject=subject, message=message)
    ticket = SupportTicket(owner_id=user.id, category=payload.category, subject=payload.subject)
    db.add(ticket)
    db.flush()
    db.add(TicketMessage(ticket_id=ticket.id, author_id=user.id, author_role='user', body=payload.message))
    audit(db, user, 'plot_mapping_requested', 'support_ticket', ticket.id, {})
    try:
        return await attach_file(ticket.id, file, ticket.revision, user, db)
    except Exception:
        db.rollback()
        raise


@router.post('/api/staff/portal/tickets/{ticket_id}/plot', response_model=dict, status_code=201)
def map_requested_plot(
    ticket_id: uuid.UUID, payload: PlotInput,
    user: AuthenticatedUser = Depends(require_reviewer), db: Session = Depends(get_db),
):
    """Staff map for the request owner; approval remains a separate action."""
    ticket = service.ticket_for_actor(db, ticket_id, user, True)
    if ticket.category != 'plot_mapping':
        raise HTTPException(422, 'Select a document mapping request.')
    if ticket.plot_id:
        raise HTTPException(409, 'A plot has already been created for this request.')
    if payload.expected_revision is None:
        raise HTTPException(422, 'The current request revision is required.')
    if not db.scalar(select(TicketAttachment.id).where(TicketAttachment.ticket_id == ticket.id).limit(1)):
        raise HTTPException(422, 'The request needs a supporting document before mapping.')
    geometry, area = validated_boundary(payload.coordinates)
    # Optimistic update serializes concurrent attempts, including multiple staff sessions.
    reserve(db, SupportTicket, ticket.id, payload.expected_revision, {'status':'in_progress', 'updated_at':utcnow()})
    plot = UserPlot(owner_id=ticket.owner_id, name=payload.name, village=payload.village.strip(), geometry=geometry, area_sqm=area)
    db.add(plot)
    db.flush()
    profile = db.get(CommunityProfile, ticket.owner_id)
    submission = PlotSubmission(plot_id=plot.id, owner_id=ticket.owner_id, plot_revision=plot.revision,
        name=plot.name, village=plot.village, geometry=geometry, area_sqm=area,
        public_label=profile.public_label if profile else '', community_name=profile.community_name if profile else '',
        share_label=profile.share_label if profile else False)
    db.add(submission)
    ticket.plot_id = plot.id
    db.add(TicketMessage(ticket_id=ticket.id, author_id=user.id, author_role=user.role,
        body=f'Staff mapped "{plot.name}" from your documents and submitted it for review. Follow its progress in My plots.'))
    db.flush()
    audit(db, user, 'staff_plot_created', 'user_plot', plot.id, {'owner_id':str(ticket.owner_id), 'ticket_id':str(ticket.id)})
    audit(db, user, 'plot_submitted', 'plot_submission', submission.id, {'plot_revision':plot.revision})
    db.commit()
    return service.submission_review_json(db, submission)
