"""One explicitly published PDF, loaded from owner data rather than source."""
MAX_BYTES = 20 * 1024 * 1024


def valid_pdf(body):
    return len(body) <= MAX_BYTES and body.startswith(b'%PDF-') and b'%%EOF' in body[-4096:]


def published_resume(brain):
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
    body = published_resume(brain)
    return {'available': body is not None, 'url': '/resume.pdf' if body is not None else '', 'type': 'application/pdf'}
