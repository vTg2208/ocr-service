"""Exercise live APIs with separate browser cookies, without the seed fixture."""
import uuid
import unittest
from unittest.mock import patch
from sqlalchemy import select, func
from app.config import get_settings
from app.db.models import User
from app.db.community_models import SupportTicket, TicketAttachment, TicketMessage, PlotSubmission, PortalPublication, UserResource
from app.db.user_plot_models import UserPlot
from tests import test_community_portal as fixture_module
Fixtures = fixture_module.CommunityWorkflowTests
from scripts.clear_portal_examples import clear_examples

class LivePortalTests(unittest.TestCase):
    tearDown=Fixtures.tearDown;clear_limits=Fixtures.clear_limits
    def setUp(self):
        fixture_module.CommunityWorkflowTests.setUp(self)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    account=Fixtures.account;plot=Fixtures.plot;submit=Fixtures.submit;review=Fixtures.review

    def document_request(self, client=None, content=b'%PDF-1.4\nland evidence\n%%EOF'):
        settings=get_settings()
        with patch.object(settings,'secure_upload_dir',self.temp.name),patch.object(settings,'upload_storage_backend','local'),patch('app.api.community_routes.ClamAVScanner.scan'):
            return (client or self.alice).post('/api/user/plot-requests',data={'subject':'My family land','message':'Please map my field near the village.'},files={'file':('land.pdf',content,'application/pdf')})

    def test_document_to_staff_mapping_approval_and_reply(self):
        response=self.document_request();self.assertEqual(response.status_code,201,response.text);ticket=response.json()
        self.assertEqual(ticket['category'],'plot_mapping');self.assertEqual(len(ticket['attachments']),1)
        self.assertEqual(self.alice.get('/api/user/plots').json()['items'],[])
        self.assertEqual(self.bob.get('/api/user/tickets/'+ticket['id']).status_code,404)
        self.assertEqual(self.bob.get(ticket['attachments'][0]['download_url']).status_code,404)
        self.assertEqual(self.staff.get('/api/staff/portal/tickets').json()['items'][0]['id'],ticket['id'])
        payload={'name':'Mapped from documents','village':'Salem','coordinates':[[78.14,11.66],[78.141,11.66],[78.141,11.661],[78.14,11.661]],'expected_revision':ticket['revision']}
        url=f"/api/staff/portal/tickets/{ticket['id']}/plot"
        self.assertEqual(self.alice.post(url,json=payload).status_code,403)
        response=self.staff.post(url,json=payload);self.assertEqual(response.status_code,201,response.text);submission=response.json()
        self.assertEqual(self.staff.post(url,json=payload).status_code,409)
        self.assertEqual(len(self.alice.get('/api/user/plots').json()['items']),1)
        self.assertEqual(self.staff.get('/api/user/plots').json()['items'],[])
        self.assertEqual(self.bob.get('/api/user/atlas').json()['features'],[])
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['plot_workflows'][0]['status'],'submitted')
        self.review(submission)
        dashboard=self.alice.get('/api/user/dashboard').json();workflow=dashboard['plot_workflows'][0]
        self.assertTrue(workflow['atlas_visible']);self.assertEqual(workflow['status'],'approved')
        self.assertEqual([s['state'] for s in workflow['steps']],['done','done','done','pending'])
        self.assertEqual(workflow['analysis_status'],'not_connected')
        for client in [self.alice,self.bob,self.staff]:
            self.assertEqual(client.get('/api/user/atlas').json()['features'][0]['properties']['plot_id'],submission['plot_id'])
        detail=self.staff.get('/api/staff/portal/tickets/'+ticket['id']).json()
        reply=self.staff.post('/api/staff/portal/tickets/'+ticket['id']+'/messages',json={'message':'The document boundary has been approved.','status':'resolved','expected_revision':detail['revision']})
        self.assertEqual(reply.status_code,201,reply.text)
        owner=self.alice.get('/api/user/dashboard').json()['tickets'][0]
        self.assertEqual(owner['latest_reply'],'The document boundary has been approved.')
        self.assertEqual(self.bob.get('/api/user/dashboard').json()['tickets'],[])
        with self.factory() as db:
            p=db.get(UserPlot,uuid.UUID(submission['plot_id']));s=db.get(PlotSubmission,uuid.UUID(submission['id']))
            self.assertEqual(p.owner_id,s.owner_id);self.assertEqual(s.status,'approved');self.assertEqual(len(p.geometry['coordinates'][0][0]),5)
            self.assertEqual(db.get(SupportTicket,uuid.UUID(ticket['id'])).plot_id,p.id)

    def test_failed_document_upload_is_atomic_and_requires_evidence(self):
        r=self.document_request(content=b'not a PDF');self.assertEqual(r.status_code,422,r.text)
        self.assertEqual(self.staff.get('/api/staff/portal/tickets').json()['items'],[])
        with self.factory() as db:
            for model in [SupportTicket,TicketMessage,TicketAttachment]:self.assertEqual(db.scalar(select(func.count()).select_from(model)),0)
        r=self.alice.post('/api/user/tickets',json={'category':'plot_mapping','subject':'No evidence','message':'map it'})
        self.assertEqual(r.status_code,422)

    def test_self_mapping_timeline_admin_review_and_revised_boundary(self):
        plot=self.plot(self.alice)
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['plot_workflows'][0]['status'],'draft')
        submission=self.submit(self.alice,plot)
        with self.factory() as db:
            db.scalar(select(User).where(User.external_id=='reviewer')).role='admin';db.commit()
        self.assertEqual(self.staff.get('/staff-services').status_code,200)
        self.review(submission)
        revised=self.alice.put('/api/user/plots/'+plot['id'],json={'name':'Revised boundary','coordinates':[[78.14,11.66],[78.142,11.66],[78.142,11.661],[78.14,11.661]],'expected_revision':1}).json()
        w=self.alice.get('/api/user/dashboard').json()['plot_workflows'][0]
        self.assertEqual(w['status'],'draft');self.assertTrue(w['atlas_visible']);self.assertEqual(w['approved_revision'],1)
        s=self.submit(self.alice,revised);self.review(s,'changes_requested')
        w=self.alice.get('/api/user/dashboard').json()['plot_workflows'][0]
        self.assertEqual(w['steps'][1]['state'],'attention');self.assertIn('Checked source',w['review_note'])
        self.assertEqual(len(self.bob.get('/api/user/atlas').json()['features']),1)

    def test_cleanup_removes_examples_but_preserves_live_records(self):
        real=self.plot(self.alice);self.review(self.submit(self.alice,real))
        fake=self.account('synthetic-portal-alice');p=self.plot(fake);self.review(self.submit(fake,p))
        ticket=fake.post('/api/user/tickets',json={'category':'general','subject':'Example ticket','message':'Fixture message'}).json()
        for synthetic,title in [(True,'Example announcement'),(False,'Real announcement')]:
            self.staff.post('/api/staff/portal/publications',json={'kind':'announcement','title':title,'body':'An update','status':'published','synthetic':synthetic})
        with self.factory() as db:
            report,_=clear_examples(db);self.assertFalse(report['applied']);self.assertEqual(report['plots'],1)
            report,_=clear_examples(db,True);self.assertTrue(report['applied'])
        atlas=self.bob.get('/api/user/atlas').json()['features'];self.assertEqual(len(atlas),1);self.assertEqual(atlas[0]['properties']['plot_id'],real['id'])
        self.assertEqual(self.staff.get('/api/staff/portal/tickets').json()['items'],[])
        pubs=self.bob.get('/api/user/publications').json()['items'];self.assertEqual([p['title'] for p in pubs],['Real announcement'])
        self.assertEqual(fake.get('/api/user/plots').json()['items'],[])

del Fixtures
