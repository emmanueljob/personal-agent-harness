"""Cookieless Plausible event relay. Never forward or store visitor IPs."""
import os
from urllib.parse import urlsplit, urlunsplit
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, Response, JSONResponse
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
from .personalization import PROFILES, UTMS

router = APIRouter()


class VisitEvent(BaseModel):
    name: Literal['pageview', 'profile_resolved', 'cta_click', 'guide_question_asked']
    url: str = Field(max_length=3000)
    profile_key: str = Field(default='default', max_length=80)
    referrer: str = Field(default='', max_length=2000)
    utm: dict[str, str] = Field(default_factory=dict, max_length=5)
    target: Literal['email', 'booking', ''] = ''


def referrer_origin(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, '', '', '')) if parsed.scheme in ('http','https') else ''


async def deliver(event, origin):
    domain = os.getenv('PLAUSIBLE_DOMAIN', '')
    if not domain:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            # Fixed upstream, no client-selected URLs, no IP headers or identifiers.
            await client.post('https://plausible.io/api/event', json={
                'name': event.name, 'domain': domain, 'url': origin+event.url,
                'referrer': referrer_origin(event.referrer),
                'props': {'profile_key': event.profile_key, **{k:v[:160] for k,v in event.utm.items() if k in UTMS}, 'target': event.target},
            }, headers={'User-Agent':'PortfolioAnalytics/1.0'})
    except httpx.HTTPError:
        pass  # Analytics must never affect navigation or guide turns.


@router.get('/visit.js', include_in_schema=False)
def visit_script():
    return FileResponse(Path(__file__).with_name('visit.js'), media_type='application/javascript')


@router.get('/api/visit-config', include_in_schema=False)
def visit_config():
    return JSONResponse({'linkedin': os.getenv('LINKEDIN_PARTNER_ID',''), 'x': os.getenv('X_PIXEL_ID','')}, headers={'Cache-Control':'no-store'})


@router.post('/api/visit-events', status_code=204, include_in_schema=False)
def visit_event(event: VisitEvent, request: Request, tasks: BackgroundTasks):
    if not event.url.startswith('/') or event.url.startswith('//'):
        raise HTTPException(422, 'Expected a relative page URL')
    if event.profile_key not in PROFILES:
        event.profile_key = 'default'
    from .public_access import consume
    # Global bounded admission, with no IP or persistent visitor identifiers.
    consume(request.app.state.public_store, [('hour:analytics', 3600, 5000)])
    # Bounded event payload, no freeform questions and no IP storage.
    from .outreach import record_event
    import sqlite3
    try:
        record_event(event)
    except (OSError, sqlite3.Error):
        pass  # Tracking failures must not break navigation.
    origin = os.getenv('PUBLIC_ORIGIN', str(request.base_url)).rstrip('/')
    tasks.add_task(deliver, event, origin)
    return Response(status_code=204)


class OutreachInput(BaseModel):
    person: str = Field(default='', max_length=160)
    company: str = Field(default='', max_length=160)
    source: str = Field(default='outreach', max_length=160)


@router.post('/api/outreach-links', include_in_schema=False)
def create_outreach_link(body: OutreachInput, request: Request):
    authenticate_link_builder(request)
    from .outreach import build_link
    origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    try:
        url = build_link(origin, body.person, body.company, body.source)
    except ValueError:
        raise HTTPException(503, 'Configure an HTTPS public origin and private outreach database')
    return JSONResponse({'url':url}, headers={'Cache-Control':'no-store'})


def authenticate_link_builder(request):
    import secrets
    password = os.getenv('PORTFOLIO_LINKS_PASSWORD', '')
    if not password:
        raise HTTPException(404)
    from .public_access import consume
    consume(request.app.state.public_store, [('hour:link-builder', 3600, 120)])
    if not secrets.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer '+password).encode()):
        raise HTTPException(401, 'Link builder authentication required')


@router.get('/api/outreach-stats', include_in_schema=False)
def outreach_stats(request: Request):
    authenticate_link_builder(request)
    from .outreach import report
    return JSONResponse(report(), headers={'Cache-Control':'no-store'})
