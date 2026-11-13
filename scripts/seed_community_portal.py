"""Idempotent, explicitly synthetic, API-driven multi-user workflow fixture."""
import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from sqlalchemy import select
from app.api.auth import create_access_token
from app.db.models import User
from app.db.user_plot_models import AccountCredential
from app.db.session import get_session_factory
from app.services.account_passwords import hash_password, verify_password

class API:
    def __init__(self,base):self.base=base.rstrip('/')
    def call(self,method,path,payload=None,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers,method=method)
        try:
            with urlopen(req,timeout=30) as response:return json.load(response)
        except HTTPError as error:
            try:message=json.load(error).get('message','API request failed')
            except Exception:message='API request failed'
            raise RuntimeError(f'{method} {path}: HTTP {error.code}: {message}') from None

def provision(username,name,role,password):
    with get_session_factory()() as db:
        user=db.scalar(select(User).where(User.external_id==username))
        if user:
            credential=db.get(AccountCredential,user.id)
            if user.role!=role or not credential or not verify_password(password,credential.password_hash):
                raise RuntimeError(f'Existing fixture account {username} does not match the role/password. It was not changed.')
        else:
            user=User(external_id=username,display_name=name,role=role);db.add(user);db.flush()
            db.add(AccountCredential(user_id=user.id,password_hash=hash_password(password)));db.commit()
    return create_access_token(username,minutes=60)

def seed(api,fixture,password):
    if fixture.get('synthetic') is not True:raise ValueError('Fixture must explicitly declare synthetic=true.')
    staff=provision(fixture['staff_username'],'Synthetic Portal Reviewer','reviewer',password)
    report={'synthetic':True,'users':[],'staff_username':fixture['staff_username']}
    for info in fixture['users']:
        if not info['username'].startswith('synthetic-portal-'):raise ValueError('Fixture usernames must begin synthetic-portal-.')
        token=provision(info['username'],info['display_name'],'user',password)
        call=lambda method,path,payload=None:api.call(method,path,payload,token)
        profile=call('GET','/api/user/profile')
        if profile['revision']==0:
            call('PUT','/api/user/profile',{'display_name':info['display_name'],'community_name':'Synthetic test community','public_label':info['public_label'],'village':'Synthetic Salem locality','livelihood':'Synthetic farming','share_label':True,'expected_revision':0})
        plots=call('GET','/api/user/plots')['items'];plot=next((p for p in plots if p['name']==info['plot_name']),None)
        if not plot:plot=call('POST','/api/user/plots',{'name':info['plot_name'],'village':'Synthetic Salem locality','coordinates':info['coordinates']})
        submission=call('POST',f"/api/user/plots/{plot['id']}/submit",{'expected_revision':plot['revision']})
        if info['approve'] and submission['status'] in {'submitted','under_review'}:
            submission=api.call('POST',f"/api/staff/portal/submissions/{submission['id']}/review",{'status':'approved','note':'SYNTHETIC fixture approval for workflow testing only.','expected_revision':submission['revision']},staff)
        if info['approve']:
            resources=api.call('GET','/api/staff/portal/resources',token=staff)['items']
            if not any(r['plot_id']==plot['id'] and r['name']=='Synthetic water resource' for r in resources):
                api.call('POST','/api/staff/portal/resources',{'plot_id':plot['id'],'name':'Synthetic water resource','description':'TEST DATA: Sample resource observation for the owner dashboard.','source':'Synthetic fixture survey','synthetic':True,'verification_state':'verified'},staff)
        tickets=call('GET','/api/user/tickets')['items'];subject='Synthetic assistance request'
        ticket=next((t for t in tickets if t['subject']==subject),None)
        if ticket is None:ticket=call('POST','/api/user/tickets',{'category':'general','subject':subject,'message':'TEST DATA: Please help me understand my plot review.','plot_id':plot['id']})
        if ticket['latest_reply_role']=='user':
            api.call('POST',f"/api/staff/portal/tickets/{ticket['id']}/messages",{'message':'TEST DATA: Staff have received your request.','status':'awaiting_user','expected_revision':ticket['revision']},staff)
        report['users'].append({'username':info['username'],'plot_id':plot['id'],'submission_status':submission['status'],'dashboard_counts':call('GET','/api/user/dashboard')['counts']})
    for publication in fixture['publications']:
        existing=api.call('GET','/api/staff/portal/publications',token=staff)['items']
        if not any(p['title']==publication['title'] for p in existing):api.call('POST','/api/staff/portal/publications',publication,staff)
    report['shared_atlas_features']=len(api.call('GET','/api/user/atlas',token=staff)['features'])
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base-url',default='http://localhost:8000');parser.add_argument('--fixture',default='data/synthetic_community_portal.json');parser.add_argument('--confirm-synthetic',action='store_true');args=parser.parse_args()
    if not args.confirm_synthetic:parser.error('Pass --confirm-synthetic to add labelled synthetic users and records.')
    password=os.environ.get('PORTAL_SEED_PASSWORD') or getpass.getpass('Password for new synthetic accounts (10–128 characters): ')
    hash_password(password) # Validate before making any changes.
    result=seed(API(args.base_url),json.loads(Path(args.fixture).read_text()),password)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
