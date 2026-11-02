"""Preview or apply a JSONL external-reference import. Private, dry-run defaults."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from harness.brain import FileBrain
from harness.ingestion import normalize, upsert_batch, withdraw_sources
from harness.paths import brain_directory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path)
    source.add_argument('--withdraw-source-id',action='append')
    parser.add_argument('--collection',required=True)
    parser.add_argument('--format',choices=['reference','podcast'],default='reference')
    parser.add_argument('--scope',choices=['private','public'],default='private')
    parser.add_argument('--brain-dir',type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--allow-public',action='store_true')
    parser.add_argument('--sync-index',action='store_true')
    args=parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    root=(args.brain_dir or brain_directory()).expanduser().resolve()
    checkout=Path(__file__).resolve().parents[1]
    if root.is_relative_to(checkout):raise ValueError('Knowledge must be stored outside the source checkout.')
    if args.sync_index and not args.apply:raise ValueError('--sync-index requires --apply.')
    if args.withdraw_source_id:
        stats=withdraw_sources(FileBrain(root),args.withdraw_source_id,args.collection,args.scope,args.apply,args.allow_public)
    else:
        if args.input.stat().st_size>128_000_000:raise ValueError('Input batch exceeds the size limit.')
        records=[]
        with args.input.open(encoding='utf-8') as file:
            for number,line in enumerate(file,1):
                if not line.strip():continue
                if len(line.encode())>3_000_000:raise ValueError(f'Input line {number} exceeds the size limit.')
                try:records.append(normalize(json.loads(line),args.collection,args.scope,args.format))
                except (ValueError,TypeError,KeyError,AttributeError):raise ValueError(f'Invalid source record at line {number}; source contents suppressed.') from None
        stats=upsert_batch(FileBrain(root),records,args.collection,args.scope,args.apply,args.allow_public)
    stats['indexed']=False
    if args.sync_index:
        from harness.gbrain import GBrain
        stats['indexed_documents']=GBrain(root).sync(args.scope)
        stats['indexed']=True
    print(json.dumps(stats,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        # Paths, input text and provider diagnostics may all be private.
        print('Import did not complete. Check JSONL/schema, external data paths and index setup. Valid records may already be applied if indexing or I/O failed; rerunning is idempotent.',file=sys.stderr)
        raise SystemExit(1)
