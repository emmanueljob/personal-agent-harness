import json
import pytest
from fastapi.testclient import TestClient
from harness.personalization import all_profiles, position_portfolio, guide_context
from harness.portfolio import Portfolio
from harness.site_config import read, save
from scripts.import_site_data import import_data
from test_agent_surfaces import fixture_app


def test_blank_install_has_no_owner_content(tmp_path,monkeypatch):
    module,_=fixture_app(tmp_path,monkeypatch)
    monkeypatch.delenv('PORTFOLIO_CONTACT_EMAIL',raising=False)
    monkeypatch.delenv('PORTFOLIO_BOOKING_URL',raising=False)
    assert list(all_profiles()) == ['default']
    assert position_portfolio(Portfolio()) == Portfolio()
    assert ']().' not in guide_context('default')
    with TestClient(module.app) as client:
        data=client.get('/api/personalization?utm_source=unknown').json()
        assert data['key']=='default' and not data['pitch'] and not data['email'] and not data['banner']


def test_import_is_atomic_validated_and_preserves_edits(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'state'))
    source=tmp_path/'site-data.json'
    data={'version':1,'settings':{'contact_email':'fixture@example.org'},'profiles':[],
          'portfolio':Portfolio(name='Synthetic Person').model_dump()}
    source.write_text(json.dumps(data))
    assert import_data(source)['imported']==0 and read('settings') is None
    assert import_data(source,True)['imported']==2
    save('settings',{'contact_email':'edited@example.org'},1)
    assert import_data(source,True)['preserved']==2
    assert read('settings')['contact_email']=='edited@example.org'
    data['profiles']=[{'key':'campaign:sample','featured':['missing']}]
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError):import_data(source,True)
    assert read('profile:campaign:sample') is None
    assert read('settings')['contact_email']=='edited@example.org'


def test_public_agent_uses_same_external_portfolio(tmp_path,monkeypatch):
    module,_=fixture_app(tmp_path,monkeypatch)
    save('portfolio',Portfolio(name='Synthetic Updated Name').model_dump(),0)
    with TestClient(module.app) as client:
        assert client.get('/api/portfolio').json()['name']=='Synthetic Updated Name'
        assert client.get('/api/public/profile').json()['profile']['name']=='Synthetic Updated Name'


def test_project_links_reject_script_urls():
    from harness.portfolio import Project
    with pytest.raises(ValueError):
        Project(id='test',eyebrow='',title='',company='',description='',detail='',outcome='',question='',url='javascript:alert(1)')
