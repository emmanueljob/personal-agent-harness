import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import './command-center.css';

type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Props = {
  api: Api;
  onOpenRecord: (recordId: string, cardId: string) => void;
  onOpenMyCareer: () => void;
  returnCardId?: string;
  selectedCardId?: string;
  onSelectCard?: (id: string) => void;
  renderChat: (agent: 'grokbot'|'muse', context: string) => ReactNode;
};
type Card = {
  id: string; revision: number; disposition: 'active'|'done'|'later'|'dismissed'; dispositionVersion: number; resumeAt: string|null; kind: string; reviewed: boolean; title: string; company: string; companyId: string;
  person: string; personId: string; why: string; evidence: string; source: string;
  sourceUrl: string; action: string; date: string; stage: string; receiptId: string | null;
};
type Contact = {id: string; kind: string; company_id: string; name: string};
const root = '/admin/command-center';

function Correction({api, card, saved, cancel}: {
  api: Api; card: Card; saved: () => void; cancel: () => void;
}) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [company, setCompany] = useState(card.companyId), [person, setPerson] = useState(card.personId);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api(root+'/contacts').then(data => {if (active) setContacts(data)})
      .catch(e => {if (active) setError(e.message)});
    return () => {active = false};
  }, [api]);
  async function save() {
    setBusy(true); setError('');
    try {
      await api('/admin/crm/correspondence/messages/'+encodeURIComponent(card.receiptId!)+'/link',
        'POST', {company_id: company, person_id: person});
      saved();
    } catch (e) {setError((e as Error).message); setBusy(false)}
  }
  return <form onSubmit={e => {e.preventDefault(); void save()}}>
    <label>Company<select value={company} onChange={e => {setCompany(e.target.value); setPerson('')}}>
      {contacts.filter(c => c.kind==='company').map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select></label>
    <label>Contact<select value={person} onChange={e => setPerson(e.target.value)}>
      <option value="">No linked contact</option>
      {contacts.filter(c => c.kind==='person' && c.company_id===company)
        .map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select></label>
    {error && <p role="alert">{error}</p>}
    <div className="cc-actions"><button className="admin-primary" disabled={busy || !contacts.length}>Save correction</button>
      <button type="button" disabled={busy} onClick={cancel}>Cancel</button></div>
  </form>;
}

type Execution = {
  kind: string; label: string; supported: boolean; reason: string;
  revision: number; dispositionVersion: number;
  result: null | {id: string; kind: string; revision: number; dispositionVersion: number;
    status: string; summary: string; scope: string; draftId?: string; draftText?: string; briefText?: string};
};

function CardExecution({api, card, busy, onBusy, open}: {
  api: Api; card: Card; busy: boolean; onBusy: (busy: boolean) => void;
  open: (recordId: string, cardId: string) => void;
}) {
  const [execution, setExecution] = useState<Execution>(), [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const pending = useRef<{revision: number; dispositionVersion: number; request_id: string; kind: string} | undefined>(undefined);
  const inFlight = useRef(false), mounted = useRef(true);
  useEffect(() => {mounted.current=true; return () => {mounted.current=false}}, []);
  useEffect(() => {
    let active=true;
    api(root+'/cards/'+encodeURIComponent(card.id)+'/execution').then(data => {if (active) {setExecution(data); setError('')}})
      .catch(e => {if (active) setError(e.message)});
    return () => {active=false};
  }, [api, card.id, reload]);
  async function execute() {
    if (!execution || inFlight.current) return;
    inFlight.current=true; onBusy(true); setError('');
    const body=pending.current || {revision: execution.revision, dispositionVersion: execution.dispositionVersion,
      request_id: crypto.randomUUID(), kind: execution.kind};
    pending.current=body;
    try {
      const data=await api(root+'/cards/'+encodeURIComponent(card.id)+'/execute','POST',body);
      if (mounted.current) setExecution(data);
      pending.current=undefined;
    } catch (e) {if (mounted.current) setError((e as Error).message)}
    finally {inFlight.current=false; onBusy(false)}
  }
  const stale = execution && (execution.revision!==card.revision || execution.dispositionVersion!==card.dispositionVersion);
  const alreadyExecuted = execution?.result && execution.result.revision===execution.revision
    && execution.result.dispositionVersion===execution.dispositionVersion;
  return <section aria-label="Agent action">
    {error && <p role="alert">{error} <button disabled={busy} onClick={() => {pending.current=undefined; setReload(n => n+1)}}>Refresh action</button></p>}
    {!execution && !error && <p role="status">Checking available action…</p>}
    {execution && <>
      <p>{execution.label}</p>
      {stale && <p role="alert">This card changed. Refresh the feed before asking the agent to act.</p>}
      {execution.reason && <p>{execution.reason}</p>}
      <button className="admin-primary" disabled={busy || !execution.supported || !!alreadyExecuted || !!stale}
        onClick={() => void execute()}>Do it</button>
      {execution.result && <details open><summary>Agent result</summary>
        <p>{execution.result.summary}</p><p>{execution.result.scope}</p>
        <small>Record revision {execution.result.revision} · decision version {execution.result.dispositionVersion}</small>
        {execution.result.draftText && <pre className="cc-result">{execution.result.draftText}</pre>}
        {execution.result.briefText && <pre className="cc-result">{execution.result.briefText}</pre>}
        {execution.result.draftId && <button onClick={() => open(execution.result!.draftId!,card.id)}>Open saved draft</button>}
      </details>}
    </>}
  </section>;
}

export function CommandCenter({api, onOpenRecord, onOpenMyCareer, returnCardId, renderChat, selectedCardId, onSelectCard}: Props) {
  const [cards, setCards] = useState<Card[]>([]), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState(selectedCardId || returnCardId || ''), [editing, setEditing] = useState('');
  const [detached, setDetached] = useState<Card>();
  const [agent, setAgent] = useState<'grokbot'|'muse'>('grokbot');
  const [scope, setScope] = useState('review'), [freeChat, setFreeChat] = useState(false);
  const [offset, setOffset] = useState(0), [nextOffset, setNextOffset] = useState<number|null>(null);
  const [generation, setGeneration] = useState(0), [notice, setNotice] = useState('');
  const pending = useRef<{id: string; body: unknown; state: string} | undefined>(undefined);
  const inFlight = useRef(false);
  const cardElement = useRef<HTMLElement>(null);
  const refresh = useCallback(() => {pending.current=undefined; setLoading(true); setGeneration(g => g+1)}, []);
  useEffect(() => {setSelected(selectedCardId || returnCardId || '')}, [selectedCardId, returnCardId]);
  useEffect(() => {
    let active = true;
    setError(''); setLoading(true); setDetached(undefined);
    async function load() {
      const data = await api(root+'?scope='+scope+'&offset='+offset);
      let extra: Card | undefined;
      if (selected && !data.cards.some((c: Card) => c.id===selected)) {
        extra = await api(root+'/cards/'+encodeURIComponent(selected));
      }
      if (active) {setCards(data.cards); setDetached(extra); setNextOffset(data.nextOffset)}
    }
    load().catch(e => {if (active) {setError(e.message); setCards([])}})
      .finally(() => {if (active) setLoading(false)});
    return () => {active = false};
  }, [api, generation, scope, offset, selected]);
  const current = cards.find(c => c.id===selected) || detached || (!selected ? cards[0] : undefined);
  useEffect(() => {
    if (!loading && current) {
      cardElement.current?.focus();
      if (!selected) {setSelected(current.id); onSelectCard?.(current.id)}
    }
  }, [loading, current?.id, selected, onSelectCard]);
  const selectCard = (id: string) => {setLoading(true); setEditing(''); setSelected(id); onSelectCard?.(id)};
  const move = (direction: number) => {
    const index = cards.findIndex(c => c.id===current?.id);
    const next = cards[index+direction];
    if (next) selectCard(next.id);
    else if (direction>0 && nextOffset!==null) {setOffset(nextOffset); selectCard('')}
    else if (direction<0 && offset>0) {setOffset(Math.max(0,offset-40)); selectCard('')}
  };
  const changeScope = (next: string) => {
    if (next===scope) return;
    setScope(next); setOffset(0); selectCard('');
  };
  async function decide(state: string) {
    if (!current || inFlight.current) return;
    inFlight.current=true; setSaving(true); setError('');
    const previous = pending.current;
    const body = previous?.id===current.id && previous.state===state ? previous.body : {
      revision: current.revision, disposition_version: current.dispositionVersion,
      state, request_id: crypto.randomUUID(),
    };
    pending.current={id: current.id, state, body};
    try {
      const result = await api(root+'/cards/'+encodeURIComponent(current.id)+'/disposition','POST',body);
      pending.current=undefined;
      setNotice(state==='later' ? 'Returns '+new Date(result.resumeAt).toLocaleString() :
        state==='dismissed' ? 'Dismissed. Restore it from Won’t do.' :
        state==='done' ? 'Marked done. You can reopen it in Completed steps.' : 'Restored to suggestions.');
      const index=cards.findIndex(c => c.id===current.id);
      selectCard(cards[index+1]?.id || cards[index-1]?.id || '');
      refresh();
    } catch (e) {setError((e as Error).message)}
    finally {inFlight.current=false; setSaving(false)}
  }
  const index = cards.findIndex(c => c.id===current?.id);
  const target = freeChat ? '' : current?.id || selected;
  return <div className="command-center">
    <header className="cc-header"><h1>Command center</h1><button onClick={onOpenMyCareer}>My Career</button></header>
    <section aria-label="Review suggestions">
      <div className="cc-toolbar">
        <label>View<select value={scope} disabled={saving} onChange={e => changeScope(e.target.value)}>
          <option value="review">Suggestions</option><option value="all">All activity</option>
          <option value="completed">Completed steps</option><option value="later">Later</option>
          <option value="dismissed">Won’t do</option>
        </select></label>
        <button disabled={loading || saving} onClick={refresh}>Refresh feed</button>
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">Loading suggestions…</p>}
      {!loading && !current && !error && <p>No cards in this view.</p>}
      {!loading && current && <article ref={cardElement} className="cc-card" tabIndex={-1} id={'cc-'+current.id}>
        <small>{[current.company, current.person, current.date].filter(Boolean).join(' · ')}</small>
        <h2>{current.title}</h2><p>{current.why}</p>
        {current.action && <p><strong>Suggested next step</strong> {current.action}</p>}
        {current.disposition==='later' && <p>Returns {new Date(current.resumeAt!).toLocaleString()}</p>}
        {current.disposition==='dismissed' && <p>You chose not to do this.</p>}
        <details><summary>Inspect evidence</summary><p>{current.source}</p>
          {current.evidence && <blockquote>{current.evidence}</blockquote>}
          {current.sourceUrl && <a href={current.sourceUrl} target="_blank" rel="noreferrer">Open original message</a>}
          <div className="cc-actions">{current.kind==='import' ? <button onClick={onOpenMyCareer}>Open My Career</button> :
            <button onClick={() => onOpenRecord(current.id,current.id)}>Open record</button>}
            {current.personId && <button onClick={() => onOpenRecord(current.personId,current.id)}>Inspect contact</button>}
            {current.receiptId && <button disabled={saving} onClick={() => setEditing(current.id)}>Correct company or contact</button>}
          </div>
          {editing===current.id && <Correction api={api} card={current} cancel={() => setEditing('')}
            saved={() => {setEditing(''); refresh()}}/>}
        </details>
        <CardExecution key={current.id+':'+current.revision+':'+current.dispositionVersion}
          api={api} card={current} busy={saving} onBusy={setSaving} open={onOpenRecord}/>
        <div className="cc-actions">
          {current.disposition==='active' && current.action && <>
            <button disabled={saving} onClick={() => void decide('done')}>Done myself</button>
            <button disabled={saving} onClick={() => void decide('later')}>Later · tomorrow</button>
            <button disabled={saving} onClick={() => void decide('dismissed')}>Won’t do</button>
          </>}
          {current.disposition!=='active' && <button disabled={saving} onClick={() => void decide('active')}>
            {current.reviewed ? 'Reopen next step' : 'Restore suggestion'}</button>}
        </div>
      </article>}
      <nav className="cc-actions" aria-label="Card navigation">
        <button disabled={loading || saving || (index<=0 && offset===0)} onClick={() => move(-1)}>Previous</button>
        <button disabled={loading || saving || (index>=cards.length-1 && nextOffset===null)} onClick={() => move(1)}>Next</button>
      </nav>
    </section>
    <section className="cc-chat" aria-label="Chat">
      <h2>{freeChat ? 'Free chat' : current ? 'Chat about '+current.title : 'Chat'}</h2>
      <div className="cc-actions">
        <button aria-pressed={!freeChat} disabled={!current || loading} onClick={() => setFreeChat(false)}>Current card</button>
        <button aria-pressed={freeChat} onClick={() => setFreeChat(true)}>Free chat</button>
      </div>
      <label>Agent<select value={agent} onChange={e => setAgent(e.target.value as typeof agent)}>
        <option value="grokbot">Grokbot</option><option value="muse">Muse</option></select></label>
      {freeChat ? <p>Using current app evidence.</p> : current && <p>Sending about {current.title} · {current.company || 'My Career'}</p>}
      {(freeChat || (!loading && current)) && <div key={agent+':'+target}>
        {renderChat(agent, JSON.stringify({surface:'command-center',card:target,
          ...(!freeChat && current ? {revision:current.revision,dispositionVersion:current.dispositionVersion} : {})}))}
      </div>}
    </section>
  </div>;
}
