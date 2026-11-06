"""Typed owner data for the optional HTML resume; never executable markup."""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,field_validator


class TextModel(BaseModel):
    model_config=ConfigDict(extra='forbid',str_max_length=8000)


class ResumeLink(TextModel):
    label: str = Field(min_length=1,max_length=200)
    url: str

    @field_validator('url')
    @classmethod
    def safe_url(cls,value):
        import re
        p=urlsplit(value)
        https=p.scheme=='https' and p.hostname and not p.username and not p.password
        mail=bool(re.fullmatch(r'mailto:[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',value))
        phone=bool(re.fullmatch(r'tel:\+?[0-9(). -]{5,30}',value))
        if any(ord(c)<32 for c in value) or not (https or mail or phone):
            raise ValueError('Resume links must be HTTPS, email or telephone links')
        return value


class ResumeBullet(TextModel):
    text: str
    children: list[str] = Field(default_factory=list,max_length=12)
    detail: str = ''


class ResumeEntry(TextModel):
    title: str = Field(max_length=300)
    organization: str = Field(default='',max_length=300)
    location: str = Field(default='',max_length=200)
    dates: str = Field(default='',max_length=150)
    summary: str = ''
    bullets: list[ResumeBullet] = Field(default_factory=list,max_length=40)
    links: list[ResumeLink] = Field(default_factory=list,max_length=12)


class ResumeSection(TextModel):
    title: str = Field(min_length=1,max_length=150)
    entries: list[ResumeEntry] = Field(default_factory=list,max_length=60)


class ResumeFormats(TextModel):
    html: bool = True
    pdf: bool = True


class ResumeData(TextModel):
    version: Literal[1] = 1
    enabled: bool = True
    preferred_format: Literal['html','pdf'] = 'html'
    formats: ResumeFormats = Field(default_factory=ResumeFormats)
    name: str = Field(min_length=1,max_length=200)
    headline: str = Field(default='',max_length=500)
    summary: str = ''
    contacts: list[ResumeLink] = Field(default_factory=list,max_length=12)
    sections: list[ResumeSection] = Field(default_factory=list,max_length=20)
    source_pdf_sha256: str = Field(default='',pattern=r'^(|[0-9a-f]{64})$')


def load_resume_data(brain):
    scope=brain.scope('public');path=scope/'resume.json'
    if path.is_symlink() or not path.resolve().is_relative_to(scope):raise ValueError('Unsafe resume data path')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>256000:raise ValueError('Invalid resume data file')
    return ResumeData.model_validate_json(path.read_bytes())
