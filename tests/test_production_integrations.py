"""Protocol-level OIDC, shared quotas, and delivery boundaries without external accounts."""
import asyncio
import base64
import hashlib
import json
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.starlette_client import OAuth
from cryptography.hazmat.primitives.asymmetric import rsa
import fakeredis.aioredis
import httpx
import jwt
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, func

from app.config import Settings, get_settings
from app.db.community_models import ContactOTP
from app.db.models import User
from app.services.contact_delivery import DeliveryUnavailable, deliver_code
from app.services.oidc import external_identity
from app.services.rate_limit import RedisRateLimiter
from tests import test_community_portal as fixture


class OIDCFlowTests(unittest.TestCase):
    setUp=fixture.CommunityWorkflowTests.setUp
    tearDown=fixture.CommunityWorkflowTests.tearDown
    clear_limits=fixture.CommunityWorkflowTests.clear_limits
    account=fixture.CommunityWorkflowTests.account

    def client(self, *, bad_nonce=False):
        self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public=jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(),as_dict=True)
        public.update(kid='test-key',alg='RS256',use='sig')
        self.verifier_seen=False
        def provider(request):
            path=request.url.path
            if path.endswith('/.well-known/openid-configuration'):
                return httpx.Response(200,json={'issuer':'https://identity.test','authorization_endpoint':'https://identity.test/authorize',
                    'token_endpoint':'https://identity.test/token','jwks_uri':'https://identity.test/keys',
                    'id_token_signing_alg_values_supported':['RS256'],'response_types_supported':['code'],
                    'subject_types_supported':['public']})
            if path=='/keys':return httpx.Response(200,json={'keys':[public]})
            if path=='/token':
                form=parse_qs(request.content.decode())
                verifier=form['code_verifier'][0]
                self.verifier_seen=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()==self.challenge
                now=int(time.time())
                token=jwt.encode({'iss':'https://identity.test','sub':'issuer-subject-1','aud':'client-one',
                    'iat':now,'exp':now+300,'nonce':'wrong' if bad_nonce else self.nonce,'name':'OIDC Example',
                    'roles':['admin']},self.key,algorithm='RS256',headers={'kid':'test-key'})
                return httpx.Response(200,json={'access_token':'provider-token','token_type':'Bearer','id_token':token})
            raise AssertionError('Unexpected OIDC request')
        oauth=OAuth()
        oauth.register('test',client_id='client-one',client_secret='test-secret',
            server_metadata_url='https://identity.test/.well-known/openid-configuration',
            client_kwargs={'scope':'openid profile','code_challenge_method':'S256','transport':httpx.MockTransport(provider)})
        return oauth.create_client('test')

    def configure(self):
        return patch.multiple(get_settings(),oidc_enabled=True,oidc_issuer='https://identity.test',
            oidc_client_id='client-one',oidc_client_secret=SecretStr('test-secret'),
            oidc_redirect_uri='https://testserver/api/auth/oidc/callback',oidc_auto_provision=True,local_auth_enabled=False)

    def start(self):
        response=self.alice.get('/api/auth/oidc/login',follow_redirects=False)
        self.assertEqual(response.status_code,302,response.text)
        query=parse_qs(urlsplit(response.headers['location']).query)
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.nonce=query['nonce'][0];self.challenge=query['code_challenge'][0]
        return query['state'][0]

    def test_real_signed_id_token_pkce_nonce_and_database_roles(self):
        client=self.client()
        with self.configure(),patch('app.api.oidc_routes.oidc_client',return_value=client):
            state=self.start()
            response=self.alice.get('/api/auth/oidc/callback',params={'state':state,'code':'code-one'},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text)
            self.assertEqual(response.headers['location'],'/my-plots')
            self.assertTrue(self.verifier_seen)
            self.assertNotIn('provider-token',response.headers.get('set-cookie',''))
            session=self.alice.get('/api/auth/session')
            self.assertEqual(session.status_code,200,session.text)
            self.assertEqual(session.json()['role'],'user')
            with self.factory() as db:
                user=db.scalar(select(User).where(User.external_id==external_identity('https://identity.test','issuer-subject-1')))
                self.assertEqual(user.role,'user')
            self.assertEqual(self.alice.get('/api/auth/oidc/callback',params={'state':state,'code':'code-one'}).status_code,401)
            self.assertEqual(self.bob.post('/api/auth/login',json={'username':'bob','password':'anything'}).status_code,403)
            self.assertEqual(self.bob.get('/api/auth/session').status_code,401)

    def test_wrong_nonce_does_not_provision_or_create_application_session(self):
        client=self.client(bad_nonce=True)
        with self.configure(),patch('app.api.oidc_routes.oidc_client',return_value=client):
            state=self.start()
            response=self.alice.get('/api/auth/oidc/callback',params={'state':state,'code':'bad'})
            self.assertEqual(response.status_code,401,response.text)
        with self.factory() as db:
            self.assertIsNone(db.scalar(select(User).where(User.external_id.like('oidc:%'))))


class SharedRateTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_instances_share_an_atomic_quota_and_expiry(self):
        server=fakeredis.FakeServer()
        first=RedisRateLimiter('',client=fakeredis.aioredis.FakeRedis(server=server))
        second=RedisRateLimiter('',client=fakeredis.aioredis.FakeRedis(server=server))
        try:
            results=await asyncio.gather(*[limiter.allow('subject',limit=3,window=60) for limiter in [first,second,first,second,first]])
            self.assertEqual(sum(allowed for allowed,ttl in results),3)
            self.assertTrue(all(0<ttl<=60 for allowed,ttl in results))
        finally:await first.close();await second.close()


class DeliveryTests(unittest.TestCase):
    def test_smtp_uses_tls_and_real_email_body(self):
        s=get_settings()
        with (patch.multiple(s,smtp_host='smtp.test',smtp_sender='noreply@example.test',smtp_username='user',smtp_password=SecretStr('secret')),
                patch('app.services.contact_delivery.smtplib.SMTP') as smtp):
            smtp.return_value.__enter__.return_value.send_message.return_value={}
            deliver_code(channel='email',target='recipient@example.test',code='123456',account='test',method='smtp')
            client=smtp.return_value.__enter__.return_value
            client.starttls.assert_called_once()
            message=client.send_message.call_args.args[0]
            self.assertEqual(message['To'],'recipient@example.test')
            self.assertIn('123456',message.get_content())

    def test_twilio_request_is_bounded_authenticated_and_errors_are_redacted(self):
        s=get_settings()
        with patch.multiple(s,twilio_account_sid='AC'+'a'*32,twilio_auth_token=SecretStr('private-token'),twilio_sender='+15005550006'):
            with patch('app.services.contact_delivery.httpx.post') as post:
                deliver_code(channel='phone',target='+919876543210',code='123456',account='test',method='twilio')
                self.assertEqual(post.call_args.kwargs['data']['To'],'+919876543210')
                self.assertEqual(post.call_args.kwargs['timeout'],15)
                self.assertFalse(post.call_args.kwargs['follow_redirects'])
            with patch('app.services.contact_delivery.httpx.post',side_effect=RuntimeError('private-token 123456')):
                with self.assertRaisesRegex(DeliveryUnavailable,'provider is unavailable') as error:
                    deliver_code(channel='phone',target='+919876543210',code='123456',account='test',method='twilio')
                self.assertNotIn('private-token',str(error.exception))


class ProductionSettingsTests(unittest.TestCase):
    def test_production_requires_oidc_and_redis_and_rejects_plaintext_smtp(self):
        values={'environment':'production','auth_secret':'x'*48,'database_url':'postgresql+psycopg://db',
            'malware_scan_required':True,'demo_auth_enabled':False,'local_auth_enabled':False,
            'oidc_enabled':True,'oidc_issuer':'https://idp.example','oidc_client_id':'client',
            'oidc_client_secret':'secret','oidc_redirect_uri':'https://app.example/api/auth/oidc/callback',
            'redis_url':'redis://redis:6379/0'}
        Settings(_env_file=None,**values)
        for change in [{'local_auth_enabled':True},{'redis_url':''},{'smtp_tls_mode':'plain'},{'oidc_redirect_uri':'http://app.example/callback'}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):Settings(_env_file=None,**(values|change))
