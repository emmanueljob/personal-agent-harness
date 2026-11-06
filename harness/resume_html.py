"""Accessible HTML rendered from validated resume data, with all text escaped."""
from html import escape as e
from .resume import resume_info
from .resume_data import load_resume_data


def render_resume(brain):
    info=resume_info(brain)
    if not info['html_url']:return None
    try:data=load_resume_data(brain)
    except (OSError,ValueError):return None
    if data is None or not data.enabled or not data.formats.html:return None
    def link(item):return '<a href="'+e(item.url,quote=True)+'">'+e(item.label)+'</a>'
    navigation='<a href="/">← Portfolio</a>'
    if info['pdf_url']:navigation+='<a class="download" href="/resume.pdf">Download PDF ↓</a>'
    contacts='<ul class="contacts">'+''.join('<li>'+link(item)+'</li>' for item in data.contacts)+'</ul>'
    sections=''
    for i,section in enumerate(data.sections):
        entries=''
        for entry in section.entries:
            org=' · '.join(e(v) for v in (entry.organization,entry.location) if v)
            dates='<p class="dates">'+e(entry.dates)+'</p>' if entry.dates else ''
            bullets=''
            for bullet in entry.bullets:
                children='<ul>'+''.join('<li>'+e(c)+'</li>' for c in bullet.children)+'</ul>' if bullet.children else ''
                detail='<p>'+e(bullet.detail)+'</p>' if bullet.detail else ''
                bullets+='<li>'+e(bullet.text)+children+detail+'</li>'
            entries+='<article><header><div><h3>'+e(entry.title)+'</h3>'+('<p class="organization">'+org+'</p>' if org else '')+'</div>'+dates+'</header>'
            if entry.summary:entries+='<p>'+e(entry.summary)+'</p>'
            if bullets:entries+='<ul class="achievements">'+bullets+'</ul>'
            if entry.links:entries+='<p class="entry-links">'+' · '.join(link(item) for item in entry.links)+'</p>'
            entries+='</article>'
        sections+='<section aria-labelledby="section-'+str(i)+'"><h2 id="section-'+str(i)+'">'+e(section.title)+'</h2>'+entries+'</section>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>'+e(data.name)+' — Resume</title><meta name="description" content="'+e(data.headline,quote=True)+'">'
        '<link rel="stylesheet" href="/resume.css"></head><body><a class="skip" href="#resume">Skip to resume</a><nav aria-label="Resume actions">'+navigation+'</nav>'
        '<main id="resume"><header class="intro"><p class="eyebrow">Resume</p><h1>'+e(data.name)+'</h1><p class="headline">'+e(data.headline)+'</p>'+contacts+'</header>'
        +('<p class="summary">'+e(data.summary)+'</p>' if data.summary else '')+sections+'</main><footer>Use your browser’s print command for a printable HTML copy.</footer></body></html>')
