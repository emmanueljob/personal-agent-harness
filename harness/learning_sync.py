"""Three-way note synchronization, independent of inference and private source data."""
import hashlib
import json


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def merge_snapshots(base, local, remote):
    result = {}
    for scope in ('public','private'):
        result[scope] = {}
        before,here,there = (s.get(scope,{}) for s in (base,local,remote))
        for ident in before.keys() | here.keys() | there.keys():
            b,l,r = (s.get(ident) for s in (before,here,there))
            if l == r or r == b:
                chosen = l
            elif l == b:
                chosen = r
            else:
                raise ValueError('The same learning note changed in both studios. Reconcile it before deploying.')
            if chosen is not None:
                result[scope][ident] = chosen
    return result
