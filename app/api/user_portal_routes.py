"""Owner-scoped plot persistence and individual browser authentication."""
import math
import re
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.validation import explain_validity
from app.api.auth import SESSION_COOKIE, AuthenticatedUser, create_access_token, get_current_user
from app.config import get_settings
from app.db.models import User, utcnow
from app.db.user_plot_models import AccountCredential, UserPlot
from app.db.session import get_db
from app.services.account_passwords import hash_password, verify_password
from app.services.audit import record_audit

router = APIRouter(tags=['User portal'])

class LoginInput(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=128)
    @field_validator('username')
    @classmethod
    def username_key(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,79}', value):
            raise ValueError('Use 3–80 letters, numbers, dots, hyphens or underscores.')
        return value

class RegisterInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=120)
    @field_validator('display_name')
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError('Enter your name.')
        return value.strip()

# Evaluate an equally expensive password hash even when the account is unknown.
DUMMY_HASH = hash_password('unavailable-account-password')

def browser_session(user):
    settings = get_settings()
    response = JSONResponse({'external_id': user.external_id, 'display_name': user.display_name,
                             'role': user.role, 'redirect': '/my-plots' if user.role == 'user' else '/fra'})
    response.set_cookie(SESSION_COOKIE, create_access_token(user.external_id, minutes=settings.demo_session_minutes),
                        max_age=settings.demo_session_minutes * 60, httponly=True,
                        secure=settings.environment.casefold() == 'production', samesite='strict', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return response

@router.post('/api/auth/register', status_code=201)
def register(payload: RegisterInput, db: Session = Depends(get_db)):
    if not get_settings().local_auth_enabled:
        raise HTTPException(403, 'Use the configured single sign-on service.')
    try:
        encoded = hash_password(payload.password)
    except ValueError as error:
        raise HTTPException(422, str(error))
    user = User(external_id=payload.username, display_name=payload.display_name, role='user')
    try:
        db.add(user); db.flush()
        db.add(AccountCredential(user_id=user.id, password_hash=encoded)); db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'This username is unavailable. Choose another or sign in.')
    response = browser_session(user); response.status_code = 201
    return response

@router.post('/api/auth/login')
def login(payload: LoginInput, db: Session = Depends(get_db)):
    if not get_settings().local_auth_enabled:
        raise HTTPException(403, 'Use the configured single sign-on service.')
    user = db.scalar(select(User).where(User.external_id == payload.username))
    credential = db.get(AccountCredential, user.id) if user else None
    correct = verify_password(payload.password, credential.password_hash if credential else DUMMY_HASH)
    if not correct or not credential or user.role not in {'user', 'reviewer', 'admin'}:
        raise HTTPException(401, 'Username or password is incorrect.')
    return browser_session(user)

class PlotInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    village: str = Field(default='', max_length=120)
    coordinates: list[list[float]] = Field(min_length=3, max_length=500)
    expected_revision: int | None = Field(default=None, ge=1)
    @field_validator('name')
    @classmethod
    def plot_name(cls, value):
        if not value.strip():
            raise ValueError('Give this plot a name.')
        return value.strip()

def validated_boundary(points):
    if any(len(p) != 2 or not all(math.isfinite(v) for v in p) or not (-180 <= p[0] <= 180 and -85 <= p[1] <= 85) for p in points):
        raise HTTPException(422, 'Each point must contain valid longitude and latitude.')
    # GeoJSON uses longitude, latitude. A closed ring is persisted even when the client omits its last point.
    ring = list(points)
    if ring[0] == ring[-1]: ring.pop()
    if len({tuple(p) for p in ring}) < 3 or len({tuple(p) for p in ring}) != len(ring):
        raise HTTPException(422, 'Choose at least three distinct corners without repeated points.')
    polygon = Polygon(ring)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise HTTPException(422, f'The boundary crosses itself or has no area. {explain_validity(polygon)}')
    if polygon.bounds[2] - polygon.bounds[0] >= 180:
        raise HTTPException(422, 'A plot cannot cross the international date line.')
    # Spherical area in square metres, consistent with the map preview.
    radians = math.radians
    closed = ring + [ring[0]]
    area = abs(sum(radians(b[0] - a[0]) * (2 + math.sin(radians(a[1])) + math.sin(radians(b[1])))
                   for a, b in zip(closed, closed[1:]))) * 6378137**2 / 2
    if area < 1 or area > 100_000_000:
        raise HTTPException(422, 'Map a plot between 1 m² and 100 km². Zoom in and check your corners.')
    return dict(mapping(MultiPolygon([polygon]))), round(area, 3)

def plot_json(plot):
    return {'id': str(plot.id), 'name': plot.name, 'village': plot.village,
            'geometry': plot.geometry, 'area_sqm': float(plot.area_sqm), 'revision': plot.revision,
            'created_at': plot.created_at.isoformat(), 'updated_at': plot.updated_at.isoformat(),
            'imagery_status': 'not_requested', 'model_status': 'not_connected'}

def own_plot(db, identifier, user):
    plot = db.scalar(select(UserPlot).where(UserPlot.id == identifier, UserPlot.owner_id == user.id))
    if plot is None: raise HTTPException(404, 'Plot not found.')
    return plot

@router.get('/api/user/plots')
def list_plots(user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return {'items': [plot_json(p) for p in db.scalars(select(UserPlot).where(UserPlot.owner_id == user.id).order_by(UserPlot.updated_at.desc()))]}

@router.get('/api/user/plots/{plot_id}')
def get_plot(plot_id: uuid.UUID, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return plot_json(own_plot(db, plot_id, user))

@router.post('/api/user/plots', status_code=201)
def create_plot(payload: PlotInput, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    geometry, area = validated_boundary(payload.coordinates)
    plot = UserPlot(owner_id=user.id, name=payload.name, village=payload.village.strip(), geometry=geometry, area_sqm=area)
    db.add(plot); db.flush()
    record_audit(db, actor_id=user.id, action='user_plot_created', entity_type='user_plot', entity_id=plot.id, after={'revision': 1})
    db.commit()
    return plot_json(plot)

@router.put('/api/user/plots/{plot_id}')
def update_plot(plot_id: uuid.UUID, payload: PlotInput, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    own_plot(db, plot_id, user)
    if payload.expected_revision is None: raise HTTPException(422, 'The saved plot revision is required.')
    geometry, area = validated_boundary(payload.coordinates)
    result = db.execute(update(UserPlot).where(UserPlot.id == plot_id, UserPlot.owner_id == user.id,
        UserPlot.revision == payload.expected_revision).values(name=payload.name, village=payload.village.strip(),
        geometry=geometry, area_sqm=area, revision=UserPlot.revision + 1, updated_at=utcnow()))
    if result.rowcount != 1:
        db.rollback(); raise HTTPException(409, 'This plot changed in another window. Reopen it before editing.')
    record_audit(db, actor_id=user.id, action='user_plot_updated', entity_type='user_plot', entity_id=plot_id,
                 after={'revision': payload.expected_revision + 1})
    db.commit(); db.expire_all()
    return plot_json(own_plot(db, plot_id, user))
