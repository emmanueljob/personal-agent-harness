import { useEffect, useState } from 'react';
import { GrokConnection } from './grok-connection';
import './job-crm.css';
type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Kind = 'company' | 'person' | 'application' | 'activity';
type RecordData = {
    kind: Kind;
    stage: string;
    notes: string;
    next_action: string;
    follow_up: string | null;
    urls: string[];
    [key: string]: any;
};
type Row = {
    id: string;
    kind: Kind;
    record: RecordData;
    revision: number;
    certainty: string;
    source_id: string | null;
    evidence: string;
    observed_on: string | null;
};
type Candidate = {
    key: string;
    record: RecordData;
    excerpt: string;
    accepted_id: string | null;
    certainty: string;
    matches: string[];
};
type Source = {
    id: string;
    title: string;
    provider: string;
    report: string;
    complete: boolean;
    observed_on: string | null;
    candidates: Candidate[];
};
type State = {
    records: Row[];
    sources: Source[];
    changes: any[];
    turns: any[];
    import_jobs?: any[];
    import_issues?: any[];
};
const stages: Record<Kind, string[]> = { company: ['interested', 'researching', 'pursuing', 'paused', 'closed'], person: ['suggested', 'to_contact', 'drafted', 'contacted', 'replied', 'connected', 'closed'], application: ['considering', 'preparing', 'applied', 'screen', 'interview', 'offer', 'rejected', 'withdrawn'], activity: ['proposed', 'drafted', 'sent', 'replied', 'completed', 'cancelled'] };
const label = (v: string) => v.replaceAll('_', ' ');
const name = (r: RecordData) => r.name || r.title || r.kind;
function blank(kind: Kind, company = ''): RecordData { return { kind, stage: stages[kind][0], notes: '', next_action: '', follow_up: null, urls: [], ...(kind === 'company' ? { name: '', domain: '', priority: 'medium', stats: {} } : { company_id: company }), ...(kind === 'person' ? { name: '', role: '', email: '' } : {}), ...(kind === 'application' ? { title: '', person_id: '', applied_on: null, resume_version: '', job_description: '' } : {}), ...(kind === 'activity' ? { title: '', person_id: '', application_id: '', channel: 'note', happened_on: null, link_id: '' } : {}) }; }
export function JobCRM({ api, dirty }: {
    api: Api;
    dirty: (v: boolean) => void;
}) {
    const [data, setData] = useState<State>({ records: [], sources: [], changes: [], turns: [] }), [view, setView] = useState('Today'), [company, setCompany] = useState(''), [query, setQuery] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [loaded, setLoaded] = useState(false), [draft, setDraft] = useState<{
        id?: string;
        revision: number;
        record: RecordData;
    } | null>(null), [changed, setChanged] = useState(false), [links, setLinks] = useState<any[]>([]);
    const mark = (v: boolean) => { setChanged(v); dirty(v); };
    const load = async () => { const [d, l] = await Promise.all([api('/admin/crm'), api('/admin/outreach')]); setData(d); setLinks(l.links); setLoaded(true); };
    useEffect(() => { void load().catch(e => setError(e.message)); }, [api]);
    const run = async (fn: () => Promise<unknown>) => { setBusy(true); setError(''); try {
        await fn();
        await load();
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setBusy(false);
    } };
    const leave = () => !changed || window.confirm('Discard the unsaved CRM form?');
    const navigate = (v: string) => { if (!leave())
        return; setView(v); setCompany(''); setDraft(null); mark(false); };
    const companies = data.records.filter(r => r.kind === 'company');
    const companyName = (id: string) => { const r = companies.find(r => r.id === id); return r ? name(r.record) : 'Unknown company'; };
    const edit = (row?: Row, kind: Kind = 'company') => { if (!leave())
        return; setDraft(row ? { id: row.id, revision: row.revision, record: structuredClone(row.record) } : { revision: 0, record: blank(kind, company) }); mark(false); };
    const open = (id: string) => { if (!leave())
        return; setCompany(id); setView('Companies'); setDraft(null); mark(false); };
    const filtered = data.records.filter(r => (!company || r.id === company || r.record.company_id === company) && (!query || JSON.stringify(r.record).toLowerCase().includes(query.toLowerCase())));
    const today = new Date().toLocaleDateString('en-CA');
    const due = data.records.filter(r => r.record.follow_up && r.record.follow_up <= today && !['closed', 'rejected', 'withdrawn', 'completed', 'cancelled'].includes(r.record.stage));
    const processing = (data.import_jobs || []).filter(j=>['queued','running'].includes(j.status)).length;
    const card = (r: Row) => <article className="crm-record" key={r.id}><div className="crm-card-top"><span className="crm-badge">{label(r.record.stage)}</span><small>{r.record.priority || label(r.certainty)}</small></div><button className="crm-title" onClick={() => r.kind === 'company' ? open(r.id) : edit(r)}>{name(r.record)}</button>{r.kind !== 'company' && <small>{companyName(r.record.company_id)}</small>}{r.record.role && <p>{r.record.role}</p>}{r.record.next_action && <p>{r.record.next_action}</p>}{r.record.follow_up && <small>Follow up {r.record.follow_up}</small>}<button onClick={() => edit(r)}>Edit {r.kind}</button></article>;
    const selected = companies.find(r => r.id === company);
    return <div className="crm-workspace"><p className="admin-intro">Companies, relationships and applications in one private workspace. Reports import automatically. Source evidence and uncertainty stay attached; edit any record to correct it.</p><nav className="crm-nav" aria-label="Job search views">{['Today', 'Companies', 'People', 'Applications', 'Activity', 'Imports', 'Agent desk'].map(v => <button aria-current={view === v ? 'page' : undefined} key={v} onClick={() => navigate(v)}>{v}{v === 'Imports' && processing ? ' · ' + processing : ''}</button>)}</nav>{error && <p role="alert" className="admin-notice">{error}</p>}<div className="admin-toolbar"><label>Search CRM<input value={query} onChange={e => setQuery(e.target.value)} placeholder="Company, person, role or note"/></label><button disabled={busy} onClick={() => void run(load)}>Refresh workspace</button>{(['company', 'person', 'application', 'activity'] as Kind[]).map(k => <button key={k} disabled={busy || (k !== 'company' && !companies.length)} onClick={() => edit(undefined, k)}>+ {k === 'person' ? 'Person' : k}</button>)}</div>{!loaded && !error && <p role="status">Loading workspace…</p>}
 {draft ? <Editor key={draft.id || draft.record.kind + company} value={draft.record} locked={!!draft.id} records={data.records} links={links} busy={busy} change={record => { setDraft({ ...draft, record }); mark(true); }} cancel={() => { if (leave()) {
            setDraft(null);
            mark(false);
        } }} save={() => void run(async () => { await api('/admin/crm/records' + (draft.id ? '/' + draft.id : ''), draft.id ? 'PUT' : 'POST', { revision: draft.revision, record: draft.record }); setDraft(null); mark(false); })}/> : <>
 {view === 'Today' && <><div className="admin-metrics">{[[companies.length, 'Companies'], [data.records.filter(r => r.kind === 'application' && ['applied', 'screen', 'interview', 'offer'].includes(r.record.stage)).length, 'Active applications'], [due.length, 'Follow-ups due'], [data.records.length, 'CRM records']].map(([n, t]) => <div className="admin-card" key={t}><strong>{n}</strong><span>{t}</span></div>)}</div><section className="admin-card"><h2>Needs attention</h2><div className="crm-grid">{due.map(card)}</div>{!due.length && <p>No follow-ups are due. Add next actions and dates to keep your search moving.</p>}</section><section className="admin-card"><h2>Your agents keep the CRM current</h2><p>Collect Grok reports or paste a Muse report into Imports. New reports are extracted in the background and added to your boards automatically.</p><button onClick={() => navigate('Imports')}>Open imports</button><button onClick={() => navigate('Agent desk')}>Talk to your coordinator</button></section></>}
 {view === 'Companies' && (selected ? <><button onClick={() => setCompany('')}>← Company board</button><section className="admin-card"><div className="admin-toolbar"><h2>{name(selected.record)}</h2><span className="crm-badge">{selected.record.stage}</span><button onClick={() => edit(selected)}>Edit company</button></div><p>{selected.record.domain} · {selected.record.priority} priority</p><dl className="crm-stats">{Object.entries(selected.record.stats || {}).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{String(v)}</dd></div>)}</dl><p className="crm-prewrap">{selected.record.notes || 'No research notes yet.'}</p>{selected.record.urls.map(url => <p key={url}><a target="_blank" rel="noreferrer" href={url}>{url}</a></p>)}<p>Next: {selected.record.next_action || 'Not set'} {selected.record.follow_up}</p>{selected.evidence && <details><summary>Original import evidence · {selected.certainty}</summary><blockquote>{selected.evidence}</blockquote><p>{data.sources.find(s => s.id === selected.source_id)?.title} · observed {selected.observed_on || 'date unknown'}</p></details>}</section>{(['person', 'application', 'activity'] as Kind[]).map(kind => <section className="admin-card" key={kind}><h2>{{ person: 'People', application: 'Applications', activity: 'Activity' }[kind as 'person' | 'application' | 'activity']}</h2><div className="crm-grid">{filtered.filter(r => r.kind === kind).map(card)}</div>{!filtered.some(r => r.kind === kind) && <p>No records yet.</p>}</section>)}<section className="admin-card"><h2>Personalized links</h2>{filtered.filter(r => r.record.link_id).map(r => { const l = links.find(l => l.id === r.record.link_id); return <p key={r.id}>{l ? '/hi/' + (l.slug || l.id) : 'Link removed'} · {name(r.record)}</p>; })}<p>Attach a link created in Outreach links to an activity. Visit counts remain in Visit reports and do not establish who visited.</p></section><details className="admin-card"><summary>Record history</summary>{data.changes.filter(c => filtered.some(r => r.id === c.record_id)).map(c => <p key={c.id}>{new Date(c.created * 1000).toLocaleString()} · {c.actor} · revision {c.revision}<span className="crm-evidence">{c.evidence}</span></p>)}</details></> : <><div className="crm-board">{stages.company.map(s => <section className="crm-column" key={s}><h2>{s} <small>{filtered.filter(r => r.kind === 'company' && r.record.stage === s).length}</small></h2>{filtered.filter(r => r.kind === 'company' && r.record.stage === s).map(card)}</section>)}</div>{!companies.length && <div className="admin-card"><h2>Your company board starts here</h2><p>Add a company or receive an agent report. Company interest is separate from each application’s hiring stage.</p><button onClick={() => edit()}>Add your first company</button></div>}</>)}
 {['People', 'Applications', 'Activity'].includes(view) && <section className="admin-card"><div className="admin-toolbar"><h2>{view}</h2><label>Company<select value={company} onChange={e => setCompany(e.target.value)}><option value="">All companies</option>{companies.map(r => <option key={r.id} value={r.id}>{name(r.record)}</option>)}</select></label></div><div className="crm-grid">{filtered.filter(r => r.kind === ({ People: 'person', Applications: 'application', Activity: 'activity' }[view])).map(card)}</div>{!filtered.some(r => r.kind === ({ People: 'person', Applications: 'application', Activity: 'activity' }[view])) && <p>No matching records yet.</p>}</section>}
 {view === 'Imports' && <Imports api={api} sources={data.sources} records={data.records} jobs={data.import_jobs || []} issues={data.import_issues || []} busy={busy} run={run} mark={mark} edit={id=>{const row=data.records.find(r=>r.id===id);if(row)edit(row)}}/>}
 {view === 'Agent desk' && <><Coordinator api={api} turns={data.turns} busy={busy} run={run}/><section className="admin-card"><h2>Muse</h2><p>The local WhatsApp MCP proof can send a synthetic test and verify Muse’s reply. The hosted CRM still needs its web-to-Mac transport and export capability.</p><p>Paste an existing Muse report into Imports for now. It retains Muse as its source and uses the same automatic import flow as Grok.</p></section><GrokConnection api={api}/></>}
 </>}</div>;
}
function Editor({ value, records, links, locked, busy, change, cancel, save }: {
    value: RecordData;
    records: Row[];
    links: any[];
    locked: boolean;
    busy: boolean;
    change: (v: RecordData) => void;
    cancel: () => void;
    save: () => void;
}) {
    const [stats, setStats] = useState(Object.entries(value.stats || {}).map(([k, v]) => k + ': ' + v).join('\n')), [urls, setUrls] = useState(value.urls.join('\n'));
    const patch = (k: string, v: any) => change({ ...value, [k]: v });
    const text = (k: string, title: string, multi = false) => <label className="admin-field" key={k}>{title}{multi ? <textarea rows={4} value={value[k] || ''} onChange={e => patch(k, e.target.value)}/> : <input value={value[k] || ''} required={['name', 'title'].includes(k)} onChange={e => patch(k, e.target.value)}/>}</label>;
    const date = (k: string, title: string) => <label className="admin-field">{title}<input type="date" value={value[k] || ''} onChange={e => patch(k, e.target.value || null)}/></label>;
    const relation = (k: string, kind: Kind, title: string) => <label className="admin-field">{title}<select disabled={k === 'company_id' && locked} required={k === 'company_id'} value={value[k] || ''} onChange={e => change({ ...value, [k]: e.target.value, ...(k === 'company_id' ? { person_id: '', application_id: '' } : {}) })}><option value="">{k === 'company_id' ? 'Choose company' : 'None'}</option>{records.filter(r => r.kind === kind && (kind === 'company' || r.record.company_id === value.company_id)).map(r => <option key={r.id} value={r.id}>{name(r.record)}</option>)}</select></label>;
    return <form className="admin-card" onSubmit={e => { e.preventDefault(); save(); }}><h2>{locked ? 'Edit' : 'Add'} {value.kind}</h2><div className="admin-fields-grid">{text(['company', 'person'].includes(value.kind) ? 'name' : 'title', 'Name / title')}{value.kind !== 'company' && relation('company_id', 'company', 'Company')}<label className="admin-field">Stage<select value={value.stage} onChange={e => patch('stage', e.target.value)}>{stages[value.kind].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>{value.kind === 'company' && <>{text('domain', 'Company domain')}<label className="admin-field">Priority<select value={value.priority} onChange={e => patch('priority', e.target.value)}>{['high', 'medium', 'low'].map(p => <option key={p}>{p}</option>)}</select></label></>}{value.kind === 'person' && <>{text('role', 'Role')}{text('email', 'Email')}</>}{['application', 'activity'].includes(value.kind) && relation('person_id', 'person', 'Related person')}{value.kind === 'application' && <>{text('resume_version', 'Résumé version')}{date('applied_on', 'Applied on')}</>}{value.kind === 'activity' && <>{relation('application_id', 'application', 'Related application')}<label className="admin-field">Channel<select value={value.channel} onChange={e => patch('channel', e.target.value)}>{['note', 'email', 'linkedin', 'x', 'whatsapp', 'call', 'interview', 'application', 'research'].map(c => <option key={c}>{c}</option>)}</select></label>{date('happened_on', 'Happened on')}<label className="admin-field">Personalized link<select value={value.link_id || ''} onChange={e => patch('link_id', e.target.value)}><option value="">None</option>{links.map(l => <option key={l.id} value={l.id}>{l.slug || l.labels.utm_content || l.labels.utm_campaign || l.id}</option>)}</select><small>Attach an existing link. This does not send it.</small></label></>}{text('next_action', 'Next action')}{date('follow_up', 'Follow-up date')}</div>{value.kind === 'company' && <label className="admin-field">Stats — one Label: value per line<textarea rows={4} value={stats} onChange={e => { setStats(e.target.value); const entries = e.target.value.split('\n').filter(l => l.includes(':')).map(l => { const i = l.indexOf(':'); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }); patch('stats', Object.fromEntries(entries)); }}/><small>Include sources and observation dates in your research notes.</small></label>}{text('notes', 'Research / notes', true)}{value.kind === 'application' && text('job_description', 'Job description', true)}<label className="admin-field">Source URLs — one per line<textarea rows={3} value={urls} onChange={e => { setUrls(e.target.value); patch('urls', e.target.value.split('\n').map(l => l.trim()).filter(Boolean)); }}/></label><div className="admin-toolbar"><button className="admin-primary" disabled={busy}>Save private record</button><button type="button" onClick={cancel}>Cancel</button></div></form>;
}
function Imports({api,sources,records,jobs,issues,busy,run,mark,edit}:{api:Api;sources:Source[];records:Row[];jobs:any[];issues:any[];busy:boolean;run:(fn:()=>Promise<unknown>)=>Promise<void>;mark:(v:boolean)=>void;edit:(id:string)=>void}) {
 const [provider,setProvider]=useState('muse'),[title,setTitle]=useState(''),[report,setReport]=useState(''),[observed,setObserved]=useState(''),[complete,setComplete]=useState(false),[reference,setReference]=useState(crypto.randomUUID()),[json,setJson]=useState<Record<string,string>>({});
 return <><section className="admin-card"><h2>Automatic imports are on</h2><p>Reports are extracted in the background and go straight into the CRM. Clear matches merge automatically. Your edits take precedence, and original reports remain available.</p><p>{jobs.filter(j=>['queued','running'].includes(j.status)).length} extraction jobs in progress · {jobs.filter(j=>j.status==='completed').length} completed · {jobs.filter(j=>j.status==='waiting_for_model').length} waiting for the configured model</p><div className="admin-toolbar"><button disabled={busy} onClick={()=>void run(()=>api('/admin/crm/sources/collect-grok','POST'))}>Collect existing Grok reports</button><button disabled={busy} onClick={()=>void run(()=>api('/admin/crm/imports/run','POST'))}>Process saved reports</button></div><details><summary>Add a Muse or other report</summary><form onChange={()=>mark(true)} onSubmit={e=>{e.preventDefault();void run(async()=>{await api('/admin/crm/sources','POST',{provider,reference,title,report,observed_on:observed||null,complete});setTitle('');setReport('');setReference(crypto.randomUUID());mark(false)})}}><label className="admin-field">Source<select value={provider} onChange={e=>setProvider(e.target.value)}>{['muse','grokbot','owner','other'].map(p=><option key={p}>{p}</option>)}</select></label><label className="admin-field">Report title<input required maxLength={300} value={title} onChange={e=>setTitle(e.target.value)}/></label><label className="admin-field">Observed on<input type="date" value={observed} onChange={e=>setObserved(e.target.value)}/></label><label className="admin-field">Original report<textarea required maxLength={60000} rows={8} value={report} onChange={e=>setReport(e.target.value)}/></label><label className="admin-check"><input type="checkbox" checked={complete} onChange={e=>setComplete(e.target.checked)}/>Source explicitly confirmed the export is complete</label><button className="admin-primary" disabled={busy}>Import report</button></form></details></section>
 {issues.length>0&&<details className="admin-card"><summary>{issues.length} data gaps recorded</summary><p>Other records continue importing. The original reports retain these details.</p>{issues.map(i=><p key={i.source_id+i.key}>{i.key}: {i.message}</p>)}</details>}
 {jobs.some(j=>j.status==='failed')&&<section className="admin-card"><h2>Some extraction jobs could not finish</h2><p>Imported records remain available. Processing saved reports retries failed jobs up to twice after a model or service issue.</p></section>}
 {sources.map(source=><section className="admin-card" key={source.id}><h2>{source.title}</h2><p>{source.provider} · {source.complete?'Source reports a complete export':'Partial / completeness unverified'} · observed {source.observed_on||'date unknown'}</p><p>{source.candidates.filter(c=>c.accepted_id).length} records imported · {jobs.filter(j=>j.source_id===source.id&&['queued','running'].includes(j.status)).length} extraction jobs in progress</p><details><summary>Original report</summary><pre className="crm-report">{source.report}</pre></details><details><summary>Imported records and their evidence</summary>{source.candidates.map(c=><article className="crm-proposal" key={c.key}><strong>{name(c.record)}</strong> · {c.record.kind} · {label(c.record.stage)} · {c.certainty}<blockquote>{c.excerpt}</blockquote>{c.accepted_id&&records.some(r=>r.id===c.accepted_id)&&<button onClick={()=>edit(c.accepted_id!)}>Edit CRM record</button>}<details><summary>Imported fields</summary><pre className="crm-report">{JSON.stringify(c.record,null,2)}</pre></details></article>)}</details><details><summary>Advanced: import structured records</summary><p>Records import immediately with supporting source excerpts. Use $key for related records in the same source.</p><textarea aria-label={'Structured records for '+source.title} rows={6} value={json[source.id]||''} onChange={e=>setJson({...json,[source.id]:e.target.value})}/><button disabled={busy} onClick={()=>void run(()=>api('/admin/crm/sources/'+source.id+'/propose','POST',JSON.parse(json[source.id]||'{}')))}>Import records</button></details></section>)}
 {!sources.length&&<p>No reports received yet. New reports will import automatically.</p>}</>;
}
function Coordinator({ api, turns, busy, run }: {
    api: Api;
    turns: any[];
    busy: boolean;
    run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
    const [prompt, setPrompt] = useState(''), [model, setModel] = useState('default'), [requestId, setRequestId] = useState(crypto.randomUUID());
    return <section className="admin-card"><h2>Your job-search coordinator</h2><p>Ask about your CRM records, request Grok’s existing knowledge, or import a report into your boards. Runs use the existing harness worker and durable queue. Refresh the workspace to see completed turns.</p><div className="crm-chat" aria-live="polite">{turns.map(t => <article key={t.id}><strong>You</strong><p className="crm-prewrap">{t.prompt}</p><strong>Coordinator · {t.status}</strong><p className="crm-prewrap">{t.result || t.error || 'Waiting for the harness worker…'}</p>{t.status === 'failed' && <button disabled={busy} onClick={() => void run(() => api('/admin/crm/chat/' + t.id + '/retry', 'POST'))}>Retry failed turn</button>}</article>)}</div><form onSubmit={e => { e.preventDefault(); void run(async () => { await api('/admin/crm/chat', 'POST', { request_id: requestId, prompt, model_choice: model }); setPrompt(''); setRequestId(crypto.randomUUID()); }); }}><label className="admin-field">Message<textarea required maxLength={8000} rows={3} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Which companies need a follow-up?"/></label><div className="admin-toolbar"><label>Model<select value={model} onChange={e => setModel(e.target.value)}><option value="default">Configured default</option><option value="openai">OpenAI</option><option value="qwen">Mac / Qwen</option></select></label><button className="admin-primary" disabled={busy || !prompt.trim()}>Send to coordinator</button></div></form></section>;
}
