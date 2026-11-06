"""Validate resume JSON; explicitly publish reviewed HTML content and format settings."""
import argparse,hashlib,json,os,sys,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.brain import FileBrain
from harness.paths import brain_directory
from harness.resume_data import ResumeData
from harness.resume import _resume_bytes


def publish(source,root,apply=False,replace=False):
    source=Path(source).expanduser().resolve();root=Path(root).expanduser().resolve()
    repo=Path(__file__).resolve().parents[1]
    if source.is_relative_to(repo) or root.is_relative_to(repo):raise ValueError('Resume data must remain outside source')
    if source.stat().st_size>256000:raise ValueError('Resume data exceeds the size limit')
    data=ResumeData.model_validate_json(source.read_bytes());brain=FileBrain(root)
    if data.enabled and data.formats.html and data.source_pdf_sha256:
        pdf=_resume_bytes(brain)
        if pdf is None or hashlib.sha256(pdf).hexdigest()!=data.source_pdf_sha256:raise ValueError('HTML data must match the published source PDF')
    body=json.dumps(data.model_dump(),ensure_ascii=False,indent=2).encode()
    scope=brain.scope('public');target=scope/'resume.json'
    if target.is_symlink():raise ValueError('Resume data cannot be a symlink')
    if apply:
        if target.exists() and not replace:raise ValueError('Data already exists; back up and use --replace after review')
        scope.mkdir(parents=True,exist_ok=True,mode=0o700);temp=scope/('.resume-'+uuid.uuid4().hex)
        try:
            fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)
    return {'published':apply,'sections':len(data.sections),'formats':data.formats.model_dump(),'sha256':hashlib.sha256(body).hexdigest()}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--brain-root',type=Path,default=brain_directory());p.add_argument('--publish',action='store_true');p.add_argument('--replace',action='store_true')
    args=p.parse_args()
    try:print(json.dumps(publish(args.source,args.brain_root,args.publish,args.replace)))
    except (ValueError,OSError):p.exit(1,'Resume data validation/publication failed; review the private input locally.\n')
