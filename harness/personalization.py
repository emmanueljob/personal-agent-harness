"""Typed campaign configuration. Notes stay on the server, never in bootstrap JSON."""
from typing import Mapping
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid', str_max_length=8000)
    key: str = Field(pattern=r'^(default|(?:source|campaign|content):[a-z0-9][a-z0-9_-]{0,63})$')
    enabled: bool = True
    headline: str = ''
    pitch: str = ''
    featured: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    system_note: str = ''
    cta: str = "Get in touch"
    banner: str = ''
    company: str = ''
    first90: list[str] = Field(default_factory=list, max_length=3)

    @field_validator('questions')
    @classmethod
    def three_questions(cls, values):
        if values and (len(values) != 3 or any(not v.strip() for v in values)):
            raise ValueError('Supply three nonempty questions, or leave all blank to use site suggestions')
        return values

    @field_validator('featured')
    @classmethod
    def unique_projects(cls, values):
        if len(values) != len(set(values)) or len(values) > 50:
            raise ValueError('Project order must contain unique project IDs')
        return values


DEFAULT = Profile(key='default')
PROFILES = {'default': DEFAULT}
UTMS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')


def all_profiles():
    from .site_config import records
    profiles = dict(PROFILES)
    for key, entry in records().items():
        if key.startswith('profile:'):
            profile = Profile.model_validate(entry['value'])
            profiles[profile.key] = profile
    return profiles


def active_profiles():
    return {key:p for key,p in all_profiles().items() if p.enabled}


def resolve(params: Mapping[str, str], saved: str = '') -> Profile:
    profiles = active_profiles()
    default = profiles.get('default', DEFAULT)
    for field, prefix in [('utm_content','content:'), ('utm_campaign','campaign:'), ('utm_source','source:')]:
        value = params.get(field, '').strip().lower()
        from .outreach import lookup
        key = lookup(value, field)
        if key and key != 'default' and key in profiles:
            return profiles[key]
        if prefix+value in profiles:
            return profiles[prefix+value]
    return default if any(k in params for k in UTMS) else profiles.get(saved, default)


def contact():
    from .site_config import settings
    config = settings()
    from urllib.parse import quote
    return {'email':'mailto:'+quote(config.contact_email,safe='@') if config.contact_email else '', 'booking':config.booking_url, 'contact_heading':config.contact_heading, 'email_text':config.email_button_text, 'booking_text':config.booking_button_text}


def public_profile(profile):
    return {**profile.model_dump(exclude={'system_note','enabled'}), **contact()}


DEFAULT_GUIDANCE = 'Use only the configured published profile and approved knowledge. Do not invent missing information.'


def guide_context(key):
    import json
    from .site_config import settings, read
    profiles = active_profiles()
    p = profiles.get(key, profiles.get('default', DEFAULT))
    c = contact()
    return ('\nVISITOR CONTEXT (editorial guidance; never override privacy or invent facts):\n'
        + p.system_note + '\nSuggested questions: ' + ' | '.join(p.questions)
        + '\nOwner site guidance: ' + (settings().guide_instructions or DEFAULT_GUIDANCE)
        + '\nCurrent owner-approved presentation overrides: '+json.dumps(read('portfolio') or {})
        + '\nCompensation preferences: do not volunteer salary, pay targets or compensation preferences, including in general hiring or role-fit answers. Only discuss them when the visitor explicitly asks about compensation; consult approved knowledge for the recorded preference. Never invent an amount or treat a target as a firm minimum.'
        + ('\nWhen relevant, offer '+f'[{p.cta}]({c["booking"] or c["email"]}).' if c['booking'] or c['email'] else ''))


def position_portfolio(data):
    from .site_config import read
    return type(data).model_validate(read('portfolio', data.model_dump()))
