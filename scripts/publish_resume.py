"""Validate a reviewed resume; --publish places it in the external public scope."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.brain import FileBrain
from harness.paths import brain_directory
from harness.resume import MAX_BYTES,valid_pdf


def publish(source, root, apply=False, replace=False):
    source=Path(source).expanduser().resolve()
    repo=Path(__file__).resolve().parents[1]
    root=Path(root).expanduser().resolve()
    if source.is_relative_to(repo) or root.is_relative_to(repo):
        raise ValueError('Resume source and brain must stay outside the repository')
    if source.stat().st_size>MAX_BYTES:
        raise ValueError('Resume exceeds the PDF size limit')
    body=source.read_bytes()
    if not valid_pdf(body):raise ValueError('Expected a PDF no larger than 20 MiB')
    scope=FileBrain(root).scope('public')
    target=scope/'resume.pdf'
    if target.is_symlink():raise ValueError('Resume cannot be a symlink')
    if apply:
        if target.exists() and not replace:raise ValueError('Resume already exists; use --replace after review and backup')
        scope.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=scope/('.resume-'+uuid.uuid4().hex)
        try:
            fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as output:
                output.write(body);output.flush();os.fsync(output.fileno())
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)
    return {'published':apply,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--brain-root',type=Path,default=brain_directory())
    parser.add_argument('--publish',action='store_true',help='Confirm the reviewed PDF is intended for public download')
    parser.add_argument('--replace',action='store_true',help='Replace an existing PDF after review and backup')
    args=parser.parse_args()
    try:print(__import__('json').dumps(publish(args.source,args.brain_root,args.publish,args.replace)))
    except (ValueError,OSError) as error:parser.exit(1,str(error)+'\n')
