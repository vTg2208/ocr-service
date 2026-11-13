"""Development-only terminal delivery; persistent bounded one-time challenges."""
from datetime import timedelta
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from fastapi import HTTPException
from sqlalchemy import select, update
from app.config import get_settings
from app.db.models import utcnow
from app.db.community_models import CommunityProfile, ContactOTP
from app.services.community_portal import utc

logger=logging.getLogger('app.contact_otp')
def digest(value):
    return hmac.new(get_settings().auth_secret.encode(),value.encode(),hashlib.sha256).hexdigest()

def locked_profile(db,user):
    profile=db.scalar(select(CommunityProfile).where(CommunityProfile.user_id==user.id).with_for_update())
    if profile is None:raise HTTPException(422,'Save your profile and contact details first.')
    return profile

def request_code(db,user,channel):
    if get_settings().environment.casefold()!='development':
        raise HTTPException(503,'Terminal OTP delivery is available only in development. Configure a real delivery service for production.')
    profile=locked_profile(db,user);target=getattr(profile,channel)
    if not target:raise HTTPException(422,f'Save your {channel} before requesting a code.')
    if channel=='phone' and not re.fullmatch(r'\+?[0-9][0-9 ()-]{6,29}',target):raise HTTPException(422,'Save a valid phone number first.')
    now=utcnow()
    previous=db.scalar(select(ContactOTP).where(ContactOTP.user_id==user.id,ContactOTP.channel==channel).order_by(ContactOTP.created_at.desc()).limit(1))
    if previous and (now-utc(previous.created_at)).total_seconds()<60:raise HTTPException(429,'Wait 60 seconds before requesting another code.')
    db.execute(update(ContactOTP).where(ContactOTP.user_id==user.id,ContactOTP.channel==channel,ContactOTP.consumed_at.is_(None)).values(consumed_at=now))
    identifier=uuid.uuid4();code=f'{secrets.randbelow(1_000_000):06d}'
    challenge=ContactOTP(id=identifier,user_id=user.id,channel=channel,contact_digest=digest(target),code_digest=digest(str(identifier)+':'+code),expires_at=now+timedelta(minutes=5))
    db.add(challenge);db.commit()
    logger.warning('DEV OTP | account=%s | channel=%s | target=%s | code=%s | expires in 5 minutes',user.external_id,channel,target,code)
    return {'challenge_id':str(identifier),'channel':channel,'expires_in_seconds':300,'resend_after_seconds':60,'delivery':'development_terminal'}

def verify_code(db,user,payload):
    if get_settings().environment.casefold()!='development':
        raise HTTPException(503,'Development terminal codes cannot be verified in production.')
    profile=locked_profile(db,user)
    challenge=db.scalar(select(ContactOTP).where(ContactOTP.id==payload.challenge_id,ContactOTP.user_id==user.id,ContactOTP.channel==payload.channel).with_for_update())
    now=utcnow()
    if challenge is None or challenge.consumed_at or utc(challenge.expires_at)<=now or challenge.attempts>=5:
        raise HTTPException(422,'The code is invalid or expired. Request a new code.')
    if not hmac.compare_digest(challenge.contact_digest,digest(getattr(profile,payload.channel))):raise HTTPException(422,'Contact details changed. Request a new code.')
    challenge.attempts+=1
    if not hmac.compare_digest(challenge.code_digest,digest(str(challenge.id)+':'+payload.code)):
        if challenge.attempts>=5:challenge.consumed_at=now
        db.commit();raise HTTPException(422,'Incorrect code. Codes allow at most five attempts.')
    challenge.consumed_at=now
    setattr(profile,payload.channel+'_verified_at',now)
    setattr(profile,payload.channel+'_verification_method','development_terminal')
    profile.revision+=1;profile.updated_at=now
    db.commit()
    return {'channel':payload.channel,'verified':True,'verified_at':now.isoformat()}
