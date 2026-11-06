"""Generate clean outreach links with readable /hi/ paths and no query parameters."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Support `python scripts/build_campaign_link.py` without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.outreach import build_link


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--person', default='', help='Person profile selector; used as the public URL label unless --slug is supplied')
    parser.add_argument('--company', default='', help='Company profile selector; default URL label when no person is supplied')
    parser.add_argument('--source', default='outreach', help='Private source label; e.g. warm or cold')
    parser.add_argument('--slug',default='',help='Public URL label; defaults to person, company or source')
    parser.add_argument('--base', default=os.getenv('PUBLIC_ORIGIN', ''))
    parser.add_argument('--server', action='store_true', help='Register on the live site using PORTFOLIO_LINKS_PASSWORD')
    parser.add_argument('--stats', action='store_true', help='Show private labels and daily event counts')
    args = parser.parse_args()
    if args.server:
        import httpx
        origin = urlsplit(args.base)
        if origin.scheme != 'https' or not origin.netloc or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('','/'):
            parser.error('--base must be an HTTPS origin')
        password = os.getenv('PORTFOLIO_LINKS_PASSWORD', '')
        if not password:
            parser.error('Set PORTFOLIO_LINKS_PASSWORD to register on the server')
        try:
            response = httpx.request('GET' if args.stats else 'POST', args.base.rstrip('/')+('/api/outreach-stats' if args.stats else '/api/outreach-links'), json=None if args.stats else {
                'person':args.person, 'company':args.company, 'source':args.source, 'slug':args.slug,
            }, headers={'Authorization':'Bearer '+password}, timeout=15, follow_redirects=False)
            if response.status_code != 200:
                parser.exit(1, f'Link registration failed (HTTP {response.status_code}); no URL generated.\n')
            print(json.dumps(response.json(), indent=2) if args.stats else response.json()['url'])
        except (httpx.HTTPError, ValueError, KeyError):
            parser.exit(1, 'Could not register outreach link; no URL generated.\n')
    elif args.stats:
        from harness.outreach import report
        print(json.dumps(report(), indent=2))
    else:
        print(build_link(args.base, args.person, args.company, args.source, args.slug))
