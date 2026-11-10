import {useEffect, useState} from 'react';

type Api = (path:string, method?:string, body?:unknown) => Promise<any>;
type Provenance = {
  record_id:string; revision:number; origin:string; source_id:string|null;
  source_title:string; provider:string; reference:string; evidence:string; evidence_truncated:boolean;
};
type Activity = {
  id:string; revision:number; record:Record<string, string|string[]|null>;
  relationships:Record<string, string>; aliases:string[]; appears_in:string[];
  visibility:{hidden?:boolean}; provenance:Provenance[]; provenance_total:number;
};
type Pair = {
  members:Activity[]; assessment:string; matched_fields:string[]; missing_fields:string[];
  different_fields:string[]; import_identity_match:boolean;
  match_basis:{record_id:string; revision:number; origin:string; record:Activity['record']}[];
};
type Preview = {activities:number; pairs:Pair[]; next_offset:number|null};
const labels:Record<string,string> = {
  company_id:'Company', person_id:'Person', title:'Title', channel:'Channel', stage:'Stage',
  happened_on:'Date', application_id:'Application', link_id:'Personalized link', urls:'Source links',
};
function value(activity:Activity, field:string) {
  const raw = activity.record[field];
  if (Array.isArray(raw)) return raw.join('\n') || 'Not recorded';
  if (!raw) return 'Not recorded';
  const related = activity.relationships[field];
  return related ? related+' · '+raw : raw;
}
export function ActivityDuplicates({api, open}:{api:Api; open:(id:string)=>void}) {
  const [data, setData] = useState<Preview|null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    setBusy(true);
    void api('/admin/crm/activity-duplicates?offset='+offset).then(result => {
      if (alive) {setData(result); setError('');}
    }).catch(error => {if (alive) setError(error.message);})
      .finally(() => {if (alive) setBusy(false);});
    return () => {alive = false;};
  }, [api, offset, refresh]);
  return <section className="crm-duplicate-preview" aria-label="Possible duplicate activities">
    <div className="crm-section-title">
      <h2>Possible duplicates</h2>
      <button disabled={busy} onClick={() => {setOffset(0); setRefresh(current => current+1);}}>Refresh preview</button>
    </div>
    <p>Compare activity details and evidence before deciding whether two records describe the same event.</p>
    {error && <p role="alert">{error}</p>}
    {busy ? <p role="status">Comparing activities…</p> : data && <>
      <p>{data.activities} activities compared, including hidden activity. {data.pairs.length} pairs on this page.</p>
      {!data.pairs.length && <p>No possible duplicates found on this page.</p>}
      {data.pairs.map(pair => <article className="crm-surface" key={pair.members.map(member => member.id).join('|')}>
        <h3>{pair.assessment==='distinct_events' ? 'Different event details' : pair.assessment==='needs_review' ? 'Details need review' : 'Possible duplicate'}</h3>
        <p>Matching fields — {pair.matched_fields.map(field => labels[field]).join(', ')}.</p>
        {pair.different_fields.length>0 && <p>Different fields — {pair.different_fields.map(field => labels[field]).join(', ')}.</p>}
        {pair.missing_fields.length>0 && <p>Missing on one or both records — {pair.missing_fields.map(field => labels[field]).join(', ')}.</p>}
        {pair.import_identity_match && <p>The import identity comparison also matches this pair.</p>}
        {pair.match_basis.some(basis => basis.origin!=='current') && <p>The match uses a previous revision or redirected activity. The table shows the current records.</p>}
        <div className="crm-table-wrap">
          <table className="crm-record-table">
            <thead><tr><th>Field</th>{pair.members.map(activity => <th key={activity.id}>
              <button onClick={() => open(activity.id)}>Open activity</button><small>{activity.id} · Revision {activity.revision}</small>
            </th>)}</tr></thead>
            <tbody>{Object.keys(labels).map(field => <tr key={field}>
              <th scope="row">{labels[field]}</th>
              {pair.members.map(activity => <td key={activity.id}>{value(activity, field)}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
        {pair.members.map((activity, index) => {
          const basis = pair.match_basis[index];
          return <details key={activity.id}>
            <summary>Evidence for {String(activity.record.title)} · {activity.id}</summary>
            <p>Appears in {activity.appears_in.join(' and ')}. {activity.visibility.hidden ? 'Hidden from the routine career feed.' : ''}</p>
            {basis.origin!=='current' && <p>Matched through {basis.origin} record {basis.record_id}, revision {basis.revision}, titled “{String(basis.record.title)}”, dated {basis.record.happened_on || 'not recorded'}.</p>}
            {activity.aliases.length>0 && <p>Redirected activity IDs — {activity.aliases.join(', ')}.</p>}
            {activity.record.urls && (activity.record.urls as string[]).map(url => <p key={url}>
              {/^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noreferrer">{url}</a> : url}
            </p>)}
            {activity.provenance.map((source, sourceIndex) => <div key={sourceIndex} className="crm-source-item">
              <p>{source.origin} record {source.record_id} · Revision {source.revision}</p>
              {source.source_id && <p>{source.source_title || source.source_id} · {source.provider} · {source.reference}</p>}
              {source.evidence && <blockquote>{source.evidence}{source.evidence_truncated ? '…' : ''}</blockquote>}
            </div>)}
            {activity.provenance_total>activity.provenance.length && <p>Showing {activity.provenance.length} of {activity.provenance_total} evidence entries. Open the activity for its revision history.</p>}
          </details>;
        })}
      </article>)}
      <div className="admin-toolbar">
        {offset>0 && <button onClick={() => setOffset(Math.max(0, offset-20))}>Previous pairs</button>}
        {data.next_offset!==null && <button onClick={() => setOffset(data.next_offset!)}>Next pairs</button>}
      </div>
    </>}
  </section>;
}
