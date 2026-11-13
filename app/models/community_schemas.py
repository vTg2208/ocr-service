"""Versioned API input contracts for community portal workflows."""
from datetime import datetime
from typing import Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class ProfileInput(Input):
    display_name: str = Field(min_length=1,max_length=120)
    community_name: str = Field(default='',max_length=120)
    public_label: str = Field(default='',max_length=120)
    village: str = Field(default='',max_length=120)
    phone: str = Field(default='',max_length=30)
    email: str = Field(default='',max_length=254)
    @field_validator('email')
    @classmethod
    def email_address(cls,value):
        import re
        value=value.lower()
        if value and not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",value):
            raise ValueError('Enter a valid email address.')
        if '..' in value: raise ValueError('Enter a valid email address.')
        return value
    livelihood: str = Field(default='',max_length=120)
    share_label: bool = False
    expected_revision: int = Field(ge=0)

class RevisionInput(Input):
    expected_revision: int = Field(ge=1)

class ReviewInput(RevisionInput):
    status: Literal['under_review','approved','changes_requested','rejected']
    note: str = Field(min_length=1,max_length=4000)

class TicketInput(Input):
    category: Literal['boundary_dispute','scheme_assistance','technical','general','plot_mapping']
    subject: str = Field(min_length=3,max_length=180)
    message: str = Field(min_length=1,max_length=6000)
    plot_id: uuid.UUID | None = None
    scheme_id: uuid.UUID | None = None

class MessageInput(RevisionInput):
    message: str = Field(min_length=1,max_length=6000)
    status: Literal['open','in_progress','awaiting_user','resolved','closed'] | None = None

class PublicationInput(Input):
    kind: Literal['announcement','scheme']
    title: str = Field(min_length=3,max_length=180)
    body: str = Field(min_length=1,max_length=10000)
    source_url: str = Field(default='',max_length=1000)
    district: str = Field(default='',max_length=120)
    status: Literal['draft','published','archived'] = 'draft'
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    synthetic: bool = False
    @field_validator('source_url')
    @classmethod
    def safe_source(cls, value):
        from urllib.parse import urlsplit
        if value:
            parsed=urlsplit(value)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Source links must use HTTPS.')
        return value
    @model_validator(mode='after')
    def dates(self):
        for value in [self.starts_at,self.ends_at]:
            if value and value.tzinfo is None: raise ValueError('Use ISO timestamps with a timezone.')
        if self.starts_at and self.ends_at and self.starts_at>=self.ends_at: raise ValueError('End time must follow start time.')
        if self.kind=='scheme' and self.status=='published' and not self.synthetic and not self.source_url:
            raise ValueError('Published schemes need a source link.')
        return self

class PublicationUpdate(PublicationInput):
    expected_revision: int = Field(ge=1)

class ResourceInput(Input):
    plot_id: uuid.UUID
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(min_length=1,max_length=4000)
    source: str = Field(min_length=1,max_length=300)
    verification_state: Literal['unverified','verified','rejected'] = 'unverified'
    synthetic: bool = False

class ResourceUpdate(Input):
    verification_state: Literal['unverified','verified','rejected']

class ItemsResponse(BaseModel):
    items: list[dict]
    contract_version: str = 'community-portal-v1'

class AtlasResponse(BaseModel):
    type: Literal['FeatureCollection'] = 'FeatureCollection'
    features: list[dict]
    contract_version: str = 'community-portal-v1'
    boundary_meaning: str = 'Staff-approved community mapping; not an official FRA title.'

class DashboardResponse(BaseModel):
    contract_version: str = 'community-portal-v1'
    generated_at: str
    profile: dict
    counts: dict[str,int]
    announcements: list[dict]
    schemes: list[dict]
    resources: list[dict]
    submissions: list[dict]
    tickets: list[dict]
    plot_workflows: list[dict] = Field(default_factory=list)

class OTPRequest(Input):
    channel: Literal['email','phone']

class OTPVerify(OTPRequest):
    challenge_id: uuid.UUID
    code: str = Field(pattern=r'^\d{6}$')
