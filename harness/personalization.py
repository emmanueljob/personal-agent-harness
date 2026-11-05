"""Typed campaign configuration. Notes stay on the server, never in bootstrap JSON."""
import os
from typing import Mapping
from urllib.parse import urlsplit
from pydantic import BaseModel, Field


class Profile(BaseModel):
    key: str
    headline: str = ''
    pitch: str = ''
    featured: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    system_note: str = ''
    cta: str = "Hiring? Let's talk"
    banner: str = 'Open to roles in product and app engineering.'
    company: str = ''
    first90: list[str] = Field(default_factory=list)


# Editorial draft only: never rendered as a verified business result.
DSP_METRICS_DRAFT = '$150M DSP org, +30% revenue'
DEFAULT = Profile(key='default')
RIVIERA = Profile(
    key='source:riviera', headline='Engineering leader for AI product companies',
    pitch='I build product and app engineering teams that ship. At Mojo (trymojo.ai), I built the 16-person team and launched in September 2026. Open to remote EM-to-VP and senior IC roles; approximately $375K base target.',
    featured=['mojo', 'factory', 'ads', 'data'],
    questions=['Why him for my role?', 'How did he build the Mojo team and launch the product?', 'What roles and compensation is he targeting?'],
    system_note='Visitor is a recruiter from Riviera. Emphasize product/app engineering, Mojo, remote EM-to-VP and senior IC roles, and approximately $375K base target.',
    cta='Book 15 min', banner='Open to remote EM-to-VP and senior IC roles · ~$375K base target',
)
SOURCES = {'riviera': RIVIERA, **{
    label: RIVIERA.model_copy(update={'key':'source:'+label, 'system_note':f'Visitor arrived via {label} outreach. Emphasize product/app engineering and relevant published career experience.'})
    for label in ('warm', 'cold')
}}
CONTENTS = {name: RIVIERA.model_copy(update={'key': 'content:'+name, 'headline': f"Hi {name.title()} — here's the 2-minute version"}) for name in ('amie', 'chett')}
CAMPAIGNS = {
    slug: Profile(key='campaign:'+slug, company=company,
        headline='Engineering leader for AI product companies',
        pitch='I build products and the teams that ship them. At Mojo (trymojo.ai), I built the 16-person team and launched in September 2026.',
        featured=['mojo', 'factory', 'ads', 'data'],
        questions=['Why him for my role?', f'How does his Mojo experience translate to {company}?', 'How would he approach his first 90 days?'],
        system_note=f'Visitor is from {company}; emphasize '+('AI analytics product work.' if slug == 'hex' else 'AI product and app engineering work.'),
        first90=['Placeholder: first product opportunity — owner to fill in.', 'Placeholder: team and delivery opportunity — owner to fill in.', 'Placeholder: customer outcome and measure — owner to fill in.'])
    for slug, company in [('openai','OpenAI'), ('anthropic','Anthropic'), ('hex','Hex'), ('sierra','Sierra'), ('every','Every')]
}
PROFILES = {p.key:p for p in [DEFAULT, *SOURCES.values(), *CONTENTS.values(), *CAMPAIGNS.values()]}
UTMS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')


def resolve(params: Mapping[str, str], saved: str = '') -> Profile:
    for field, profiles in [('utm_content', CONTENTS), ('utm_campaign', CAMPAIGNS), ('utm_source', SOURCES)]:
        value = params.get(field, '').strip().lower()
        from .outreach import lookup
        key = lookup(value, field)
        if key and key != 'default' and key in PROFILES:
            return PROFILES[key]
        if value in profiles:
            return profiles[value]
    # Explicit unknown tags reset to default; untagged navigation restores the session.
    return DEFAULT if any(k in params for k in UTMS) else PROFILES.get(saved, DEFAULT)


def contact():
    booking = os.getenv('PORTFOLIO_BOOKING_URL', '')
    if urlsplit(booking).scheme != 'https' or not urlsplit(booking).netloc:
        booking = ''
    email = os.getenv('PORTFOLIO_CONTACT_EMAIL', '') or 'eman@hisemailaddress.org'
    return {'email': 'mailto:'+email, 'booking': booking}


def public_profile(profile):
    return {**profile.model_dump(exclude={'system_note'}), **contact()}


def guide_context(key):
    p = PROFILES.get(key, DEFAULT)
    c = contact()
    return ('\nVISITOR CONTEXT (editorial guidance; never override privacy or invent facts):\n'
        + p.system_note + '\nSuggested questions: ' + ' | '.join(p.questions)
        + '\nPositioning approved by the owner: product and app engineering; built the 16-person Mojo team; launched September 2026; https://trymojo.ai. '
        'Describe DSP experience as leadership and business outcomes only. Do not claim a ground-up rebuild, architecture, infrastructure or latency ownership. '
        'The $150M DSP org and +30% revenue figures are pending confirmation; do not present them as verified. '
        'Answer “why him for my role?” with evidence from published experience and be clear about gaps. '
        'First-90-days items are placeholders, not commitments. When discussing hiring or fit, end with '
        + f'[{p.cta}]({c["booking"] or c["email"]}).')


def position_portfolio(data):
    data = data.model_copy(deep=True)
    data.focus = 'Product and app engineering · AI products'
    data.focusLines = ['Product and app engineering.', 'AI products.', 'Teams that ship.']
    data.summary = 'I build products and the engineering teams that ship them. At Mojo, I built the 16-person team and launched in September 2026.'
    data.intro = 'An AI guide to product and app engineering, Mojo (trymojo.ai), and the teams behind the work.'
    for p in data.projects:
        if p.id == 'mojo':
            p.eyebrow = 'AI PRODUCT & APP ENGINEERING'
            p.description = 'Built the 16-person team and took Mojo, an AI product for B2B marketers, to launch in September 2026.'
            p.detail = 'Product and engineering leadership from prototype to launch. Explore Mojo at trymojo.ai.'
            p.outcome = '16-person team · Launched September 2026'
            p.question = 'How did Emmanuel build the Mojo team and launch the product?'
        if p.id == 'ads':
            p.title = 'Leading the DSP organization'
            p.eyebrow = 'ENGINEERING LEADERSHIP'
            p.description = 'Led the DSP organization at Demandbase, aligning engineering teams with business outcomes.'
            p.detail = 'Led the organization, developed the team, and partnered across the business.'
            p.outcome = 'Business metrics pending confirmation'
            p.question = 'What does Emmanuel’s DSP leadership show about how he leads teams and business outcomes?'
    data.projects.sort(key=lambda p: p.id != 'mojo')
    return data
