from fastapi.testclient import TestClient

from harness import my_career as career
from test_admin import AUTH, admin_app
from test_crm import add, company, source


def test_correct_sourced_preference_reconsiders_job(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    company_id = add(store, company())
    role = {'kind':'application','company_id':company_id,'title':'Workshop planner'}
    job_id = add(store, role)
    add(store, {**role,'title':'Past application','stage':'rejected'})
    quote = 'I prefer studio roles exclusively.'
    source(store, 'Owner career preferences: '+quote)
    assessments = []

    def fake_model(schema, instruction, data):
        if schema is career.Findings:
            index = next(i for i,s in enumerate(data) if quote in s['text'])
            return schema(claims=[
                career.Claim(statement='Studio roles only',source=index,quote=quote),
                career.Claim(statement='Unsupported',source=index,quote='invented source'),
            ])
        assessments.append(data['preferences'])
        eligible = 'Workshop roles are welcome.' in data['preferences']
        return schema(disposition='pursue' if eligible else 'ruled_out', preference=0,
                      reason='Workshop work matches the corrected scope.' if eligible else 'Outside the studio scope.')

    monkeypatch.setattr(career,'infer',fake_model)
    url = '/api/admin/crm/career'
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.get(url).status_code == 401
        assert client.post(url+'/discover',headers=AUTH).is_success
        initial = client.get(url,headers=AUTH).json()
        claim, = initial['claims']
        assert claim['quote'] == quote and 'muse' in claim['source']
        assert client.post(url+'/review',headers=AUTH).is_success
        original, = client.get(url,headers=AUTH).json()['jobs']
        assert original['id'] == job_id and original['disposition'] == 'ruled_out'
        edit = {'revision':initial['revision'],'index':0,'statement':'Workshop roles are welcome.'}
        assert client.put(url,json=edit,headers=AUTH).is_success
        assert client.put(url,json=edit,headers=AUTH).status_code == 409
        updated = client.get(url,headers=AUTH).json()
        assert updated['claims'][0]['history'][0]['quote'] == quote
        result, = updated['jobs']
        assert (result['previous'],result['disposition'],result['pending']) == ('ruled_out','pursue',False)
        assert client.post(url+'/review',headers=AUTH).is_success
        assert len(assessments) == 2
        assert store.brain.read('public',career.FILE) is None
        assert store.query('SELECT revision FROM crm_records WHERE id=?',(job_id,))[0]['revision'] == 1


def test_review_discards_superseded_result_and_recovers(tmp_path, monkeypatch):
    from harness.store import Store
    store = Store(tmp_path)
    co = add(store, company())
    add(store, {'kind':'application','company_id':co,'title':'Studio lead'})
    career.correct(store, career.Correction(revision=0,statement='Studio only'))

    def superseded(*args):
        career.correct(store, career.Correction(revision=1,index=0,statement='Both studios and workshops'))
        return career.Assessment(disposition='ruled_out', reason='Old assessment',preference=0)

    monkeypatch.setattr(career,'infer',superseded)
    career.review_one(store)
    assert career.state(store)['jobs'][0]['pending']
    assert not store.query('SELECT * FROM crm_career_assessments')

    def offline(*args):
        raise RuntimeError('Model offline')
    monkeypatch.setattr(career,'infer',offline)
    career.review_all(store)
    assert career.review_status(store)['error']
    from fastapi import BackgroundTasks
    import asyncio
    background = BackgroundTasks()
    monkeypatch.setattr(career,'infer',lambda *args: career.Assessment(
        disposition='research', reason='Check the scope', preference=0))
    career.schedule(store,background)
    asyncio.run(background())
    assert career.review_status(store) == {'running':False,'error':''}
    assert career.state(store)['jobs'][0]['disposition'] == 'research'
