import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import './command-center.css';
import {AnimatePresence, useReducedMotion} from 'motion/react';
import {CommandCard} from './command-card';
import type {ReviewScope} from './admin-route';

type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Props = {
  api: Api;
  onOpenRecord: (recordId: string, cardId: string) => void;
  onOpenMyCareer: () => void;
  returnCardId?: string;
  selectedCardId?: string;
  onSelectCard?: (id: string) => void;
  reviewScope?: ReviewScope;
  reviewOffset?: number;
  onReviewNavigate?: (location: {scope: ReviewScope; offset: number; card: string}) => void;
  renderChat: (agent: 'owner', context: string, disabled: boolean) => ReactNode;
};
type Card = {
  id: string; intentRevision?: string; revision: number; disposition: 'active'|'done'|'later'|'dismissed'; dispositionVersion: number; resumeAt: string|null; kind: string; reviewed: boolean; title: string; company: string; companyId: string;
  person: string; personId: string; why: string; evidence: string; source: string;
  sourceUrl: string; action: string; date: string; stage: string; receiptId: string | null;
};
type Contact = {id: string; kind: string; company_id: string; name: string};
const root = '/admin/command-center';

function Correction({api, card, saved, cancel, onBusy}: {
  api: Api; card: Card; saved: () => void; cancel: () => void; onBusy: (busy: boolean) => void;
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
    if (busy) return;
    setBusy(true); onBusy(true); setError('');
    try {
      await api('/admin/crm/correspondence/messages/'+encodeURIComponent(card.receiptId!)+'/link',
        'POST', {company_id: company, person_id: person});
      saved();
    } catch (e) {setError((e as Error).message); setBusy(false)}
    finally {onBusy(false)}
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
  kind: string; label: string; buttonLabel: string; supported: boolean; reason: string; resultCurrent: boolean; intentRevision?: string;
  revision: number; dispositionVersion: number;
  result: null | {id: string; kind: string; revision: number; dispositionVersion: number;
    status: string; summary: string; scope: string; helperRequestId?: string;
    delegation?: {status: string; tasks: {id: string; helper: string; status: string; reason: string; report?: unknown}[]}; draftId?: string; draftText?: string; briefText?: string};
};

function CardExecution({api, card, busy, onBusy, open, actions, refreshCard}: {
  api: Api; card: Card; busy: boolean; onBusy: (busy: boolean) => void; actions: ReactNode;
  open: (recordId: string, cardId: string) => void; refreshCard: () => void;
}) {
  const [execution, setExecution] = useState<Execution>(), [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const pending = useRef<{revision: number; dispositionVersion: number; request_id: string; kind: string; intentRevision?: string} | undefined>(undefined);
  const inFlight = useRef(false), mounted = useRef(true);
  useEffect(() => {mounted.current=true; return () => {mounted.current=false}}, []);
  useEffect(() => {
    let active=true;
    api(root+'/cards/'+encodeURIComponent(card.id)+'/execution').then(data => {if (active) {setExecution(data); setError('')}})
      .catch(e => {if (active) setError(e.message)});
    return () => {active=false};
  }, [api, card.id, card.revision, card.dispositionVersion, card.intentRevision, reload]);
  useEffect(() => {
    const status = execution?.result?.delegation?.status;
    if (!status || !['pending','partial','unknown'].includes(status)) return;
    const timer = window.setTimeout(() => setReload(n => n+1), 3000);
    return () => window.clearTimeout(timer);
  }, [execution]);
  async function retryHelpers() {
    const id = execution?.result?.helperRequestId;
    if (!id || inFlight.current) return;
    inFlight.current=true; onBusy(true); setError('');
    try {await api('/admin/helper-requests/'+encodeURIComponent(id)+'/retry','POST',{}); setReload(n => n+1)}
    catch (e) {if (mounted.current) setError((e as Error).message)}
    finally {inFlight.current=false; onBusy(false)}
  }
  async function execute() {
    if (!execution || inFlight.current) return;
    inFlight.current=true; onBusy(true); setError('');
    const body=pending.current || {revision: execution.revision, dispositionVersion: execution.dispositionVersion,
      request_id: crypto.randomUUID(), kind: execution.kind, intentRevision: execution.intentRevision};
    pending.current=body;
    try {
      const data=await api(root+'/cards/'+encodeURIComponent(card.id)+'/execute','POST',body);
      if (mounted.current) setExecution(data);
      pending.current=undefined;
    } catch (e) {if (mounted.current) setError((e as Error).message)}
    finally {inFlight.current=false; onBusy(false)}
  }
  const stale = execution && (execution.revision!==card.revision || execution.dispositionVersion!==card.dispositionVersion
    || execution.intentRevision!==card.intentRevision && !!card.intentRevision);
  const resultPanel = useRef<HTMLDetailsElement>(null);
  const retry = () => {pending.current=undefined; setExecution(undefined); setError(''); setReload(n => n+1)};
  const primaryLabel = stale ? 'Refresh suggestion' : !execution ? (error ? 'Retry action' : 'Checking action…')
    : execution.resultCurrent ? 'View result' : !execution.supported ? 'Open record' : execution.buttonLabel;
  function primaryAction() {
    if (stale) refreshCard();
    else if (!execution) retry();
    else if (execution.resultCurrent) {if (resultPanel.current) {resultPanel.current.open=true; resultPanel.current.focus()}}
    else if (!execution.supported) open(card.id,card.id);
    else void execute();
  }
  return <section className="cc-execution" aria-label="Agent action">
    <div className="cc-execution-content" tabIndex={0} aria-label="Agent action and result">
    {error && <p role="alert">{error} <button disabled={busy} onClick={retry}>Refresh action</button></p>}
    {!execution && !error && <p role="status">Checking available action…</p>}
    {execution && <>
      {execution.label!=='Do it' && execution.label.toLowerCase()!==card.action.toLowerCase() &&
        <p className="cc-operation">{execution.label}</p>}
      {stale && <p role="alert">This card changed. Refresh the feed before asking the agent to act.</p>}
      {execution.reason && <p>{execution.reason}</p>}
      {execution.result && <details ref={resultPanel} tabIndex={-1} open><summary>{execution.resultCurrent ? 'Agent result' : 'Earlier result'}</summary>
        <p>{execution.result.summary}</p><p>{execution.result.scope}</p>
        {execution.result.delegation && <div aria-live="polite">
          <p>Research request: {execution.result.delegation.status}</p>
          {execution.result.delegation.tasks.map(task => <div key={task.id}>
            <p>{task.helper}: {task.status}{task.reason ? ' · '+task.reason : ''}</p>
            {!!task.report && <pre className="cc-result">{typeof task.report==='string' ? task.report : JSON.stringify(task.report,null,2)}</pre>}
          </div>)}
          {['unavailable','failed','unknown','partial'].includes(execution.result.delegation.status) &&
            <button disabled={busy} onClick={() => void retryHelpers()}>Retry helper request</button>}
        </div>}
        <small>Record revision {execution.result.revision} · decision version {execution.result.dispositionVersion}</small>
        {execution.result.draftText && <pre className="cc-result">{execution.result.draftText}</pre>}
        {execution.result.briefText && <pre className="cc-result">{execution.result.briefText}</pre>}
        {execution.result.draftId && <button onClick={() => open(execution.result!.draftId!,card.id)}>Open saved draft</button>}
      </details>}
    </>}
    </div>
    <div className="cc-decision-bar" aria-label="Suggestion actions">
      {card.disposition==='active' && card.action && <button className="admin-primary"
        disabled={busy || !execution && !error}
        onClick={primaryAction}>{primaryLabel}</button>}
      {actions}
    </div>
  </section>;
}

export function CommandCenter({api, onOpenRecord, onOpenMyCareer, returnCardId, renderChat, selectedCardId, onSelectCard, reviewScope, reviewOffset, onReviewNavigate}: Props) {
  const [cards, setCards] = useState<Card[]>([]), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState(selectedCardId || returnCardId || ''), [editing, setEditing] = useState('');
  const [detached, setDetached] = useState<Card>();
  const reducedMotion = useReducedMotion();
  const [direction, setDirection] = useState(1), [animating, setAnimating] = useState(false);
  const previousCard = useRef<Card | undefined>(undefined);
  const enteringCard = useRef('');
  const [scope, setScope] = useState<ReviewScope>(reviewScope || 'review'), [freeChat, setFreeChat] = useState(false);
  const [offset, setOffset] = useState(reviewOffset || 0), [nextOffset, setNextOffset] = useState<number|null>(null);
  const [generation, setGeneration] = useState(0), [notice, setNotice] = useState('');
  const pending = useRef<{id: string; body: unknown; state: string} | undefined>(undefined);
  const inFlight = useRef(false);
  const cardElement = useRef<HTMLElement>(null);
  const selectLast = useRef(false);
  const refresh = useCallback(() => {pending.current=undefined; setLoading(true); setGeneration(g => g+1)}, []);
  useEffect(() => {
    const nextCard = selectedCardId || returnCardId || '';
    const nextScope = reviewScope || 'review', nextOffset = reviewOffset || 0;
    if (nextCard!==selected || nextScope!==scope || nextOffset!==offset) {
      setLoading(true); setSelected(nextCard); setScope(nextScope); setOffset(nextOffset);
      setEditing('');
    }
  }, [selectedCardId, returnCardId, reviewScope, reviewOffset]);
  const notifyLocation = (id: string, nextScope = scope, nextOffset = offset) => {
    if (onReviewNavigate) onReviewNavigate({scope:nextScope,offset:nextOffset,card:id});
    else onSelectCard?.(id);
  };
  useEffect(() => {
    let active = true;
    setError(''); setLoading(true); setDetached(undefined);
    async function load() {
      const data = await api(root+'?scope='+scope+'&offset='+offset);
      let extra: Card | undefined;
      let missingSelected = false;
      if (selected && !data.cards.some((c: Card) => c.id===selected)) {
        try {extra = await api(root+'/cards/'+encodeURIComponent(selected))}
        catch (e) {if ((e as Error & {status?: number}).status===404) missingSelected=true; else throw e}
      }
      if (active) {
        setCards(data.cards); setDetached(extra); setNextOffset(data.nextOffset);
        const resolvedId = (!missingSelected && selected) || data.cards[selectLast.current ? data.cards.length-1 : 0]?.id || '';
        if (resolvedId===previousCard.current?.id) setAnimating(false);
        if (!selected || missingSelected) {
          const nextId = data.cards[selectLast.current ? data.cards.length-1 : 0]?.id || '';
          setSelected(nextId);
          notifyLocation(nextId);
        }
        selectLast.current=false;
      }
    }
    load().catch(e => {if (active) {setError(e.message); setCards([])}})
      .finally(() => {if (active) setLoading(false)});
    return () => {active = false};
  }, [api, generation, scope, offset, selected]);
  const current = cards.find(c => c.id===selected) || detached || (!selected ? cards[0] : undefined);
  const visibleCard = loading ? previousCard.current : current;
  enteringCard.current=visibleCard?.id || '';
  useEffect(() => {
    if (!loading) {
      previousCard.current=current;
      if (!current) setAnimating(false);
    }

  }, [loading, animating, current]);
  const entered = (id: string) => {if (enteringCard.current===id) setAnimating(false)};
  const selectCard = (id: string, nextScope = scope, nextOffset = offset) => {
    setAnimating(!reducedMotion); setLoading(true); setEditing(''); setSelected(id); setScope(nextScope); setOffset(nextOffset);
    if (id) notifyLocation(id, nextScope, nextOffset);
  };
  const move = (direction: number) => {
    setDirection(direction);
    const index = cards.findIndex(c => c.id===current?.id);
    const next = cards[index+direction];
    if (next) selectCard(next.id);
    else if (direction>0 && nextOffset!==null) {selectCard('', scope, nextOffset)}
    else if (direction<0 && offset>0) {selectLast.current=true; selectCard('', scope, Math.max(0,offset-40))}
  };
  const changeScope = (next: ReviewScope) => {
    if (next===scope) return;
    setDirection(1); selectLast.current=false; selectCard('', next, 0);
  };
  async function decide(state: string) {
    if (!current || inFlight.current) return;
    inFlight.current=true; setSaving(true); setError(''); setDirection(1);
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
  const transitioning = loading || animating;
  return <div className="command-center">
    <header className="cc-header"><h1>Command center</h1><button onClick={onOpenMyCareer}>My Career</button></header>
    <section aria-label="Review suggestions">
      <div className="cc-toolbar">
        <label>View<select value={scope} disabled={saving} onChange={e => changeScope(e.target.value as ReviewScope)}>
          <option value="review">Suggestions</option><option value="all">All activity</option>
          <option value="completed">Completed steps</option><option value="later">Later</option>
          <option value="dismissed">Won’t do</option>
        </select></label>
        <button disabled={transitioning || saving} onClick={refresh}>Refresh feed</button>
      </div>
      <div className="cc-feedback">
        {error && <p role="alert">{error}</p>}
        {!error && notice && <p role="status">{notice}</p>}
      </div>
      <div className="cc-card-stack" aria-busy={transitioning}>
        <div className="cc-stack-layer" aria-hidden="true"/>
        <div className="cc-card-stage">
          {!visibleCard && <p className="cc-empty" role="status">{loading ? 'Loading suggestions…' : error ? 'Unable to load this suggestion.' : 'No cards in this view.'}</p>}
          <AnimatePresence initial={false} mode="wait" custom={direction}>
          {visibleCard && <CommandCard key={visibleCard.id} id={visibleCard.id} direction={direction}
            cardRef={cardElement} locked={transitioning || saving} onEntered={entered}
            onEntering={() => setAnimating(true)}>
            <div className="cc-card-content" tabIndex={0} aria-label="Suggestion details">
              <p className="cc-card-context">{[visibleCard.company, visibleCard.person, visibleCard.date].filter(Boolean).join(' · ')}</p>
              <h2 id={'cc-title-'+visibleCard.id}>{visibleCard.title}</h2><p className="cc-reason">{visibleCard.why}</p>
              {visibleCard.action && <div className="cc-next-step"><span>Suggested next step</span><p>{visibleCard.action}</p></div>}
              {visibleCard.disposition==='later' && <p>Returns {new Date(visibleCard.resumeAt!).toLocaleString()}</p>}
              {visibleCard.disposition==='dismissed' && <p>You chose not to do this.</p>}
              {visibleCard.evidence && <figure className="cc-evidence">
                <blockquote>{visibleCard.evidence}</blockquote>
                <figcaption>{visibleCard.source}</figcaption>
              </figure>}
              <details><summary>Inspect evidence</summary>
                {visibleCard.evidence && <blockquote>{visibleCard.evidence}</blockquote>}
                {visibleCard.sourceUrl && <a href={visibleCard.sourceUrl} target="_blank" rel="noreferrer">Open original message</a>}
                <div className="cc-actions">{visibleCard.kind==='import' ? <button onClick={onOpenMyCareer}>Open My Career</button> :
                  <button onClick={() => onOpenRecord(visibleCard.id,visibleCard.id)}>Open record</button>}
                  {visibleCard.personId && <button onClick={() => onOpenRecord(visibleCard.personId,visibleCard.id)}>Inspect contact</button>}
                  {visibleCard.receiptId && <button disabled={saving} onClick={() => setEditing(visibleCard.id)}>Correct company or contact</button>}
                </div>
                {editing===visibleCard.id && <Correction api={api} card={visibleCard} cancel={() => setEditing('')}
                  saved={() => {setEditing(''); refresh()}} onBusy={setSaving}/>}
              </details>
            </div>
            <CardExecution key={visibleCard.id+':'+visibleCard.revision+':'+visibleCard.dispositionVersion+':'+(visibleCard.intentRevision || '')}
              api={api} card={visibleCard} busy={saving || transitioning} onBusy={setSaving} open={onOpenRecord} refreshCard={refresh}
              actions={<>
                {visibleCard.disposition==='active' && visibleCard.action && <>
                  <button disabled={saving} onClick={() => void decide('done')}>Done myself</button>
                  <button disabled={saving} onClick={() => void decide('later')}>Later · tomorrow</button>
                  <button disabled={saving} onClick={() => void decide('dismissed')}>Won’t do</button>
                </>}
                {visibleCard.disposition!=='active' && <button disabled={saving} onClick={() => void decide('active')}>
                  {visibleCard.reviewed ? 'Reopen next step' : 'Restore suggestion'}</button>}
              </>}/>
          </CommandCard>}
          </AnimatePresence>
        </div>
      </div>
      <nav className="cc-actions" aria-label="Card navigation">
        <button disabled={transitioning || saving || (index<=0 && offset===0)} onClick={() => move(-1)}><span aria-hidden="true">← </span>Previous</button>
        <button disabled={transitioning || saving || (index>=cards.length-1 && nextOffset===null)} onClick={() => move(1)}>Next<span aria-hidden="true"> →</span></button>
      </nav>
    </section>
    <section className="cc-chat" aria-label="Chat">
      <div className="cc-chat-heading">
        <h2>{freeChat ? 'Ask your agent' : current ? 'Discuss this suggestion' : 'Ask your agent'}</h2>
        <div className="cc-chat-modes">
          <button aria-pressed={!freeChat} disabled={!current || transitioning} onClick={() => setFreeChat(false)}>Current card</button>
          <button aria-pressed={freeChat} onClick={() => setFreeChat(true)}>Free chat</button>
        </div>
      </div>
      <p className="cc-chat-target" aria-live="polite">{freeChat ? 'Using current app evidence.' :
        current ? current.title+' · '+(current.company || 'My Career') : 'Select a suggestion or use free chat.'}</p>
      <div className="cc-chat-body">
        {renderChat('owner', JSON.stringify({surface:'command-center',card:target,
          ...(!freeChat && current ? {revision:current.revision,dispositionVersion:current.dispositionVersion,title:current.title} : {})}),
          transitioning || saving || (!freeChat && !current))}
      </div>
    </section>
  </div>;
}
