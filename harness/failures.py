"""Translate provider failure chains into fixed, public-safe messages."""

GENERIC = 'This turn could not finish. You can retry it.'
QUOTA = 'Chat is temporarily unavailable because the AI service has reached its usage limit. Please try again later.'


def public_failure(failure):
    # Never return provider text, tracebacks, request bodies or credentials.
    rate_limited = False
    for _ in range(12):
        if failure is None:break
        kind = failure.application_failure_info.type.lower()
        message = failure.message.lower()
        if 'ratelimit' in kind or 'quota' in kind:
            rate_limited = True
            if any(code in message for code in ('credit_balance_exhausted','insufficient_quota','no credits remaining','exceeded your current quota')):
                return QUOTA
        if 'authenticationerror' in kind:
            return 'Chat is temporarily unavailable because the AI service could not authenticate. Please try again later.'
        failure = failure.cause if failure.HasField('cause') else None
    if rate_limited:return 'The AI service is busy. Please try again shortly.'
    return GENERIC
