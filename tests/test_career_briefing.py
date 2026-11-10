import pytest
from fastapi.testclient import TestClient
from harness import career_briefing, grok_connection as grok, my_career as career
from test_admin import AUTH, admin_app


def test_narrative_and_followup_with_existing_correction(tmp_path,monkeypatch):
    module,store = admin_app(tmp_path,monkeypatch)
    career.correct(store,career.Correction(revision=0,statement='Prefer field work'))
    calls = []
    def model(schema,instruction,data):
        calls.append(schema)
        if schema is career_briefing.Narrative:
            return schema(summary='You prefer field work.',questions=['Which environments have you enjoyed?'])
        assert 'Prefer field work' in data['current']
        return schema(claims=[
            career.Claim(statement='Enjoys forests',source=0,quote='I like forests'),
            career.Claim(statement='Unsupported',source=0,quote='fabricated quote')])
    monkeypatch.setattr(career,'infer',model)
    base = '/api/admin/crm/career'
    with TestClient(module.app) as client:
        assert client.post(base+'/narrative').status_code == 401
        assert client.post(base+'/narrative',headers=AUTH).json()['summary']=='You prefer field work.'
        client.post(base+'/narrative',headers=AUTH)
        assert calls.count(career_briefing.Narrative)==1
        payload = {'question':'Which environments?', 'answer':'The owner said “I like forests”.',
                   'request_id':'synthetic-question'}
        ident = client.post(base+'/questions/muse',headers=AUTH,json=payload).json()
        assert client.get(base+'/questions',headers=AUTH).json()['pending']==[ident]
        process = base+'/questions/'+ident+'/process'
        assert client.post(process,headers=AUTH).is_success
        assert client.post(process,headers=AUTH).is_success
        assert calls.count(career.Findings)==1
        owner,new = career.brief(store)['claims']
        assert owner['source']=='Owner correction'
        assert new['source']=='muse · Which environments?' and new['quote']=='I like forests'
        assert client.get(base+'/questions',headers=AUTH).json()['pending']==[]
        client.post(base+'/narrative',headers=AUTH)
        assert calls.count(career_briefing.Narrative)==2


def test_grok_question_roundtrip_and_duplicate_callback(tmp_path,monkeypatch):
    from harness.store import Store
    from fastapi import HTTPException
    store = Store(tmp_path)
    sent = []
    monkeypatch.setenv('GROKBOT_MCP_TOKEN','synthetic')
    monkeypatch.setattr(grok,'push_webhook',lambda data:sent.append(data) or 200)
    ask = grok.Assignment(action='career_question',request_id='synthetic-request',question='Which environment?')
    with pytest.raises(HTTPException):
        grok.create_assignment(store,ask)
    proof = grok.create_assignment(store,grok.Assignment(request_id='synthetic-proof'))['id']
    def deliver(ident,report):
        grok.dispatch_assignment(store,ident)
        claim=grok.acknowledge(store,ident,'synthetic-claim')
        result=grok.Result(submission_id='synthetic-result',outcome='completed',report=report)
        grok.submit(store,ident,'synthetic-claim',result)
        assert grok.submit(store,ident,'synthetic-claim',result)['duplicate']
        return claim['instruction']
    deliver(proof,'SYNTHETIC CONNECTION OK')
    task=grok.create_assignment(store,ask)['id']
    with pytest.raises(HTTPException):
        grok.create_assignment(store,ask.model_copy(update={'question':'Another question?'}))
    assert ask.question in deliver(task,'Owner prefers field work.')
    assert ask.question in sent[-1]['instruction']
    progress=career_briefing.progress(store)
    assert len(progress['pending'])==1 and progress['tasks'][0]['status']=='completed'
