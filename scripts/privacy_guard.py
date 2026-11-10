"""Fail closed on sensitive paths, local private fingerprints and credentials.

Reads Git's actual index/objects, never a working-tree approximation of a commit.
Diagnostics name files/categories only; matching content is never printed.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
GITLEAKS = ROOT / '.runtime/tools/gitleaks/gitleaks'
VERSION = '8.30.1'
MAX_BLOB = 2_000_000
MAX_SCAN = 64_000_000
BLOCKED_DIRS = {'.runtime','.venv','node_modules','dist','data','brain','state','private','raw','artifacts','previews','downloads','.ssh','.aws','__pycache__','.pytest_cache'}
BLOCKED_NAMES = {'profile.md','voice.md','portfolio.json','perspectives.json','learning.json','podcast-artwork.json','bookshelf.json','library.json','.ds_store','.gitconfig','.netrc','.npmrc'}
TEXT_TYPES = {'.py','.ts','.tsx','.js','.mjs','.css','.html','.json','.toml','.md','.txt','.yaml','.yml','.sh','.lock'}
# Owner-provided synthetic landscape, visually reviewed; replacements need review.
PUBLIC_ASSETS = {'frontend/public/images/alpine-panorama.jpeg': '1e2c892c99e2bd1a07c1ae6cfbfd66533a720887291ad0bae4bb895d9d9ea7a7'}
SPECIAL = {'Dockerfile','.dockerignore','.gitignore','.env.example','.gitleaks.toml','.githooks/pre-commit','.githooks/pre-push'}
# Reviewed source patch for the separately built companion; still scanned as text.
# Do not permit arbitrary patch exports (which can contain runtime/knowledge data).
SPECIAL.add('integrations/whatsapp-muse/companion.patch')


class Blocked(Exception):
    pass


def git(*args):
    result = subprocess.run(['git',*args],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        raise Blocked('Cannot inspect Git objects; publication is blocked.')
    return result.stdout


def check_path(path, mode):
    p = PurePosixPath(path)
    if mode in ('120000','160000'):
        raise Blocked(f'{path}: symlinks/submodules require a separate publication review.')
    if set(part.lower() for part in p.parts) & BLOCKED_DIRS:
        raise Blocked(f'{path}: runtime, dependency or personal-data path.')
    if p.name.lower() in BLOCKED_NAMES or (p.name.startswith('.env') and path != '.env.example'):
        raise Blocked(f'{path}: personal data or environment file.')
    if path not in PUBLIC_ASSETS and path not in SPECIAL and p.suffix.lower() not in TEXT_TYPES:
        raise Blocked(f'{path}: unreviewed file type; only source/configuration text is publishable.')


def env_values():
    values = dict(os.environ)
    file = ROOT / '.env'
    if file.is_file():
        for line in file.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value = line.split('=',1)
                values[key.strip()] = value.strip().strip('"\'')
    return values


def windows(text):
    words = re.findall(r'[\w@.+-]+',text.lower())
    return (hashlib.sha256(' '.join(words[i:i+12]).encode()).digest() for i in range(max(0,len(words)-11)))


def local_private_fingerprints():
    values = env_values()
    secrets = [value.encode() for key,value in values.items() if len(value)>=8 and re.search(r'api.?key|token|password|secret|credential',key,re.I)]
    home = Path(values.get('AFTERHOURS_HOME') or (Path.home()/'Library/Application Support/Afterhours' if sys.platform=='darwin' else Path(values.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'afterhours'))
    brain = Path(values.get('BRAIN_DIR') or home/'brain').expanduser()
    fingerprints, emails = set(), set()
    for directory in (brain/'private',brain/'raw'):
        if not directory.exists():
            continue
        for path in directory.rglob('*'):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ('.md','.txt','.json','.eml','.html'):
                continue
            if path.stat().st_size > MAX_BLOB:
                raise Blocked('A private reference exceeds the fingerprint size limit; review it before publication.')
            try:
                text = path.read_text()
            except UnicodeError:
                continue
            fingerprints.update(windows(text))
            for email in re.findall(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',text):
                if not email.lower().endswith(('@example.org','@example.com','@example.net')):
                    emails.add(email.lower().encode())
    required = subprocess.run(['git','config','--local','--bool','--get','privacy.requireLocalKnowledge'],cwd=ROOT,capture_output=True)
    if required.stdout.strip() == b'true' and not fingerprints:
        raise Blocked('Required local private references are unavailable; publication is blocked.')
    return secrets, fingerprints, emails


def index_entries():
    for row in git('ls-files','--stage','-z').split(b'\0'):
        if not row:
            continue
        metadata,path = row.split(b'\t',1)
        mode,oid,stage = metadata.decode().split()
        if stage != '0':
            raise Blocked('Resolve unmerged index entries before publication.')
        yield oid,path.decode(),mode


def history_entries(tips):
    for tip in tips:
        while git('cat-file','-t',tip).strip() == b'tag':
            yield tip,'<tag metadata>','metadata'
            tip = git('cat-file','-p',tip).splitlines()[0].split()[1].decode()
    commits = git('rev-list',*tips).decode().splitlines()
    for commit in commits:
        yield commit,'<commit metadata>','metadata'
        for row in git('ls-tree','-r','-z',commit).split(b'\0'):
            if not row:
                continue
            metadata,path = row.split(b'\t',1)
            mode,_,oid = metadata.decode().split()
            yield oid,path.decode(),mode


def scan(entries):
    secrets,fingerprints,emails = local_private_fingerprints()
    seen,contents,total = set(),[],0
    for oid,path,mode in entries:
        if (oid,path) in seen:
            continue
        seen.add((oid,path))
        if mode != 'metadata':
            check_path(path,mode)
        size = int(git('cat-file','-s',oid))
        if size > MAX_BLOB:
            raise Blocked(f'{path}: oversized source object requires review.')
        body = git('cat-file','-p',oid)
        if path in PUBLIC_ASSETS:
            if hashlib.sha256(body).hexdigest() != PUBLIC_ASSETS[path]:
                raise Blocked(f'{path}: public asset differs from reviewed content.')
            total += len(body)
            if total > MAX_SCAN:
                raise Blocked('History scan exceeds the review budget; do not bypass the hook.')
            continue
        try:
            text = body.decode('utf-8')
        except UnicodeError:
            raise Blocked(f'{path}: binary data is not approved for publication.') from None
        if b'\0' in body:
            raise Blocked(f'{path}: binary data is not approved for publication.')
        if path == '.env.example':
            for line in text.splitlines():
                key,separator,value = line.partition('=')
                if separator and not key.lstrip().startswith('#') and re.search(r'api.?key|token|password|secret|credential',key,re.I) and value.strip().strip('"\''):
                    raise Blocked('.env.example: credential placeholders must be empty.')
        if any(secret in body for secret in secrets):
            raise Blocked(f'{path}: contains a local credential value (redacted).')
        if any(email in body.lower() for email in emails):
            raise Blocked(f'{path}: contains an email from private source material (redacted).')
        if fingerprints.intersection(windows(text)):
            raise Blocked(f'{path}: matches a passage from local private knowledge (redacted).')
        total += len(body)
        if total > MAX_SCAN:
            raise Blocked('History scan exceeds the review budget; do not bypass the hook.')
        contents.append((path+'\n').encode()+body+b'\n')
    if not GITLEAKS.is_file():
        raise Blocked('Secret scanner missing. Run python3 scripts/setup_git_safety.py.')
    version = subprocess.run([str(GITLEAKS),'version'],capture_output=True,text=True)
    if version.returncode or version.stdout.strip() != VERSION:
        raise Blocked('Secret scanner version mismatch; rerun scripts/setup_git_safety.py.')
    with tempfile.TemporaryDirectory(prefix='privacy-scan-') as temp:
        report = Path(temp)/'redacted.json'
        result = subprocess.run([str(GITLEAKS),'stdin','--config',str(ROOT/'security/gitleaks.toml'),'--ignore-gitleaks-allow','--gitleaks-ignore-path',str(Path(temp)/'no-ignores'),'--redact','--no-banner','--log-level','error','--report-format','json','--report-path',str(report)],input=b'\n'.join(contents),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if result.returncode:
            raise Blocked('Credential scanner found a secret or could not complete; output is suppressed to avoid disclosing matches.')
    print(f'Privacy check passed: {len(seen)} Git objects; sensitive paths, local fingerprints and Gitleaks checked.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--staged',action='store_true')
    group.add_argument('--all',action='store_true')
    group.add_argument('--pre-push',action='store_true')
    args = parser.parse_args()
    if args.staged:
        entries = index_entries()
    else:
        if args.pre_push:
            tips = []
            for line in sys.stdin:
                parts = line.split()
                if len(parts) != 4 or not re.fullmatch('[0-9a-f]{40,64}',parts[1]):
                    raise Blocked('Invalid pre-push ref input.')
                if set(parts[1]) != {'0'}:
                    tips.append(parts[1])
            if not tips:
                print('No objects to publish (ref deletion or no updates).')
                return
        else:
            # Include detached HEAD (e.g. a CI merge checkout), plus every local ref.
            tips = list(set(git('rev-parse','--verify','HEAD').decode().splitlines() + git('for-each-ref','--format=%(objectname)').decode().splitlines()))
        entries = history_entries(tips)
    scan(entries)


if __name__ == '__main__':
    try:
        main()
    except (Blocked,OSError,ValueError) as error:
        # Errors from subprocesses/providers are intentionally never printed.
        print('PUBLICATION BLOCKED: '+(str(error) if isinstance(error,Blocked) else 'Privacy check could not complete safely.'),file=sys.stderr)
        raise SystemExit(1)
