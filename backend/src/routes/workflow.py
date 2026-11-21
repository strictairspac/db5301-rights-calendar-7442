from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import text

from src.config.settings import cache, engine
from src.controllers.workflow import download
from src.models.tables import audit, cases, evidence, runs
from src.schemas.workflow import (BookingForm, CaseForm, DraftForm, LoginForm,
                                  ReviewForm)
from src.services import domain, programme, workflow
from src.services.security import current_user, login, operator, reviewer

router = APIRouter()


@router.get('/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1')).scalar_one()
    cache.ping()
    return {'ok': True}


@router.post('/login')
def sign_in(form: LoginForm):
    return login(form.username, form.password)


@router.get('/spec')
def spec(_=Depends(current_user)):
    return {**domain.SPEC, 'programme': domain.PROGRAMME}


@router.get('/cases')
def list_cases(_=Depends(current_user)):
    return workflow.recent(cases)


@router.post('/cases', status_code=201)
def create_case(form: CaseForm, actor=Depends(operator)):
    return workflow.make_case(form.title, form.payload, actor)


@router.get('/runs')
def list_runs(_=Depends(current_user)):
    return workflow.recent(runs)


@router.post('/cases/{case_id}/runs', status_code=202)
def start_run(case_id: int, actor=Depends(operator)):
    return workflow.enqueue(case_id, actor)


@router.get('/evidence')
def list_evidence(_=Depends(current_user)):
    return workflow.recent(evidence)


@router.get('/evidence/{identity}/download')
def export_evidence(identity: int, _=Depends(current_user)):
    return download(identity)


@router.post('/evidence/{identity}/review')
def review_evidence(identity: int, form: ReviewForm, actor=Depends(reviewer)):
    return workflow.review(identity, form.note, actor)


@router.get('/audit')
def list_audit(_=Depends(current_user)):
    return workflow.recent(audit)


# ---- auditorium programme switching ----------------------------------------


@router.get('/auditoriums')
def list_auditoriums(_=Depends(current_user)):
    return programme.listing()


@router.get('/auditoriums/{identity}')
def auditorium_detail(identity: int, _=Depends(current_user)):
    return programme.detail(identity)


@router.post('/auditoriums/{identity}/preview')
def preview_programme(identity: int, form: DraftForm, _=Depends(current_user)):
    return programme.preview(identity, form.draft)


@router.post('/auditoriums/{identity}/bookings', status_code=201)
def create_booking(identity: int, form: BookingForm, actor=Depends(operator),
                   idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    return programme.create_booking(identity, form.model_dump(), actor, idempotency_key)


@router.post('/auditoriums/{identity}/bookings/{booking_id}/cancel')
def cancel_booking(identity: int, booking_id: int, actor=Depends(operator)):
    return programme.cancel_booking(identity, booking_id, actor)


@router.post('/auditoriums/{identity}/publish', status_code=201)
def publish_programme(identity: int, form: DraftForm, response: Response,
                      actor=Depends(operator),
                      idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    result = programme.publish(identity, form.draft, actor, idempotency_key)
    if result.get('replayed'):
        response.status_code = 200
    return result
