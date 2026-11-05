"""Owner-editable site configuration, separate from code and published knowledge."""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .paths import data_directory


class Conflict(ValueError):
    pass


def path():
    target = (data_directory() / 'site-config.sqlite').resolve()
    if target.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Site configuration must live outside the source repository')
    return target


def records():
    target = path()
    if not target.is_file():
        return {}
    with sqlite3.connect(target.as_uri()+'?mode=ro', uri=True, timeout=5) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='site_config'").fetchone():
            return {}
        return {key:{'value':json.loads(value),'revision':revision} for key,value,revision in db.execute('SELECT key,value,revision FROM site_config')}


def read(key, default=None):
    return records().get(key, {}).get('value', default)


def save(key, value, revision):
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(target, 0o600)
    with sqlite3.connect(target, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS site_config (key TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL,updated REAL NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT revision FROM site_config WHERE key=?',(key,)).fetchone()
        if (current[0] if current else 0) != revision:
            raise Conflict('This configuration changed in another session. Reload before saving.')
        new_revision = revision+1
        db.execute('INSERT INTO site_config VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,revision=excluded.revision,updated=excluded.updated',
                   (key,json.dumps(value),new_revision,time.time()))
    return new_revision


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contact_heading: str = Field(default="Hiring? Let's talk", min_length=1, max_length=80)
    email_button_text: str = Field(default='Email me', min_length=1, max_length=80)
    booking_button_text: str = Field(default='Book 15 min', min_length=1, max_length=80)
    contact_email: str = Field(default='', max_length=254)
    booking_url: str = Field(default='', max_length=2000)
    tracking_enabled: bool = True
    plausible_domain: str = Field(default='', max_length=253)
    linkedin_partner_id: str = Field(default='', max_length=32, pattern=r'^\d*$')
    x_pixel_id: str = Field(default='', max_length=32, pattern=r'^[a-zA-Z0-9]*$')
    guide_instructions: str = Field(default='', max_length=8000)

    @field_validator('contact_email')
    @classmethod
    def email(cls, value):
        if value and not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",value):
            raise ValueError('Enter a valid contact email address')
        return value

    @field_validator('booking_url')
    @classmethod
    def booking(cls, value):
        parsed = urlsplit(value)
        if value and (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or any(c in value for c in '\r\n<>')):
            raise ValueError('Booking URL must use HTTPS without embedded credentials')
        return value

    @field_validator('plausible_domain')
    @classmethod
    def domain(cls,value):
        if value and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?',value):
            raise ValueError('Enter a domain, not a URL')
        return value


def settings():
    defaults = Settings(contact_email=os.getenv('PORTFOLIO_CONTACT_EMAIL','') or 'eman@hisemailaddress.org',
        booking_url=os.getenv('PORTFOLIO_BOOKING_URL',''), plausible_domain=os.getenv('PLAUSIBLE_DOMAIN',''),
        linkedin_partner_id=os.getenv('LINKEDIN_PARTNER_ID',''),x_pixel_id=os.getenv('X_PIXEL_ID',''))
    return Settings.model_validate(read('settings', defaults.model_dump()))
