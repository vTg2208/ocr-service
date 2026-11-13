"""Remove only the explicitly named portal fixture records; preserve real accounts/data.

Run without --apply to preview, then with --apply to remove these examples.
The database transaction aborts if a real record references a fixture plot or scheme.
"""
import argparse
import json
from sqlalchemy import select, delete, or_
from app.db.models import User
from app.db.community_models import PlotSubmission, PortalPublication, UserResource, SupportTicket, TicketMessage, TicketAttachment
from app.db.user_plot_models import UserPlot
from app.db.session import get_session_factory
from app.config import get_settings
from app.services.storage import create_storage

FIXTURE_USERS=('synthetic-portal-alice','synthetic-portal-bob','synthetic-portal-charlie')

def clear_examples(db, apply=False):
    owners=list(db.scalars(select(User.id).where(User.external_id.in_(FIXTURE_USERS))))
    plots=list(db.scalars(select(UserPlot.id).where(UserPlot.owner_id.in_(owners))))
    tickets=list(db.scalars(select(SupportTicket.id).where(SupportTicket.owner_id.in_(owners))))
    publications=list(db.scalars(select(PortalPublication.id).where(PortalPublication.synthetic.is_(True))))
    # Refuse to detach or delete a real person's records as a side effect.
    referenced=db.scalar(select(SupportTicket.id).where(~SupportTicket.owner_id.in_(owners),or_(SupportTicket.plot_id.in_(plots),SupportTicket.scheme_id.in_(publications))).limit(1))
    foreign_resource=db.scalar(select(UserResource.id).where(UserResource.plot_id.in_(plots),~UserResource.owner_id.in_(owners)).limit(1))
    if referenced or foreign_resource:
        raise RuntimeError('A real record references example data. No changes made; resolve those references before cleanup.')
    attachments=list(db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id.in_(tickets))))
    resource_ids=list(db.scalars(select(UserResource.id).where(or_(UserResource.owner_id.in_(owners),UserResource.synthetic.is_(True)))))
    summary={'plots':len(plots),'tickets':len(tickets),'attachments':len(attachments),'publications':len(publications),'resources':len(resource_ids),'applied':apply}
    if not apply:return summary,[]
    for model,condition in [
        (TicketAttachment,TicketAttachment.ticket_id.in_(tickets)),(TicketMessage,TicketMessage.ticket_id.in_(tickets)),
        (SupportTicket,SupportTicket.id.in_(tickets)),(UserResource,UserResource.id.in_(resource_ids)),
        (PlotSubmission,PlotSubmission.plot_id.in_(plots)),(UserPlot,UserPlot.id.in_(plots)),
        (PortalPublication,PortalPublication.id.in_(publications))]:
        db.execute(delete(model).where(condition))
    # Retain identity/profile/audit history. No real account is removed or reset.
    db.commit()
    return summary,[a.storage_key for a in attachments]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    with get_session_factory()() as db:summary,keys=clear_examples(db,args.apply)
    storage=create_storage(get_settings())
    failures=0
    for key in keys:
        try:storage.delete(key)
        except Exception:failures+=1
    if failures:summary['private_file_cleanup_failures']=failures
    print(json.dumps(summary,indent=2))
    if not args.apply:print('Preview only. Add --apply to remove these example records.')

if __name__=='__main__':main()
