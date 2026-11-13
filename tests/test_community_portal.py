"""Producer-to-consumer integration tests across independent user sessions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db.base import Base
from app.db.models import User
from app.db.session import get_db
from app.config import get_settings

class CommunityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.clear_limits()
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine);self.factory=sessionmaker(self.engine,expire_on_commit=False)
        def db():
            with self.factory() as session:yield session
        app.dependency_overrides[get_db]=db
        self.clients=[]
        self.alice=self.account('alice');self.bob=self.account('bob');self.staff=self.account('reviewer')
        with self.factory() as db:
            db.scalar(select(User).where(User.external_id=='reviewer')).role='reviewer';db.commit()
        self.temp=tempfile.TemporaryDirectory()
    def clear_limits(self):
        node = app.middleware_stack
        while node is not None:
            if hasattr(node, "hits"): node.hits.clear()
            node = getattr(node, "app", None)
    def tearDown(self):
        self.clear_limits()
        for c in self.clients:c.close()
        app.dependency_overrides.clear();self.engine.dispose();self.temp.cleanup()
    def account(self,name):
        c=TestClient(app,base_url='https://testserver');self.clients.append(c)
        r=c.post('/api/auth/register',json={'username':name,'display_name':name.title(),'password':'test passphrase 123'})
        self.assertEqual(r.status_code,201,r.text);return c
    def plot(self,c,name='Family plot',offset=0):
        r=c.post('/api/user/plots',json={'name':name,'village':'Salem','coordinates':[[78.14+offset,11.66],[78.141+offset,11.66],[78.141+offset,11.661],[78.14+offset,11.661]]})
        self.assertEqual(r.status_code,201,r.text);return r.json()
    def submit(self,c,p):
        r=c.post(f"/api/user/plots/{p['id']}/submit",json={'expected_revision':p['revision']});self.assertEqual(r.status_code,201,r.text);return r.json()
    def review(self,s,status='approved'):
        r=self.staff.post(f"/api/staff/portal/submissions/{s['id']}/review",json={'status':status,'note':'Checked source and boundary.','expected_revision':s['revision']});self.assertEqual(r.status_code,200,r.text);return r.json()
    def test_signup_has_dashboard_without_profile_or_plot_and_profile_is_private(self):
        self.assertEqual(self.alice.get('/my-plots').status_code,200)
        d=self.alice.get('/api/user/dashboard').json();self.assertEqual(d['counts']['plots'],0);self.assertIn('profile',d)
        self.assertEqual(self.alice.get('/api/user/atlas').status_code,200)
        payload={'display_name':'Alice','village':'Salem','livelihood':'Farming','public_label':'North community','community_name':'Community A','phone':'private-phone','share_label':True,'expected_revision':0}
        r=self.alice.put('/api/user/profile',json=payload);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['completion_percent'],75)
        self.assertNotIn('private-phone',self.bob.get('/api/user/dashboard').text)
        self.assertEqual(self.alice.put('/api/user/profile',json=payload).status_code,409)
    def test_review_gate_atlas_overlap_and_immutable_approved_boundary(self):
        p=self.plot(self.alice);s=self.submit(self.alice,p)
        self.assertEqual(self.bob.get('/api/user/atlas').json()['features'],[])
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['counts']['pending_reviews'],1)
        self.assertEqual(self.submit(self.alice,p)['id'],s['id'])
        self.assertEqual(self.alice.post(f"/api/staff/portal/submissions/{s['id']}/review",json={'status':'approved','note':'no','expected_revision':1}).status_code,403)
        self.review(s)
        shared=self.bob.get('/api/user/atlas').json();self.assertEqual(len(shared['features']),1)
        self.assertEqual(shared['features'][0]['properties']['label'],'Community member')
        self.assertNotIn('owner_id',str(shared));self.assertNotIn('phone',str(shared))
        overlap=self.bob.post('/api/user/overlap-check',json={'name':'Candidate','coordinates':[[78.1405,11.66],[78.1415,11.66],[78.1415,11.661],[78.1405,11.661]]})
        self.assertEqual(overlap.status_code,200,overlap.text);self.assertTrue(overlap.json()['has_overlap']);self.assertAlmostEqual(overlap.json()['items'][0]['candidate_overlap_percent'],50,delta=.5)
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['counts']['approved_plots'],1)
        revised=self.alice.put(f"/api/user/plots/{p['id']}",json={'name':'Moved draft','coordinates':[[79,11],[79.001,11],[79.001,11.001],[79,11.001]],'expected_revision':1}).json()
        self.assertEqual(self.bob.get('/api/user/atlas').json()['features'][0]['geometry'],shared['features'][0]['geometry'])
        new=self.submit(self.alice,revised);self.review(new)
        features=self.bob.get('/api/user/atlas').json()['features'];self.assertEqual(len(features),1);self.assertNotEqual(features[0]['geometry'],shared['features'][0]['geometry'])
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['counts']['approved_plots'],1)
    def test_new_approval_supersedes_pending_revisions_and_consent_can_be_revoked(self):
        payload={'display_name':'Alice','public_label':'Visible community','share_label':True,'expected_revision':0}
        self.alice.put('/api/user/profile',json=payload)
        plot=self.plot(self.alice);old=self.submit(self.alice,plot)
        revised=self.alice.put('/api/user/plots/'+plot['id'],json={'name':'Updated plot','coordinates':[[78.14,11.66],[78.141,11.66],[78.141,11.661],[78.14,11.661]],'expected_revision':1}).json()
        self.review(self.submit(self.alice,revised))
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['counts']['pending_reviews'],0)
        self.assertEqual(self.bob.get('/api/user/atlas').json()['features'][0]['properties']['label'],'Visible community')
        payload.update(share_label=False,expected_revision=1)
        self.alice.put('/api/user/profile',json=payload)
        self.assertEqual(self.bob.get('/api/user/atlas').json()['features'][0]['properties']['label'],'Community member')

    def test_request_changes_resubmission_and_stale_review(self):
        p=self.plot(self.alice);s=self.submit(self.alice,p);reviewed=self.review(s,'under_review')
        stale=self.staff.post(f"/api/staff/portal/submissions/{s['id']}/review",json={'status':'approved','note':'Stale','expected_revision':1})
        self.assertEqual(stale.status_code,409)
        self.review(reviewed,'changes_requested');self.assertEqual(self.bob.get('/api/user/atlas').json()['features'],[])
        self.assertIn('Checked source',self.alice.get('/api/user/dashboard').text)
    def test_publications_reflect_across_users_resources_only_to_owner(self):
        p=self.plot(self.alice)
        scheme=self.staff.post('/api/staff/portal/publications',json={'kind':'scheme','title':'Synthetic water scheme','body':'Sample assistance information','status':'draft','synthetic':True}).json()
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['schemes'],[])
        body={k:scheme[k] for k in ['kind','title','body','status','synthetic','district','source_url','starts_at','ends_at']};body.update(status='published',expected_revision=1)
        r=self.staff.put('/api/staff/portal/publications/'+scheme['id'],json=body);self.assertEqual(r.status_code,200,r.text)
        for c in [self.alice,self.bob]:self.assertEqual(len(c.get('/api/user/dashboard').json()['schemes']),1)
        resource=self.staff.post('/api/staff/portal/resources',json={'plot_id':p['id'],'name':'Water point','description':'Sample field observation','source':'Synthetic survey','synthetic':True}).json()
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['resources'],[])
        self.assertEqual(self.staff.patch('/api/staff/portal/resources/'+resource['id'],json={'verification_state':'verified'}).status_code,200)
        self.assertEqual(len(self.alice.get('/api/user/dashboard').json()['resources']),1)
        self.assertEqual(self.bob.get('/api/user/dashboard').json()['resources'],[])
        body.update(status='archived',expected_revision=2)
        self.staff.put('/api/staff/portal/publications/'+scheme['id'],json=body)
        self.assertEqual(self.bob.get('/api/user/dashboard').json()['schemes'],[])
    def test_tickets_staff_replies_documents_and_isolation(self):
        p=self.plot(self.alice)
        r=self.alice.post('/api/user/tickets',json={'category':'boundary_dispute','subject':'Please check overlap','message':'Private evidence request','plot_id':p['id']});self.assertEqual(r.status_code,201,r.text);t=r.json()
        self.assertEqual(self.bob.get('/api/user/tickets/'+t['id']).status_code,404)
        self.assertEqual(self.bob.get('/api/user/dashboard').json()['tickets'],[])
        self.assertEqual(len(self.staff.get('/api/staff/portal/tickets').json()['items']),1)
        settings=get_settings()
        with patch.object(settings,'secure_upload_dir',self.temp.name),patch.object(settings,'upload_storage_backend','local'),patch('app.api.community_routes.ClamAVScanner.scan'):
            upload=self.alice.post(f"/api/community/tickets/{t['id']}/attachments",data={'expected_revision':t['revision']},files={'file':('proof.pdf',b'%PDF-1.4\nsynthetic proof\n%%EOF','application/pdf')})
            self.assertEqual(upload.status_code,201,upload.text);t=upload.json();url=t['attachments'][0]['download_url']
            self.assertEqual(self.bob.get(url).status_code,404);self.assertEqual(self.staff.get(url).status_code,200)
        reply=self.staff.post(f"/api/staff/portal/tickets/{t['id']}/messages",json={'message':'We will arrange field verification.','status':'awaiting_user','expected_revision':t['revision']})
        self.assertEqual(reply.status_code,201,reply.text);t=reply.json()
        owner=self.alice.get('/api/user/dashboard').json()['tickets'][0];self.assertEqual(owner['latest_reply_role'],'reviewer');self.assertIn('field verification',owner['latest_reply'])
        self.assertEqual(self.bob.get('/api/user/dashboard').json()['tickets'],[])
        self.assertEqual(self.alice.post(f"/api/user/tickets/{t['id']}/messages",json={'message':'Thank you','status':'resolved','expected_revision':t['revision']}).status_code,403)
        stale=self.alice.post(f"/api/user/tickets/{t['id']}/messages",json={'message':'old tab','expected_revision':1});self.assertEqual(stale.status_code,409)
        final=self.staff.post(f"/api/staff/portal/tickets/{t['id']}/messages",json={'message':'Resolved after review.','status':'resolved','expected_revision':t['revision']});self.assertEqual(final.status_code,201)
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['counts']['open_tickets'],0)
    def test_synthetic_fixture_can_be_replayed_without_duplicates(self):
        import json
        from scripts.seed_community_portal import seed
        owner=self
        class InProcessAPI:
            def call(self,method,path,payload=None,token=None):
                r=owner.staff.request(method,path,json=payload,headers={'Authorization':'Bearer '+token} if token else {})
                owner.assertLess(r.status_code,300,r.text)
                return r.json()
        fixture=json.loads(Path('data/synthetic_community_portal.json').read_text())
        with patch('scripts.seed_community_portal.get_session_factory',return_value=self.factory):
            first=seed(InProcessAPI(),fixture,'synthetic test password')
            second=seed(InProcessAPI(),fixture,'synthetic test password')
        self.assertEqual(first['shared_atlas_features'],2)
        self.assertEqual(second['shared_atlas_features'],2)
        self.assertEqual([u['plot_id'] for u in first['users']],[u['plot_id'] for u in second['users']])
        self.assertEqual(len(self.staff.get('/api/staff/portal/publications').json()['items']),2)

    def test_permissions_and_api_contracts(self):
        for path in ['/api/staff/portal/submissions','/api/staff/portal/tickets','/api/staff/portal/resources','/api/staff/portal/publications','/api/staff/portal/plots']:
            self.assertEqual(self.alice.get(path).status_code,403,path)
        anonymous=TestClient(app,base_url='https://testserver');self.clients.append(anonymous)
        for path in ['/api/user/dashboard','/api/user/profile','/api/user/atlas','/api/user/submissions','/api/user/tickets','/api/user/publications','/api/user/resources']:
            self.assertEqual(anonymous.get(path).status_code,401,path)
        schema=self.staff.get('/openapi.json').json()['paths']
        self.assertIn('/api/user/dashboard',schema);self.assertIn('/api/staff/portal/submissions/{submission_id}/review',schema)
        self.assertEqual(self.alice.get('/api/user/dashboard').json()['contract_version'],'community-portal-v1')
        self.assertEqual(self.alice.get('/api/user/atlas').json()['contract_version'],'community-portal-v1')

if __name__=='__main__':unittest.main()
