"""Cache published podcast artwork URLs outside Git, from the original RSS feeds."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urljoin

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from harness.brain import FileBrain
from harness.ingestion import reference_root,read_record
from harness.podcast_artwork import parse_feed,match_artwork
from harness.web_tools import public_address


def fetch_feed(url):
    deadline=time.monotonic()+40
    for _ in range(5):
        address=public_address(url)
        parsed=httpx.URL(url)
        with httpx.Client(timeout=12,follow_redirects=False,trust_env=False) as client:
            with client.stream('GET',parsed.copy_with(host=address),headers={'Host':parsed.host,'User-Agent':'PortfolioArtwork/1.0','Accept':'application/rss+xml,application/xml,text/xml'},extensions={'sni_hostname':parsed.host}) as r:
                if r.status_code in (301,302,303,307,308):
                    url=urljoin(url,r.headers['location']);continue
                r.raise_for_status()
                body=bytearray()
                for chunk in r.iter_bytes(32768):
                    if len(body)+len(chunk)>20_000_000 or time.monotonic()>deadline:
                        raise ValueError('Feed exceeded its size or time limit.')
                    body.extend(chunk)
                return parse_feed(bytes(body))
    raise ValueError('Too many feed redirects.')


def sync(brain):
    root=reference_root(brain,'public','podcasts')
    records={p.stem:read_record(p) for p in root.glob('*.json')}
    records={k:r for k,r in records.items() if r['active'] and r['scope']=='public'}
    show_feeds={r['provenance'].get('show'):r['provenance']['feed_url'] for r in records.values() if r['provenance'].get('feed_url') and r['provenance'].get('show')}
    urls={r['provenance'].get('feed_url') or show_feeds.get(r['provenance'].get('show')) for r in records.values()}-{None,''}
    def fetch(url):
        try:return url,fetch_feed(url)
        except Exception:return url,None
    with ThreadPoolExecutor(max_workers=4) as pool:
        feeds=dict(pool.map(fetch,sorted(urls)))
    old=json.loads(brain.read('public','podcast-artwork.json') or '{"episodes":{}}')['episodes']
    images={k:v for k,v in old.items() if k in records}
    for ident,record in records.items():
        url=record['provenance'].get('feed_url') or show_feeds.get(record['provenance'].get('show'))
        feed=feeds.get(url)
        if feed:
            matched=match_artwork(record,feed)
            if matched:images[ident]={**matched,'source_url':url}
    scope=brain.scope('public');target=scope/'podcast-artwork.json'
    if target.is_symlink():raise ValueError('Artwork metadata cannot be a symbolic link.')
    temp=scope/('.podcast-artwork-'+uuid.uuid4().hex)
    temp.write_text(json.dumps(dict(version=1,episodes=images),indent=2,sort_keys=True)+'\n');temp.chmod(0o600);temp.replace(target)
    return dict(episodes=len(records),with_artwork=len(images),episode_art=sum(i['kind']=='episode' for i in images.values()),show_art=sum(i['kind']=='show' for i in images.values()),feeds_failed=sum(v is None for v in feeds.values()))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brain-root',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(sync(FileBrain(args.brain_root))))
