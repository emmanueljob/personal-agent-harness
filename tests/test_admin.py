import json
from urllib.parse import urlsplit, parse_qs
import pytest
from fastapi.testclient import TestClient
from test_agent_surfaces import fixture_app
from harness.portfolio import Portfolio
from harness.site_config import records
from test_personalization import tags_from_url

AUTH={'Authorization':'Bearer synthetic-owner-access'}


def admin_app(tmp_path,monkeypatch):
    module, store=fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('OWNER_STUDIO_TOKEN','synthetic-owner-access')
    monkeypatch.setenv('PUBLIC_ORIGIN','https://testserver')
    data=Portfolio(name='Fixture Leader',projects=[dict(id=id,eyebrow='Public work',title=id,company='Fixture',description='Public description',detail='Public detail',outcome='Public outcome',question='How did this work?') for id in ['mojo','ads']])
    (store.brain.scope('public')/'portfolio.json').write_text(data.model_dump_json())
    return module,store


def test_owner_boundary_no_private_bootstrap_or_tracking(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        home=client.get('/admin')
        assert home.status_code==200 and home.headers['x-robots-tag']=='noindex, nofollow'
        assert '/visit.js' not in home.text and 'id="personalization"' not in home.text and 'id="agent-entry"' not in home.text
        assert client.get('/api/admin/config').status_code==401
        assert client.get('/api/admin/outreach').status_code==401
        assert client.post('/api/admin/links',json={}).status_code==401
        assert client.get('/api/admin/config',headers={'Authorization':'Bearer not-the-owner'}).status_code==401
        response=client.get('/api/admin/config',headers=AUTH)
        assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
        assert 'synthetic-owner-access' not in response.text
        assert 'PRIVATE PROFILE CANARY' not in response.text
        assert 'PRIVATE NOTE CANARY' not in response.text
        assert '/api/admin' not in client.get('/api/public/openapi.json').text
        denied=client.put('/api/admin/settings',headers={**AUTH,'Origin':'https://untrusted.example'},json={})
        assert denied.status_code==403
        monkeypatch.delenv('OWNER_STUDIO_TOKEN')
        assert client.get('/api/admin/config',headers=AUTH).status_code==404


def test_runtime_settings_and_optimistic_concurrency(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        config=client.get('/api/admin/config',headers=AUTH).json()
        settings={**config['settings'],'contact_email':'public@example.org','booking_url':'https://example.org/book','linkedin_partner_id':'123','x_pixel_id':'abc','guide_instructions':'Owner-approved direction for future turns.'}
        edit={'revision':config['settings_revision'],'value':settings}
        assert client.put('/api/admin/settings',headers=AUTH,json=edit).status_code==200
        assert client.put('/api/admin/settings',headers=AUTH,json=edit).status_code==409
        profile=client.get('/api/personalization').json()
        assert profile['email']=='mailto:public@example.org' and profile['booking']=='https://example.org/book'
        assert 'guide_instructions' not in profile
        assert client.get('/api/visit-config').json()=={'linkedin':'123','x':'abc','enabled':True}
        csp=client.get('/').headers['content-security-policy']
        assert 'snap.licdn.com' in csp and 'static.ads-twitter.com' in csp
        assert records()['settings']['revision']==1
        from harness.site_config import path
        assert path().stat().st_mode & 0o777 == 0o600
        settings['tracking_enabled']=False
        assert client.put('/api/admin/settings',headers=AUTH,json={'revision':1,'value':settings}).status_code==200
        assert not client.get('/api/visit-config').json()['enabled']
        assert 'snap.licdn.com' not in client.get('/').headers['content-security-policy']
        for field,value in [('booking_url','javascript:alert(1)'),('contact_email','bad\nrecipient@example.org'),('linkedin_partner_id','<script>'),('plausible_domain','https://example.org')]:
            bad={**settings,field:value}
            assert client.put('/api/admin/settings',headers=AUTH,json={'revision':2,'value':bad}).status_code==422


def test_custom_profile_link_preview_and_revocation(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        config=client.get('/api/admin/config',headers=AUTH).json()
        seed=next(p['value'] for p in config['profiles'] if p['value']['key']=='source:warm')
        p={**seed,'key':'content:fixture','headline':'Hi Fixture — a personal overview','featured':['mojo'],'system_note':'INTERNAL AUDIENCE GUIDANCE fixture'}
        assert client.put('/api/admin/profiles/content:fixture',headers=AUTH,json={'revision':0,'value':p}).status_code==200
        assert client.put('/api/admin/profiles/content:fixture',headers=AUTH,json={'revision':0,'value':p}).status_code==409
        values={'person':'fixture','company':'hex','source':'warm'}
        preview=client.post('/api/admin/resolve',headers=AUTH,json=values).json()
        assert preview['key']=='content:fixture' and 'system_note' not in preview
        link=client.post('/api/admin/links',headers=AUTH,json=values).json()['url']
        assert 'fixture' not in link and 'hex' not in link and 'warm' not in link
        assert 'Hi Fixture' in client.get(link).text
        assert 'INTERNAL AUDIENCE GUIDANCE' not in client.get(link).text
        tags=tags_from_url(link)
        event={'name':'pageview','url':'/?'+urlsplit(link).query,'utm':tags,'profile_key':'content:fixture'}
        assert client.post('/api/visit-events',json=event).status_code==204
        report=client.get('/api/admin/outreach',headers=AUTH).json()
        assert report['counts'][0]['count']==1 and len(report['links'])==1
        ident=report['links'][0]['id']
        assert client.put('/api/admin/links/'+ident,headers=AUTH,json={'active':False}).status_code==200
        assert client.get('/api/personalization',params=tags).json()['key']=='default'
        assert client.post('/api/visit-events',json=event).status_code==204
        assert client.get('/api/admin/outreach',headers=AUTH).json()['counts'][0]['count']==1
        assert client.put('/api/admin/links/'+ident,headers=AUTH,json={'active':True}).status_code==200
        assert client.get('/api/personalization',params=tags).json()['key']=='content:fixture'
        assert client.put('/api/admin/links/missing',headers=AUTH,json={'active':False}).status_code==404
        p['enabled']=False
        assert client.put('/api/admin/profiles/content:fixture',headers=AUTH,json={'revision':1,'value':p}).status_code==200
        assert client.get('/api/personalization',params=tags).json()['key']=='campaign:hex'
        default=next(p['value'] for p in config['profiles'] if p['value']['key']=='default')
        assert client.put('/api/admin/profiles/default',headers=AUTH,json={'revision':0,'value':{**default,'enabled':False}}).status_code==422


def test_portfolio_edits_and_guide_use_runtime_copy(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        config=client.get('/api/admin/config',headers=AUTH).json()
        data=config['portfolio'];data['intro']='New owner-approved introduction.';data['projects'][0]['description']='New owner-approved Mojo outcome.'
        assert client.post('/api/admin/portfolio/validate',headers=AUTH,json=data).status_code==200
        assert client.put('/api/admin/portfolio',headers=AUTH,json={'revision':0,'value':data}).status_code==200
        assert client.get('/api/portfolio').json()['intro']=='New owner-approved introduction.'
        assert 'New owner-approved Mojo outcome.' in client.get('/api/portfolio').text
        assert 'New owner-approved' not in store.brain.public_portfolio().get('intro','')
        from harness.personalization import guide_context
        assert 'New owner-approved Mojo outcome.' in guide_context('default')
        data['projects'].append(data['projects'][0])
        assert client.put('/api/admin/portfolio',headers=AUTH,json={'revision':1,'value':data}).status_code==422
        assert client.put('/api/admin/portfolio',headers=AUTH,json={'revision':0,'value':config['portfolio']}).status_code==422


def test_global_tracking_disable_stops_local_counts(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        url=client.post('/api/admin/links',headers=AUTH,json={'source':'cold'}).json()['url']
        config=client.get('/api/admin/config',headers=AUTH).json()
        edit={**config['settings'],'tracking_enabled':False}
        assert client.put('/api/admin/settings',headers=AUTH,json={'revision':0,'value':edit}).status_code==200
        event={'name':'pageview','url':'/?'+urlsplit(url).query,'utm':tags_from_url(url)}
        assert client.post('/api/visit-events',json=event).status_code==204
        assert client.get('/api/admin/outreach',headers=AUTH).json()['counts']==[]


def test_dynamic_guide_snapshot_and_legacy_tag_management(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        config=client.get('/api/admin/config',headers=AUTH).json()
        p=next(p['value'] for p in config['profiles'] if p['value']['key']=='default')
        p={**p,'key':'content:new-fixture','system_note':'Owner-saved audience context','questions':['One?','Two?','Three?']}
        assert client.put('/api/admin/profiles/content:new-fixture',headers=AUTH,json={'revision':0,'value':p}).status_code==200
        ident=store.enqueue(store.thread('public')['id'],'Why is this a fit?',profile_key='content:new-fixture')
        from harness.agent import prepare_turn
        _,snapshot=prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(ident,))[0])
        assert 'Owner-saved audience context' in snapshot['profile']
        p['system_note']='New context for future turns'
        assert client.put('/api/admin/profiles/content:new-fixture',headers=AUTH,json={'revision':1,'value':p}).status_code==200
        _,retry=prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(ident,))[0])
        assert retry==snapshot
        url=client.post('/api/admin/links',headers=AUTH,json={'source':'warm'}).json()['url']
        tags=tags_from_url(url)
        assert client.put('/api/admin/tags/'+tags['utm_source'],headers=AUTH,json={'active':False}).status_code==200
        assert client.get('/api/personalization',params=tags).json()['key']=='default'
        assert not client.get('/api/admin/outreach',headers=AUTH).json()['tags'][0]['active']
        assert client.put('/api/admin/tags/'+tags['utm_source'],headers=AUTH,json={'active':True}).status_code==200
        assert client.get('/api/personalization',params=tags).json()['key']=='source:warm'


def test_delete_link_removes_mappings_counts_and_keeps_other_links(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        urls=[client.post('/api/admin/links',headers=AUTH,json={'source':'warm','company':'hex','person':'chett'}).json()['url'] for _ in range(2)]
        original_tags=tags_from_url(urls[0])
        for url in urls:
            tags=tags_from_url(url)
            assert client.post('/api/visit-events',json={'name':'pageview','url':'/?'+urlsplit(url).query,'utm':tags}).status_code==204
        state=client.get('/api/admin/outreach',headers=AUTH).json()
        selected=next(link for link in state['links'] if link['id']==urlsplit(urls[0]).path.rsplit('/',1)[-1])
        assert client.delete('/api/admin/links/'+selected['id']).status_code==401
        assert client.delete('/api/admin/links/'+selected['id'],headers={**AUTH,'Origin':'https://untrusted.example'}).status_code==403
        before=client.get('/api/admin/config',headers=AUTH).json()
        result=client.delete('/api/admin/links/'+selected['id'],headers=AUTH)
        assert result.status_code==200
        assert result.json()=={'links_deleted':1,'tags_deleted':3,'count_rows_deleted':1}
        state=client.get('/api/admin/outreach',headers=AUTH).json()
        assert len(state['links'])==1 and len(state['tags'])==3 and len(state['counts'])==1
        tags=original_tags
        assert client.get('/api/personalization',params=tags).json()['key']=='default'
        assert client.post('/api/visit-events',json={'name':'pageview','url':'/?'+urlsplit(urls[0]).query,'utm':tags}).status_code==204
        assert len(client.get('/api/admin/outreach',headers=AUTH).json()['counts'])==1
        assert client.get('/api/admin/config',headers=AUTH).json()==before
        assert client.delete('/api/admin/links/'+selected['id'],headers=AUTH).status_code==404


def test_clear_old_links_includes_legacy_but_preserves_newer_links(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    import sqlite3,time
    from harness.outreach import database_path
    with TestClient(module.app,base_url='https://testserver') as client:
        old=client.post('/api/admin/links',headers=AUTH,json={'source':'warm'}).json()['url']
        oldtoken=tags_from_url(old)['utm_source']
        with sqlite3.connect(database_path()) as db:
            db.execute('DELETE FROM outreach_links')  # Reproduce pre-portal legacy tags.
        cutoff=time.time()
        newer=client.post('/api/admin/links',headers=AUTH,json={'source':'cold'}).json()['url']
        assert client.delete('/api/admin/links').status_code==401
        result=client.delete('/api/admin/links',headers=AUTH,params={'before':cutoff}).json()
        assert result['tags_deleted']==1 and result['links_deleted']==0
        state=client.get('/api/admin/outreach',headers=AUTH).json()
        assert len(state['links'])==1 and len(state['tags'])==1 and oldtoken not in str(state)
        newtags=tags_from_url(newer)
        assert client.get('/api/personalization',params=newtags).json()['key']=='source:cold'
        token=newtags['utm_source']
        result=client.delete('/api/admin/tags/'+token,headers=AUTH).json()
        assert result['tags_deleted']==1 and result['links_deleted']==1
        assert client.get('/api/admin/outreach',headers=AUTH).json()=={'links':[],'tags':[],'counts':[]}
        assert client.delete('/api/admin/tags/'+token,headers=AUTH).status_code==404
        assert client.delete('/api/admin/links',headers=AUTH).json()=={'links_deleted':0,'tags_deleted':0,'count_rows_deleted':0}
