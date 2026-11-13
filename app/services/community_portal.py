"""One read model drives dashboards, Atlas and staff/user review state."""
from datetime import timezone
from collections import defaultdict
import hashlib
from sqlalchemy import select
from fastapi import HTTPException
from shapely.geometry import shape, mapping
from app.db.models import User, utcnow
from app.db.user_plot_models import UserPlot
from app.db.community_models import CommunityProfile, PlotSubmission, PortalPublication, UserResource, SupportTicket, TicketMessage, TicketAttachment

CONTRACT='community-portal-v1'
def iso(value): return utc(value).isoformat() if value else None
def utc(value): return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

def publications(db,kind=None,staff=False):
    query=select(PortalPublication).order_by(PortalPublication.created_at.desc(),PortalPublication.id)
    if kind:query=query.where(PortalPublication.kind==kind)
    if not staff:query=query.where(PortalPublication.status=='published')
    now=utcnow(); rows=db.scalars(query).all()
    return [r for r in rows if staff or not r.ends_at or utc(r.ends_at)>now]

def publication_json(p):
    now=utcnow()
    return {'contract_version':CONTRACT, **{k:getattr(p,k) for k in ['kind','title','body','source_url','district','status','synthetic','revision']}} | {
        'id':str(p.id),'starts_at':iso(p.starts_at),'ends_at':iso(p.ends_at),'created_at':iso(p.created_at),
        'availability':'upcoming' if p.starts_at and utc(p.starts_at)>now else 'current'}

def resource_json(r):
    return {'contract_version':CONTRACT, **{k:getattr(r,k) for k in ['name','description','source','verification_state','synthetic']}} | {
        'id':str(r.id),'plot_id':str(r.plot_id),'observed_at':iso(r.observed_at)}

def profile_json(db,user):
    p=db.get(CommunityProfile,user.id); identity=db.get(User,user.id)
    fields={k:getattr(p,k) if p else (False if k=='share_label' else '') for k in ['community_name','public_label','village','phone','email','livelihood','share_label']}
    has_plot=db.scalar(select(UserPlot.id).where(UserPlot.owner_id==user.id).limit(1)) is not None
    has_document=db.scalar(select(SupportTicket.id).where(SupportTicket.owner_id==user.id,SupportTicket.category=='plot_mapping').limit(1)) is not None
    complete={'name':bool(identity.display_name),'village':bool(fields['village']),'livelihood':bool(fields['livelihood']),'land_record':has_plot or has_document}
    return {'email_verification_method':p.email_verification_method if p else None,
            'phone_verification_method':p.phone_verification_method if p else None,
            'email_verified':bool(p and p.email_verified_at),'phone_verified':bool(p and p.phone_verified_at),
            'email_verified_at':iso(p.email_verified_at) if p else None,'phone_verified_at':iso(p.phone_verified_at) if p else None,
            'contract_version':CONTRACT,'display_name':identity.display_name or '',**fields,'revision':p.revision if p else 0,
            'completion_percent':round(sum(complete.values())/len(complete)*100),'steps':complete,
            'missing_steps':[k for k,v in complete.items() if not v]}

def submission_json(s):
    return {'contract_version':CONTRACT,'id':str(s.id),'plot_id':str(s.plot_id),'plot_revision':s.plot_revision,'name':s.name,'village':s.village,
            'geometry':s.geometry,'area_sqm':float(s.area_sqm),'public_label':s.public_label if s.share_label else 'Community member',
            'community_name':s.community_name if s.share_label else '', 'status':s.status,'review_note':s.review_note,
            'revision':s.revision,'created_at':iso(s.created_at),'reviewed_at':iso(s.reviewed_at)}

def submission_review_json(db,s):
    result=submission_json(s)
    identity=db.get(User,s.owner_id)
    result['owner']={'user_id':str(identity.id),'username':identity.external_id,'display_name':identity.display_name,
                     'profile':profile_json(db,identity)}
    return result

def approved_rows(db):
    return db.scalars(select(PlotSubmission).where(PlotSubmission.status=='approved').order_by(PlotSubmission.id)).all()

def shared_identity(db,row):
    profile=db.get(CommunityProfile,row.owner_id)
    allowed=row.share_label and bool(profile and profile.share_label)
    return allowed, ((row.public_label or row.community_name or 'Community member') if allowed else 'Community member')

def atlas(db,user):
    rows=approved_rows(db); features=[]
    for row in rows:
        allowed,label=shared_identity(db,row)
        color='#'+hashlib.sha256(str(row.owner_id).encode()).hexdigest()[:6]
        features.append({'type':'Feature','id':str(row.id),'geometry':row.geometry,'properties':{
            'submission_id':str(row.id),'plot_id':str(row.plot_id),'label':label,
            'community_name':row.community_name if allowed else '', 'name':row.name if allowed or row.owner_id==user.id else 'Reviewed plot','village':row.village,
            'color':color,'is_mine':row.owner_id==user.id,'status':'approved','area_sqm':float(row.area_sqm),
            'boundary_meaning':'Staff-approved mapping, not a legal title'}})
    return {'type':'FeatureCollection','features':features,'contract_version':CONTRACT,
            'boundary_meaning':'Staff-approved community mapping; not an official FRA title.'}

def overlap(db,geometry,user,exclude_plot=None):
    candidate=shape(geometry); findings=[]
    for other in approved_rows(db):
        if exclude_plot and other.plot_id==exclude_plot:continue
        intersection=candidate.intersection(shape(other.geometry))
        if intersection.is_empty or intersection.area<=0:continue
        # Local equal-area projection accounts for latitude when measuring intersection.
        from pyproj import CRS, Transformer
        from shapely.ops import transform
        centre=candidate.centroid
        project=Transformer.from_crs('EPSG:4326',CRS.from_proj4(f'+proj=laea +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m'),always_xy=True).transform
        sqm=transform(project,intersection).area
        total=transform(project,candidate).area
        if sqm<0.01:continue
        findings.append({'submission_id':str(other.id),'plot_id':str(other.plot_id),
            'label':shared_identity(db,other)[1],
            'overlap_sqm':round(sqm,2),'candidate_overlap_percent':round(sqm/total*100,2),'geometry':mapping(intersection)})
    return {'contract_version':CONTRACT,'items':findings,'has_overlap':bool(findings),
            'message':'Supporting map comparison only; overlaps require staff review.'}

def ticket_for_actor(db,identifier,user,staff=False):
    query=select(SupportTicket).where(SupportTicket.id==identifier)
    if not staff:query=query.where(SupportTicket.owner_id==user.id)
    result=db.scalar(query)
    if result is None:raise HTTPException(404,'Ticket not found.')
    return result

def ticket_json(db,t,detail=False):
    last=db.scalar(select(TicketMessage).where(TicketMessage.ticket_id==t.id).order_by(TicketMessage.created_at.desc(),TicketMessage.id.desc()).limit(1))
    result={'contract_version':CONTRACT,'id':str(t.id),'category':t.category,'subject':t.subject,'plot_id':str(t.plot_id) if t.plot_id else None,
        'scheme_id':str(t.scheme_id) if t.scheme_id else None,'status':t.status,'revision':t.revision,
        'created_at':iso(t.created_at),'updated_at':iso(t.updated_at),
        'latest_reply_role':last.author_role if last else None,'latest_reply':last.body if last else ''}
    if detail:
        result['messages']=[{'id':str(m.id),'author_role':m.author_role,'body':m.body,'created_at':iso(m.created_at)} for m in db.scalars(select(TicketMessage).where(TicketMessage.ticket_id==t.id).order_by(TicketMessage.created_at,TicketMessage.id))]
        result['attachments']=[{'id':str(a.id),'filename':a.filename,'created_at':iso(a.created_at),
            'download_url':f'/api/community/tickets/{t.id}/attachments/{a.id}'} for a in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id==t.id).order_by(TicketAttachment.created_at))]
    return result

def dashboard(db,user):
    submissions=db.scalars(select(PlotSubmission).where(PlotSubmission.owner_id==user.id).order_by(PlotSubmission.created_at.desc())).all()
    tickets=db.scalars(select(SupportTicket).where(SupportTicket.owner_id==user.id).order_by(SupportTicket.updated_at.desc())).all()
    resources=db.scalars(select(UserResource).where(UserResource.owner_id==user.id,UserResource.verification_state=='verified').order_by(UserResource.observed_at.desc())).all()
    plots=db.scalars(select(UserPlot).where(UserPlot.owner_id==user.id).order_by(UserPlot.updated_at.desc())).all()
    by_plot=defaultdict(list)
    for submission in submissions:by_plot[submission.plot_id].append(submission)
    return {'contract_version':CONTRACT,'generated_at':iso(utcnow()),'profile':profile_json(db,user),
        'counts':{'plots':len(plots),'approved_plots':sum(s.status=='approved' for s in submissions),
            'pending_reviews':sum(s.status in {'submitted','under_review'} for s in submissions),
            'open_tickets':sum(t.status not in {'resolved','closed'} for t in tickets),'verified_resources':len(resources)},
        'announcements':[publication_json(p) for p in publications(db,'announcement')],
        'schemes':[publication_json(p) for p in publications(db,'scheme')],
        'resources':[resource_json(r) for r in resources], 'submissions':[submission_json(s) for s in submissions],
        'tickets':[ticket_json(db,t) for t in tickets],
        'plot_workflows':[plot_workflow(p, by_plot[p.id]) for p in plots]}


def plot_workflow(plot, submissions):
    current = next((s for s in submissions if s.plot_id == plot.id and s.plot_revision == plot.revision), None)
    approved = next((s for s in submissions if s.plot_id == plot.id and s.status == 'approved'), None)
    status = current.status if current else 'draft'
    done = status == 'approved'
    steps = [
        {'key':'mapped', 'label':'Mapped', 'state':'done', 'at':iso(plot.updated_at)},
        {'key':'review', 'label':'In review' if status == 'under_review' else 'Awaiting review',
         'state':'done' if done else ('current' if status in {'submitted','under_review'} else 'pending'),
         'at':iso(current.created_at) if current else None},
        {'key':'approved', 'label':'Approved', 'state':'done' if done else 'pending',
         'at':iso(current.reviewed_at) if done else None},
        {'key':'analysis', 'label':'Analysis pending', 'state':'pending', 'at':None},
    ]
    if status in {'changes_requested','rejected','superseded'}:
        steps[1].update(label={'changes_requested':'Changes needed','rejected':'Rejected','superseded':'Superseded'}[status], state='attention')
    return {'plot_id':str(plot.id), 'name':plot.name, 'plot_revision':plot.revision,
        'status':status, 'review_note':current.review_note if current else '', 'steps':steps,
        'atlas_visible':approved is not None, 'approved_revision':approved.plot_revision if approved else None,
        'analysis_status':'not_connected',
        'analysis_note':'Satellite retrieval and the segmentation runner are not connected to community plots yet.'}


def staff_ticket_json(db,t,detail=False):
    result=ticket_json(db,t,detail)
    owner=db.get(User,t.owner_id)
    result['owner']={'display_name':owner.display_name, 'username':owner.external_id}
    return result
