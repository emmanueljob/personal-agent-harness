"""Authenticated owner portal APIs. Credentials remain in deployment configuration."""
from fastapi import APIRouter, Depends, HTTPException, Request, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from . import site_config
from .personalization import Profile, all_profiles, position_portfolio, DEFAULT_GUIDANCE, resolve, public_profile
from .portfolio import Portfolio
from .outreach import build_link, report, set_link_active, set_tag_active, delete_links
from .visits import OutreachInput
import os


class SettingsEdit(BaseModel):
    revision: int = Field(ge=0)
    value: site_config.Settings


class ProfileEdit(BaseModel):
    revision: int = Field(ge=0)
    value: Profile


class PortfolioEdit(BaseModel):
    revision: int = Field(ge=0)
    value: Portfolio


class LinkState(BaseModel):
    active: bool


def response(value):
    return JSONResponse(value, headers={'Cache-Control':'private, no-store'})


def mount_admin(app, get_store, authorize):
    def portal_owner(request:Request, authorization:str=Header(default='')):
        try:
            authorize(authorization)
        except HTTPException:
            from .public_access import consume
            consume(get_store(), [('hour:admin-auth-failures',3600,120)])
            raise
    router = APIRouter(prefix='/api/admin', dependencies=[Depends(portal_owner)], include_in_schema=False)

    def current_portfolio():
        return position_portfolio(Portfolio.model_validate(get_store().brain.public_portfolio()))

    def write(key, value, revision):
        try:
            return {'revision':site_config.save(key,value,revision)}
        except site_config.Conflict as error:
            raise HTTPException(409,str(error))

    @router.get('/config')
    def config():
        data = site_config.records()
        return response({
            'profiles':[{'value':p.model_dump(),'revision':data.get('profile:'+p.key,{}).get('revision',0)} for p in all_profiles().values()],
            'settings':site_config.settings().model_dump(), 'settings_revision':data.get('settings',{}).get('revision',0),
            'portfolio':current_portfolio().model_dump(), 'portfolio_revision':data.get('portfolio',{}).get('revision',0),
            'guide_default':DEFAULT_GUIDANCE,
            'origin':os.getenv('PUBLIC_ORIGIN','').rstrip('/'),
        })

    @router.put('/settings')
    def settings(body:SettingsEdit):
        return response(write('settings',body.value.model_dump(),body.revision))

    @router.put('/profiles/{key}')
    def profile(key:str, body:ProfileEdit):
        if body.value.key != key:
            raise HTTPException(422,'Profile key must match the selected record')
        if key == 'default' and not body.value.enabled:
            raise HTTPException(422,'The default profile must remain enabled')
        ids = {p.id for p in current_portfolio().projects}
        if any(ident not in ids for ident in body.value.featured):
            raise HTTPException(422,'Project order references a project that is not in the site portfolio')
        return response(write('profile:'+key,body.value.model_dump(),body.revision))

    @router.post('/portfolio/validate')
    def validate_portfolio(body:Portfolio):
        return response(body.model_dump())

    @router.put('/portfolio')
    def portfolio(body:PortfolioEdit):
        ids = [p.id for p in body.value.projects]
        if len(ids) != len(set(ids)):
            raise HTTPException(422,'Project IDs must be unique')
        return response(write('portfolio',body.value.model_dump(),body.revision))

    @router.post('/resolve')
    def preview(body:OutreachInput):
        return response(public_profile(resolve({'utm_content':body.person,'utm_campaign':body.company,'utm_source':body.source})))

    @router.post('/links')
    def links(body:OutreachInput):
        try:
            url = build_link(os.getenv('PUBLIC_ORIGIN','').rstrip('/'),body.person,body.company,body.source)
        except ValueError as error:
            raise HTTPException(422,str(error))
        return response({'url':url})

    @router.get('/outreach')
    def outreach():
        return response(report())

    @router.put('/links/{ident}')
    def link_state(ident:str, body:LinkState):
        try:
            set_link_active(ident,body.active)
        except KeyError:
            raise HTTPException(404,'Link not found')
        return response({'active':body.active})

    @router.put('/tags/{token}')
    def tag_state(token:str, body:LinkState):
        try:
            set_tag_active(token,body.active)
        except KeyError:
            raise HTTPException(404,'Tag not found')
        return response({'active':body.active})

    @router.delete('/links')
    def clear_links(before:float|None=Query(default=None,ge=0)):
        return response(delete_links(before=before))

    @router.delete('/links/{ident}')
    def remove_link(ident:str):
        try:
            return response(delete_links(ident=ident))
        except KeyError:
            raise HTTPException(404,'Link not found')

    @router.delete('/tags/{token}')
    def remove_tag(token:str):
        try:
            return response(delete_links(token=token))
        except KeyError:
            raise HTTPException(404,'Tag not found')

    app.include_router(router)
