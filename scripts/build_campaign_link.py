"""Build tagged portfolio links without a hosted admin surface."""
import argparse
from urllib.parse import urlencode, urlsplit


def build_link(base, person='', company='', source='riviera'):
    url = urlsplit(base)
    if url.scheme != 'https' or not url.netloc or url.query or url.fragment:
        raise ValueError('Base must be an HTTPS URL without query or fragment')
    return base.rstrip('/')+'/?'+urlencode({k:v for k,v in {
        'utm_source':source, 'utm_campaign':company, 'utm_content':person,
    }.items() if v})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--person', default='')
    parser.add_argument('--company', default='')
    parser.add_argument('--source', default='riviera')
    parser.add_argument('--base', default='https://eman.hisemailaddress.org')
    args = parser.parse_args()
    print(build_link(args.base, args.person, args.company, args.source))
