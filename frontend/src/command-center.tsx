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
  id: string; revision: number; kind: string; reviewed: boolean; title: string; company: string; companyId: string;
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

export function CommandCenter({api, onOpenRecord, onOpenMyCareer, returnCardId, renderChat, selectedCardId, onSelectCard}: Props) {
  const [cards, setCards] = useState<Card[]>([]), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [saving, setSaving] = useState('');
  const [selected, setSelected] = useState(selectedCardId || ''), [editing, setEditing] = useState('');
  const [agent, setAgent] = useState<'grokbot'|'muse'>('grokbot');
  const [scope, setScope] = useState<'all'|'completed'>('all');
  const [offset, setOffset] = useState(0), [nextOffset, setNextOffset] = useState<number|null>(null);
  const [generation, setGeneration] = useState(0);
  const restored = useRef(''), chat = useRef<HTMLHeadingElement>(null);
  const refresh = useCallback(() => {setLoading(true); setGeneration(g => g+1)}, []);
  useEffect(() => {
    let active = true;
    setError(''); setLoading(true);
    api(root+'?scope='+scope+'&offset='+offset).then(data => {
      if (active) {setCards(data.cards); setNextOffset(data.nextOffset)}
    })
      .catch(e => {if (active) {setError(e.message); setCards([])}})
      .finally(() => {if (active) setLoading(false)});
    return () => {active = false};
  }, [api, generation, scope, offset]);
  useEffect(() => {
    if (returnCardId && restored.current!==returnCardId && !loading) {
      const element = document.getElementById('cc-'+returnCardId);
      if (element) {element.focus(); restored.current=returnCardId}
    }
  }, [returnCardId, cards, loading]);

  async function complete(card: Card) {
    setSaving(card.id); setError('');
    try {await api(root+'/cards/'+encodeURIComponent(card.id)+(card.reviewed ? '/reopen' : '/complete'), 'POST', {revision: card.revision}); refresh()}
    catch (e) {setError((e as Error).message)}
    finally {setSaving('')}
  }
  useEffect(() => {setSelected(selectedCardId || '')}, [selectedCardId]);
  const selectCard = (id: string) => {setSelected(id); onSelectCard?.(id)};
  const changeScope = (next: 'all'|'completed') => {
    if (next===scope) return;
    setLoading(true); setCards([]); setEditing(''); setOffset(0); setScope(next);
  };
  const changePage = (next: number) => {setLoading(true); setCards([]); setEditing(''); setOffset(next)};
  const current = cards.find(c => c.id===selected);
  return <div className="command-center">
    <header className="cc-header"><h1>Command center</h1>
      <button onClick={onOpenMyCareer}>My Career</button></header>
    <div className="cc-layout"><section aria-label="Career activity">
      <div className="cc-toolbar"><h2>Career activity</h2><button disabled={loading} onClick={refresh}>Refresh feed</button></div>
      <div className="cc-actions" aria-label="Feed view">
        <button aria-pressed={scope==='all'} onClick={() => changeScope('all')}>All activity</button>
        <button aria-pressed={scope==='completed'} onClick={() => changeScope('completed')}>Completed steps</button>
      </div>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading career activity…</p>}
      {!loading && !cards.length && !error && <p>{scope==='completed' ? 'No completed next steps on this page.' : 'No career activity to show yet.'}</p>}
      {cards.map(card => <article className="cc-card" key={card.id} tabIndex={-1} id={'cc-'+card.id}>
        <small>{[card.company, card.person, card.date].filter(Boolean).join(' · ')}</small>
        <h3>{card.title}</h3><p>{card.why}</p>
        {card.action && <p><strong>Next step</strong> {card.action}</p>}
        <details><summary>Inspect evidence</summary><p>{card.source}</p>
          {card.evidence && <blockquote>{card.evidence}</blockquote>}
          {card.sourceUrl && <a href={card.sourceUrl} target="_blank" rel="noreferrer">Open original message</a>}
          <div className="cc-actions">{card.kind==='import' ? <button onClick={onOpenMyCareer}>Open My Career</button> : <button onClick={() => onOpenRecord(card.id, card.id)}>Open record</button>}
            {card.personId && <button onClick={() => onOpenRecord(card.personId, card.id)}>Inspect contact</button>}
            {card.receiptId && <button onClick={() => setEditing(card.id)}>Correct company or contact</button>}</div>
          {editing===card.id && <Correction api={api} card={card} cancel={() => setEditing('')}
            saved={() => {setEditing(''); refresh()}}/>}
        </details>
        <div className="cc-actions">
          {(card.action || card.reviewed) && <button className="admin-primary" disabled={!!saving || loading}
            onClick={() => void complete(card)}>{card.reviewed ? 'Reopen next step' : 'Mark next step done'}</button>}
          <button onClick={() => {selectCard(card.id); chat.current?.focus()}}>Ask about this</button>
        </div>
      </article>)}
      <div className="cc-actions">
        {offset>0 && <button disabled={loading} onClick={() => changePage(Math.max(0,offset-40))}>Previous page</button>}
        {nextOffset!==null && <button disabled={loading} onClick={() => changePage(nextOffset)}>Next page</button>}
      </div>
    </section><aside className="cc-chat">
      <h2 tabIndex={-1} ref={chat}>Chat</h2>
      <label>Agent<select value={agent} onChange={e => setAgent(e.target.value as typeof agent)}>
        <option value="grokbot">Grokbot</option><option value="muse">Muse</option></select></label>
      {current && <p>About <strong>{current.title}</strong> <button onClick={() => selectCard('')}>Clear card</button></p>}
      {renderChat(agent, JSON.stringify({surface: 'command-center', card: selected}))}
    </aside></div>
  </div>;
}
