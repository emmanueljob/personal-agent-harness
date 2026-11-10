import {CareerConversation} from './career-conversation';
import {CareerSummary} from './career-summary';
import { useCallback, useEffect, useState } from 'react';

type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Evidence = {statement: string; source: string; quote: string; date?: string};
type Claim = Evidence & {history: Evidence[]};
type Job = {id: string; title: string; company: string; pending: boolean; disposition: string | null;
  previous: string | null; preference: string; reason: string};
type View = {running: boolean; error: string; revision: number; claims: Claim[]; jobs: Job[]};
const endpoint = '/admin/crm/career';
const labels: Record<string,string> = {ruled_out: 'Ruled out', research: 'Needs research', pursue: 'Worth pursuing'};

function Source({entry}: {entry: Evidence}) {
  return <><small>{entry.source}{entry.date && ' · '+entry.date}</small><blockquote>{entry.quote}</blockquote></>;
}

export function MyCareer({api, open, mark}: {api: Api; open: (id: string) => void; mark: (dirty: boolean) => void}) {
  const [view, setView] = useState<View>();
  const [editing, setEditing] = useState<{index: number | null; revision: number; text: string}>();
  const [error, setError] = useState(''), [busy, setBusy] = useState(''), [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    async function load() {
      setBusy('Loading');
      try {
        let data: View = await api(endpoint);
        if (active) setView(data);
        if (!data.revision && active) {
          setBusy('Reading career sources');
          await api(endpoint+'/discover','POST');
          data = await api(endpoint);
        }
        if (active && data.claims.length && data.jobs.some(job => job.pending) && !data.running) {
          await api(endpoint+'/review','POST');
          data = await api(endpoint);
        }
        while (active) {
          setView(data);
          if (data.error) throw new Error(data.error);
          if (!data.running) break;
          setBusy('Reviewing jobs');
          await new Promise(resolve => setTimeout(resolve, 1500));
          if (!active) return;
          data = await api(endpoint);
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setBusy('');
      }
    }
    void load();
    return () => {active = false};
  }, [api, reload]);

  const retry = useCallback(() => {setError(''); setReload(n => n+1)}, []);
  const start = (index: number | null) => {
    if (view) setEditing({index, revision:view.revision, text:index === null ? '' : view.claims[index].statement});
  };
  const cancel = () => {setEditing(undefined); mark(false)};
  async function save() {
    if (!editing) return;
    setBusy('Saving'); setError('');
    try {
      await api(endpoint,'PUT',{revision:editing.revision,index:editing.index,statement:editing.text.trim()});
      cancel(); retry();
    } catch (e) {setBusy(''); setError((e as Error).message)}
  }
  const editor = editing && <form onSubmit={e => {e.preventDefault(); void save()}}>
    <label className="admin-field">What should your agent know?
      <textarea rows={3} maxLength={600} value={editing.text} autoFocus
        onChange={e => {setEditing({...editing,text:e.target.value}); mark(true)}}/>
    </label>
    <button className="admin-primary" disabled={!editing.text.trim() || busy==='Saving'}>Save and reconsider jobs</button>
    <button type="button" disabled={busy==='Saving'} onClick={cancel}>Cancel</button>
  </form>;
  const reconsidered = (job: Job) => !job.pending && job.previous==='ruled_out' && job.disposition!=='ruled_out';
  const jobs = [...(view?.jobs || [])].sort((a,b) => Number(reconsidered(b))-Number(reconsidered(a)));
  return <>
    <CareerConversation api={api}/>
    {error && <p role="alert">{error} <button onClick={retry}>Retry</button></p>}
    {busy && <p role="status">{busy}…</p>}
    {view && <><CareerSummary api={api} revision={view.revision} updated={retry}/><section className="crm-surface crm-career-section"><h2>What I’m looking for</h2>
      {!view.claims.length && <p>Add a preference in your own words or check your saved sources. <button disabled={!!busy} onClick={async () => {try {setBusy('Reading career sources'); await api(endpoint+'/discover','POST'); retry()} catch(e) {setError((e as Error).message); setBusy('')}}}>Check sources</button></p>}
      {view.claims.map((claim,i) => <article key={i}>
        <p><strong>{claim.statement}</strong></p>
        <details><summary>Source and corrections</summary><Source entry={claim}/>
          {claim.history.slice().reverse().map((old,n) => <div key={n}><p>Previously: {old.statement}</p><Source entry={old}/></div>)}
        </details>
        {editing?.index===i ? editor : <button disabled={!!editing} onClick={() => start(i)}>Correct preference</button>}
      </article>)}
      {editing?.index===null ? editor : <button disabled={!!editing} onClick={() => start(null)}>Add preference</button>}
    </section><section className="crm-surface crm-career-section"><h2>Job fit</h2>
      {!jobs.length && <p>No roles are currently being considered or prepared.</p>}
      {jobs.map(job => <article key={job.id}><h3><button className="crm-record-name" onClick={() => open(job.id)}>{job.title}</button></h3>
        <small>{job.company} · {job.pending ? 'Awaiting review' : labels[job.disposition!]}</small>
        {reconsidered(job) && <p><strong>Previously ruled out</strong></p>}
        {!job.pending && <><p>{job.reason}</p><small>Based on “{job.preference}”</small></>}
      </article>)}
    </section></>}
  </>;
}
