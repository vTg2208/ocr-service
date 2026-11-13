"""Contact persistence, challenge ownership, one-time use and contact-change invalidation."""
from datetime import timedelta
import unittest
from unittest.mock import patch
from tests import test_community_portal as workflow_fixture
from app.db.community_models import CommunityProfile,ContactOTP
from app.db.models import User,utcnow
from sqlalchemy import select

class ContactVerificationTests(unittest.TestCase):
    setUp=workflow_fixture.CommunityWorkflowTests.setUp
    tearDown=workflow_fixture.CommunityWorkflowTests.tearDown
    clear_limits=workflow_fixture.CommunityWorkflowTests.clear_limits
    account=workflow_fixture.CommunityWorkflowTests.account
    # Reuse the integration fixture only, without inheriting its workflow tests.
    def save_contacts(self,email='alice@gmail.com',phone='+919876543210'):
        old=self.alice.get('/api/user/profile').json()
        payload={key:old[key] for key in ['display_name','community_name','public_label','village','phone','email','livelihood','share_label']}
        payload.update(email=email,phone=phone,expected_revision=old['revision'])
        result=self.alice.put('/api/user/profile',json=payload);self.assertEqual(result.status_code,200,result.text);return result.json()
    def request_otp(self,channel='email',code=123456):
        with patch('app.services.contact_otp.secrets.randbelow',return_value=code),self.assertLogs('app.contact_otp',level='WARNING') as logs:
            result=self.alice.post('/api/user/profile/otp/request',json={'channel':channel})
        self.assertEqual(result.status_code,200,result.text);self.assertIn(f'{code:06d}',logs.output[0]);self.assertNotIn(f'{code:06d}',result.text)
        return result.json()
    def verify(self,challenge,client=None,code='123456'):
        return (client or self.alice).post('/api/user/profile/otp/verify',json={'channel':challenge['channel'],'challenge_id':challenge['challenge_id'],'code':code})
    def test_otp_persistence_and_contact_update_resets_verification(self):
        result=self.save_contacts(email='Alice@Gmail.com');self.assertEqual(result['email'],'alice@gmail.com');self.assertFalse(result['email_verified'])
        challenge=self.request_otp();self.assertEqual(self.verify(challenge).status_code,200)
        profile=self.alice.get('/api/user/profile').json();self.assertTrue(profile['email_verified']);self.assertEqual(profile['email_verification_method'],'development_terminal')
        self.assertTrue(self.alice.get('/api/user/dashboard').json()['profile']['email_verified'])
        self.assertEqual(self.verify(challenge).status_code,422)
        with self.factory() as db:
            user=db.scalar(select(User).where(User.external_id=='alice'));p=db.get(CommunityProfile,user.id);self.assertIsNotNone(p.email_verified_at)
            otp=db.scalar(select(ContactOTP).where(ContactOTP.user_id==user.id));self.assertNotEqual(otp.code_digest,'123456');self.assertIsNotNone(otp.consumed_at)
        updated=self.save_contacts(email='new@gmail.com');self.assertFalse(updated['email_verified']);self.assertIsNone(updated['email_verified_at']);self.assertIsNone(updated['email_verification_method'])
    def test_wrong_codes_attempt_limit_and_cross_user_denial(self):
        self.save_contacts();challenge=self.request_otp()
        self.assertEqual(self.verify(challenge,client=self.bob).status_code,422)
        for _ in range(5):self.assertEqual(self.verify(challenge,code='000000').status_code,422)
        self.assertEqual(self.verify(challenge).status_code,422)
        self.assertFalse(self.alice.get('/api/user/profile').json()['email_verified'])
    def test_expiry_cooldown_and_old_contact_challenges(self):
        self.save_contacts();challenge=self.request_otp()
        self.assertEqual(self.alice.post('/api/user/profile/otp/request',json={'channel':'email'}).status_code,429)
        self.save_contacts(email='other@gmail.com');self.assertEqual(self.verify(challenge).status_code,422)
        challenge=self.request_otp(channel='phone')
        with self.factory() as db:
            import uuid
            otp=db.get(ContactOTP,uuid.UUID(challenge['challenge_id']));otp.expires_at=utcnow()-timedelta(seconds=1);db.commit()
        self.assertEqual(self.verify(challenge).status_code,422)
    def test_phone_success_and_email_validation(self):
        self.assertEqual(self.alice.put('/api/user/profile',json={'display_name':'Alice','email':'not an email','expected_revision':0}).status_code,422)
        self.save_contacts();challenge=self.request_otp('phone');self.assertEqual(self.verify(challenge).status_code,200)
        self.assertTrue(self.alice.get('/api/user/profile').json()['phone_verified'])
        self.assertFalse(self.bob.get('/api/user/profile').json()['phone_verified'])

class ContactMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_existing_profiles(self):
        import tempfile,uuid
        from pathlib import Path
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine,text
        with tempfile.TemporaryDirectory() as temp:
            url='sqlite+pysqlite:///'+str(Path(temp)/'upgrade.db');config=Config('alembic.ini');config.set_main_option('sqlalchemy.url',url)
            command.upgrade(config,'20261112_0015');engine=create_engine(url)
            with engine.begin() as conn:
                identifier=uuid.uuid4().hex
                conn.execute(text("INSERT INTO users (id,external_id,role,created_at) VALUES (:id,'existing','user',CURRENT_TIMESTAMP)"),{'id':identifier})
                conn.execute(text("INSERT INTO community_profiles (user_id,community_name,public_label,village,phone,livelihood,share_label,revision,updated_at) VALUES (:id,'','','Salem','1234567890','farmer',0,1,CURRENT_TIMESTAMP)"),{'id':identifier})
            command.upgrade(config,'head')
            with engine.connect() as conn:
                row=conn.execute(text('SELECT email,email_verified_at,phone_verified_at,village FROM community_profiles')).one()
                self.assertEqual(tuple(row),('',None,None,'Salem'))
            engine.dispose()
