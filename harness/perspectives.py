"""Owner-authored views and explicit influence links, stored outside source."""
import json
import re
from urllib.parse import urlparse


def perspective_hits(brain, query, mode):
    brain.scope(mode)
    tokens=set(re.findall(r'\w+',query.lower()))
    tokens-={'what','how','does','think','about','opinion','your','you','the','and','is','of','on','owner'}
    influence_query=bool(tokens & {'influence','influences','influenced','listened','listening','podcasts'})
    if not tokens:return []
    results=[]
    for scope in (['public'] if mode=='public' else ['public','private']):
        data=json.loads(brain.read(scope,'perspectives.json') or '{"version":1,"entries":[]}')
        if data.get('version')!=1 or not isinstance(data.get('entries'),list):
            raise ValueError('Invalid perspective records.')
        for entry in data['entries']:
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',entry['id']) or entry['basis'] not in ('owner_confirmed','inferred'):
                raise ValueError('Invalid perspective identity or basis.')
            if not isinstance(entry['view'],str) or len(entry['view'])>3000 or not isinstance(entry.get('topics'),list):
                raise ValueError('Invalid perspective content.')
            score=len(tokens & set(re.findall(r'\w+',' '.join(entry['topics']).lower())))
            if not score and not influence_query:continue
            influences=[]
            for source in entry.get('influences',[])[:8]:
                parsed=urlparse(source['url'])
                if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Invalid influence citation.')
                status=source.get('listening_status','unknown')
                if status not in ('owner_reported','unknown'):
                    raise ValueError('Invalid listening provenance.')
                influences.append({k:source.get(k) for k in ('source_id','title','url','listening_status','relevance')})
            evidence={k:entry.get(k) for k in ('id','basis','view','owner_source','limits')}
            evidence['influences']=influences
            text='# Owner perspective\n\n'+json.dumps(evidence,ensure_ascii=False)
            if len(text)>6000:
                raise ValueError('Perspective evidence exceeds the context budget.')
            results.append({'source':scope+'/perspectives.json#'+entry['id'],'text':text,
                            'score':100+score,'knowledge_role':'owner_perspective','basis':entry['basis']})
    ordered=sorted(results,key=lambda h:h['score'],reverse=True)
    selected=[];remaining=6000
    for hit in ordered:
        if len(hit['text'])>remaining:continue
        selected.append(hit);remaining-=len(hit['text'])
        if len(selected)==3:break
    return selected
