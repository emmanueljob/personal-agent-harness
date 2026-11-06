"""Podcast feed artwork metadata; no audio downloads or inference."""
import re
from urllib.parse import urlsplit
from lxml import etree
from .web_tools import public_url


def image_url(value):
    try:
        return public_url(value) if urlsplit(value).scheme == 'https' else ''
    except (ValueError, TypeError):
        return ''


def title_key(title):
    return ' '.join(re.findall(r'\w+',title.lower()))


def artwork(element):
    # Prefer iTunes/Media RSS image attributes, then the RSS image/url element.
    for node in element:
        name=etree.QName(node).localname if isinstance(node.tag,str) else ''
        if name in ('image','thumbnail','content'):
            url=image_url(node.get('href') or node.get('url') or '')
            if name=='content' and not node.get('type','').startswith('image/'):
                continue
            if url:return url
            for child in node:
                if isinstance(child.tag,str) and etree.QName(child).localname=='url':
                    url=image_url(child.text or '')
                    if url:return url
    return ''


def parse_feed(body):
    root=etree.fromstring(body,parser=etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))
    if root.getroottree().docinfo.doctype:
        raise ValueError('Podcast feeds cannot contain document type declarations.')
    channel=root.find('channel')
    if channel is None:raise ValueError('Expected an RSS podcast feed.')
    fallback=artwork(channel)
    episodes=[]
    for item in channel.findall('item'):
        url=artwork(item)
        episodes.append(dict(title=title_key(item.findtext('title') or ''),
            identities={s.strip() for s in (item.findtext('guid'),item.findtext('link')) if s and s.strip()},
            url=url or fallback,kind='episode' if url else 'show'))
    return dict(url=fallback,episodes=episodes)


def match_artwork(record,feed):
    provenance=record.get('provenance',{})
    identities={record['source_url'],provenance.get('guid'),provenance.get('episode_url')} - {None,''}
    matches=[e for e in feed['episodes'] if e['identities'] & identities]
    if not matches:
        matches=[e for e in feed['episodes'] if e['title']==title_key(record['title'])]
    if len(matches)==1 and matches[0]['url']:
        return dict(url=matches[0]['url'],kind=matches[0]['kind'])
    return dict(url=feed['url'],kind='show') if feed['url'] else None
