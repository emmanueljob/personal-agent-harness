"""Export the explicitly published brain, separately from code and local state."""
import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def export_public(brain_root, destination):
    root = Path(brain_root)/'public'
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Expected a real published public scope.')
    root = root.resolve()
    destination = Path(destination).resolve()
    repo = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(repo) and not destination.is_relative_to(repo/'.runtime'):
        raise ValueError('The publication bundle must stay outside tracked source.')
    entries = {}
    def add(relative, required=False):
        source = root/relative
        if any((root/Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
            raise ValueError('Published source cannot contain symbolic links.')
        if not source.is_file():
            if required:
                raise ValueError('Required published source is missing.')
            return
        if not source.resolve().is_relative_to(root):
            raise ValueError('Published source escapes its scope.')
        body = source.read_bytes()
        if source.read_bytes() != body:
            raise ValueError('Published source changed during export; retry.')
        entries['brain/public/'+relative.as_posix()] = body
    for name in ('profile.md', 'voice.md', 'portfolio.json', 'bookshelf.json'):
        add(Path(name), required=True)
    # Only an explicitly published, bounded PDF belongs in a public export.
    if (root/'resume.pdf').exists() or (root/'resume.pdf').is_symlink():
        from harness.brain import FileBrain
        from harness.resume import _resume_bytes
        if _resume_bytes(FileBrain(Path(brain_root))) is None:
            raise ValueError('Published resume must be a regular, bounded PDF.')
        add(Path('resume.pdf'))
    if (root/'resume.json').exists() or (root/'resume.json').is_symlink():
        from harness.brain import FileBrain
        from harness.resume_data import load_resume_data
        load_resume_data(FileBrain(Path(brain_root)))
        add(Path('resume.json'))
    add(Path('perspectives.json'))
    add(Path('learning.json'))
    add(Path('podcast-artwork.json'))
    for source in sorted((root/'wiki').rglob('*.md')):
        relative = source.relative_to(root)
        if any(part.startswith('.') or part in ('raw','private') for part in relative.parts):
            continue
        add(relative)
    for source in sorted((root/'references'/'podcasts').glob('*.json')):
        if re.fullmatch(r'[0-9a-f]{64}\.json', source.name):
            if source.is_symlink():
                raise ValueError('Published source cannot contain symbolic links.')
            record = json.loads(source.read_text())
            if record.get('scope') != 'public' or record.get('knowledge_role') != 'external_reference':
                raise ValueError('Reference is not explicitly published.')
            if record.get('active'):
                add(source.relative_to(root))
    shelf = json.loads(entries['brain/public/bookshelf.json'])
    for book in shelf['books']:
        ident = book['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident):
            raise ValueError('Invalid published book ID.')
        for suffix in ('jpg','png','webp'):
            add(Path('bookshelf-assets')/(ident+'.'+suffix))
        if book.get('cover'):
            key = ident+'-'+hashlib.sha256(book['cover'].encode()).hexdigest()[:16]+'.jpg'
            add(Path('bookshelf-covers')/key)
    manifest = {name:hashlib.sha256(body).hexdigest() for name,body in entries.items()}
    entries['public-export.json'] = json.dumps({'version':1,'sha256':manifest},sort_keys=True).encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    tmp = destination.with_suffix('.tmp')
    with tarfile.open(tmp,'w:gz') as archive:
        for name,body in sorted(entries.items()):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o600
            archive.addfile(info,io.BytesIO(body))
    tmp.chmod(0o600)
    tmp.replace(destination)
    return {'files':len(manifest),'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brain-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(export_public(args.brain_root,args.output)))


if __name__=='__main__':main()
