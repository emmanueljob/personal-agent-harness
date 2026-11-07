import io
import hashlib
import subprocess
import shutil
from pathlib import Path
import pytest
from scripts import privacy_guard as guard


@pytest.fixture
def repo(tmp_path,monkeypatch):
    subprocess.run(['git','init','-q','-b','main',str(tmp_path)],check=True)
    for key,value in [('user.name','Test Author'),('user.email','test@example.org')]:
        subprocess.run(['git','-C',str(tmp_path),'config',key,value],check=True)
    (tmp_path/'security').mkdir()
    (tmp_path/'security/gitleaks.toml').write_text((guard.ROOT/'security/gitleaks.toml').read_text())
    monkeypatch.setattr(guard,'ROOT',tmp_path)
    monkeypatch.setenv('BRAIN_DIR',str(tmp_path/'local-brain'))
    return tmp_path


def stage(path,name,text):
    target=path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
    subprocess.run(['git','-C',str(path),'add',name],check=True)


def commit(path,message='test change'):
    subprocess.run(['git','-C',str(path),'commit','-qm',message],check=True)


def test_index_is_scanned_even_when_working_copy_has_been_cleaned(repo):
    stage(repo,'notes.md','safe text')
    (repo/'notes.md').write_text('different working copy')
    oid=next(guard.index_entries())[0]
    assert guard.git('cat-file','-p',oid)==b'safe text'
    guard.scan(guard.index_entries())


@pytest.mark.parametrize('path',['.env','nested/.env.local','brain/public/profile.md','runtime.sqlite','resume.pdf','notes/voice.md','.runtime/config.json','copy.zip'])
def test_forced_sensitive_paths_are_blocked(repo,path):
    stage(repo,path,'private source')
    with pytest.raises(guard.Blocked):guard.scan(guard.index_entries())


def test_secret_deleted_in_later_commit_still_blocks_push(repo):
    secret='gh'+'p_'+hashlib.sha256(b'nonfunctional-test-credential').hexdigest()[:36]
    stage(repo,'example.py','token = "'+secret+'" # gitleaks:allow\n');commit(repo)
    stage(repo,'example.py','token = None\n');commit(repo)
    tips=guard.git('rev-parse','HEAD').decode().splitlines()
    with pytest.raises(guard.Blocked,match='Credential scanner'):guard.scan(guard.history_entries(tips))


def test_copied_private_text_and_local_credentials_are_blocked(repo,monkeypatch):
    private=repo/'local-brain/private';private.mkdir(parents=True)
    text='A private account of a meeting with twelve distinctive details that should remain entirely outside the source repository.'
    (private/'reference.md').write_text(text)
    stage(repo,'notes.md',text.upper())
    with pytest.raises(guard.Blocked,match='private knowledge'):guard.scan(guard.index_entries())
    secret='test-only-'+('sensitive-'*4)
    monkeypatch.setenv('EXAMPLE_API_KEY',secret)
    stage(repo,'notes.md',secret)
    with pytest.raises(guard.Blocked,match='local credential'):guard.scan(guard.index_entries())


def test_symlinks_and_missing_scanner_fail_closed(repo,monkeypatch):
    with pytest.raises(guard.Blocked,match='symlinks'):guard.check_path('copy.md','120000')
    stage(repo,'example.py','print("hello")\n')
    monkeypatch.setattr(guard,'GITLEAKS',repo/'missing')
    with pytest.raises(guard.Blocked,match='scanner missing'):guard.scan(guard.index_entries())


def test_owner_checkout_requires_its_private_references(repo):
    subprocess.run(['git','-C',str(repo),'config','privacy.requireLocalKnowledge','true'],check=True)
    stage(repo,'example.py','print("hello")\n')
    with pytest.raises(guard.Blocked,match='references are unavailable'):guard.scan(guard.index_entries())


def test_pre_push_deletion_and_new_ref_history(repo,monkeypatch):
    stage(repo,'example.py','print("hello")\n');commit(repo)
    sha=guard.git('rev-parse','HEAD').decode().strip();zero='0'*40
    monkeypatch.setattr('sys.argv',['privacy_guard','--pre-push'])
    monkeypatch.setattr('sys.stdin',io.StringIO(f'refs/heads/main {sha} refs/heads/main {zero}\n'))
    guard.main()
    monkeypatch.setattr('sys.stdin',io.StringIO(f'(delete) {zero} refs/heads/main {sha}\n'))
    guard.main()


@pytest.mark.parametrize('kind',['commit','nested-tag'])
def test_commit_message_and_nested_tag_are_scanned(repo,kind):
    secret='gh'+'p_'+hashlib.sha256(b'nonfunctional-test-credential').hexdigest()[:36]
    stage(repo,'example.py','print("hello")\n');commit(repo,'credential: '+secret if kind=='commit' else 'safe commit')
    tip='HEAD'
    if kind=='nested-tag':
        subprocess.run(['git','-C',str(repo),'tag','-a','inner','-m','credential: '+secret],check=True)
        subprocess.run(['git','-C',str(repo),'tag','-a','outer','inner','-m','outer tag'],check=True,capture_output=True)
        tip='outer'
    with pytest.raises(guard.Blocked,match='Credential scanner'):guard.scan(guard.history_entries([tip]))


def test_real_pre_push_hook_blocks_deleted_private_file_before_transfer(tmp_path,monkeypatch):
    source=guard.ROOT
    work=tmp_path/'work';remote=tmp_path/'remote.git'
    subprocess.run(['git','init','-q','--bare',str(remote)],check=True)
    subprocess.run(['git','init','-q','-b','main',str(work)],check=True)
    for name in ('.githooks/pre-commit','.githooks/pre-push','scripts/privacy_guard.py','security/gitleaks.toml','.runtime/tools/gitleaks/gitleaks'):
        target=work/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
    for key,value in [('user.name','Test Author'),('user.email','test@example.org'),('core.hooksPath','.githooks')]:
        subprocess.run(['git','-C',str(work),'config',key,value],check=True)
    subprocess.run(['git','-C',str(work),'remote','add','origin',str(remote)],check=True)
    monkeypatch.setenv('BRAIN_DIR',str(tmp_path/'empty-brain'))
    stage(work,'example.py','print("hello")\n');commit(work)
    subprocess.run(['git','-C',str(work),'push','-q','origin','main'],check=True,capture_output=True)
    safe=subprocess.check_output(['git','-C',str(remote),'rev-parse','refs/heads/main']).strip()
    stage(work,'brain/private/profile.md','Synthetic private data, never a real personal record.')
    subprocess.run(['git','-C',str(work),'-c','core.hooksPath=/dev/null','commit','-qm','synthetic bypass fixture'],check=True)
    subprocess.run(['git','-C',str(work),'rm','-q','brain/private/profile.md'],check=True)
    commit(work,'remove unsafe path from tip')
    result=subprocess.run(['git','-C',str(work),'push','origin','main'],capture_output=True)
    assert result.returncode != 0
    assert b'PUBLICATION BLOCKED' in result.stderr
    assert subprocess.check_output(['git','-C',str(remote),'rev-parse','refs/heads/main']).strip()==safe


def test_reviewed_public_asset_requires_exact_content(repo, monkeypatch):
    asset = 'frontend/public/images/alpine-panorama.jpeg'
    body = b'\xff\xd8synthetic-test-image\x00\xff\xd9'
    monkeypatch.setattr(guard, 'PUBLIC_ASSETS', {asset: hashlib.sha256(body).hexdigest()})
    target = repo / asset
    target.parent.mkdir(parents=True)
    target.write_bytes(body)
    subprocess.run(['git', '-C', str(repo), 'add', asset], check=True)
    guard.scan(guard.index_entries())
    target.write_bytes(body + b'changed')
    subprocess.run(['git', '-C', str(repo), 'add', asset], check=True)
    with pytest.raises(guard.Blocked, match='differs from reviewed'):
        guard.scan(guard.index_entries())
    with pytest.raises(guard.Blocked, match='unreviewed file type'):
        guard.check_path('frontend/public/images/other.jpeg', '100644')
