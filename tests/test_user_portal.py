"""Account separation, durable boundary persistence and concurrent edit protection."""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app as portal_app
from app.db.base import Base
from app.db.models import User
from app.db.session import get_db

class UserPortalTests(unittest.TestCase):
    def setUp(self):
        self.clear_limits()
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(self.engine,expire_on_commit=False)
        app=portal_app
        def db():
            with self.factory() as session: yield session
        app.dependency_overrides[get_db]=db
        self.client=TestClient(app, base_url="https://testserver")
    def clear_limits(self):
        node = portal_app.middleware_stack
        while node is not None:
            if hasattr(node, "hits"): node.hits.clear()
            node = getattr(node, "app", None)
    def tearDown(self):
        self.clear_limits()
        self.client.close(); portal_app.dependency_overrides.clear(); self.engine.dispose()
    def register(self,name='alice'):
        return self.client.post('/api/auth/register',json={'username':name,'display_name':name.title(),'password':'a secure passphrase','role':'admin'})
    def boundary(self):
        return {'name':'North field','village':'Salem','coordinates':[[78.14,11.66],[78.141,11.66],[78.141,11.661],[78.14,11.661]]}
    def test_register_login_and_role_cannot_be_chosen(self):
        response=self.register(); self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['role'],'user'); self.assertEqual(response.json()['redirect'],'/my-plots')
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertEqual(self.client.get('/fra', follow_redirects=False).headers['location'], '/my-plots')
        self.assertEqual(self.client.get('/my-plots').status_code, 200)
        self.assertEqual(self.register().status_code,409)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'alice','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'alice','password':'a secure passphrase'}).status_code,200)
    def test_owner_isolation_and_closed_persisted_geometry(self):
        self.register(); response=self.client.post('/api/user/plots',json=self.boundary()); self.assertEqual(response.status_code,201)
        plot=response.json(); ring=plot['geometry']['coordinates'][0][0]
        self.assertEqual(ring[0],ring[-1]); self.assertGreater(plot['area_sqm'],10000)
        self.assertEqual(len(self.client.get('/api/user/plots').json()['items']),1)
        self.register('bob')
        self.assertEqual(self.client.get('/api/user/plots').json()['items'],[])
        self.assertEqual(self.client.get('/api/user/plots/'+plot['id']).status_code,404)
        self.assertEqual(self.client.put('/api/user/plots/'+plot['id'],json={**self.boundary(),'expected_revision':1}).status_code,404)
    def test_invalid_polygons_and_revision_conflicts(self):
        self.register()
        for points in [[[78,11],[78,11],[79,12]],[[78,11],[79,12],[78,12],[79,11]]]:
            self.assertEqual(self.client.post('/api/user/plots',json={**self.boundary(),'coordinates':points}).status_code,422)
        plot=self.client.post('/api/user/plots',json=self.boundary()).json()
        payload={**self.boundary(),'name':'Revised plot','expected_revision':1}
        update=self.client.put('/api/user/plots/'+plot['id'],json=payload)
        self.assertEqual(update.status_code,200);self.assertEqual(update.json()['revision'],2)
        self.assertEqual(self.client.put('/api/user/plots/'+plot['id'],json=payload).status_code,409)
    def test_anonymous_access_is_denied(self):
        self.assertEqual(self.client.get('/api/user/plots').status_code,401)
    def test_login_uses_stored_staff_role(self):
        self.register()
        with self.factory() as db:
            user=db.scalar(select(User).where(User.external_id=='alice'));user.role='admin';db.commit()
        result=self.client.post('/api/auth/login',json={'username':'alice','password':'a secure passphrase'})
        self.assertEqual(result.json()['role'],'admin');self.assertEqual(result.json()['redirect'],'/fra')

if __name__=='__main__':unittest.main()
