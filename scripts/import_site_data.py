"""Validate detached site data; optionally import missing records without overwriting edits.

Use DATA_DIR outside the checkout. Run without --apply to validate only.
Publication to a hosted instance is a separate owner-reviewed operation.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from harness.personalization import Profile
from harness.portfolio import Portfolio
from harness.site_config import Settings, path


class SiteData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    settings: Settings
    profiles: list[Profile] = Field(default_factory=list)
    portfolio: Portfolio


def import_data(source, apply=False):
    source = Path(source).expanduser().resolve()
    if source.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Keep site data outside the source repository')
    data = SiteData.model_validate_json(source.read_text())
    keys = [p.key for p in data.profiles]
    if len(keys) != len(set(keys)) or any(p.key == 'default' and not p.enabled for p in data.profiles):
        raise ValueError('Profile keys must be unique and the default must be enabled')
    ids = [p.id for p in data.portfolio.projects]
    if len(ids) != len(set(ids)) or any(set(p.featured)-set(ids) for p in data.profiles):
        raise ValueError('Profiles must reference unique configured projects')
    entries = {'settings':data.settings.model_dump(), 'portfolio':data.portfolio.model_dump(),
               **{'profile:'+p.key:p.model_dump() for p in data.profiles}}
    if not apply:
        return {'validated':len(entries),'imported':0}
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(target,0o600)
    with sqlite3.connect(target,timeout=5) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS site_config (key TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL,updated REAL NOT NULL)')
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO site_config VALUES (?,?,1,?)',[(k,json.dumps(v),time.time()) for k,v in entries.items()])
        count = db.total_changes-before
    return {'validated':len(entries),'imported':count,'preserved':len(entries)-count}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(import_data(args.source,args.apply)))
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(1,'Site data validation/import failed; inspect the private input locally. No input content is printed.\n')
