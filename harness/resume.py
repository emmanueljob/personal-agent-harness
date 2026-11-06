"""One explicitly published PDF, loaded from owner data rather than source."""
MAX_BYTES = 20 * 1024 * 1024


def valid_pdf(body):
    return len(body) <= MAX_BYTES and body.startswith(b'%PDF-') and b'%%EOF' in body[-4096:]


def _resume_bytes(brain):
    try:
        scope = brain.scope('public')
        path = scope / 'resume.pdf'
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(scope):
            return None
        if path.stat().st_size > MAX_BYTES:
            return None
        with path.open('rb') as source:
            body = source.read(MAX_BYTES + 1)
        if not valid_pdf(body):
            return None
        return body
    except (OSError, ValueError):
        return None


def resume_info(brain):
    from .resume_data import load_resume_data
    import hashlib
    try:
        data=load_resume_data(brain)
    except (OSError,ValueError):
        return {'available':False,'url':'','type':'','html_url':'','pdf_url':''}
    body=_resume_bytes(brain)
    enabled=data is None or data.enabled
    pdf=enabled and (data is None or data.formats.pdf) and body is not None
    html=bool(enabled and data is not None and data.formats.html)
    # A new PDF must not silently leave an HTML version derived from an older file.
    if html and data.source_pdf_sha256:
        html=body is not None and hashlib.sha256(body).hexdigest()==data.source_pdf_sha256
    html_url='/resume' if html else ''
    pdf_url='/resume.pdf' if pdf else ''
    preferred=html_url if data is not None and data.preferred_format=='html' else pdf_url
    url=preferred or html_url or pdf_url
    return {'available':bool(url),'url':url,'type':'text/html' if url=='/resume' else 'application/pdf' if url else '',
            'html_url':html_url,'pdf_url':pdf_url}


def published_resume(brain):
    return _resume_bytes(brain) if resume_info(brain)['pdf_url'] else None
