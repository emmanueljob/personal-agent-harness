import {useEffect, useRef, useState} from 'react';

type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Agent = 'grokbot' | 'muse' | 'owner';
type Props = {api: Api; agent: Agent; context?: string; disabled?: boolean; threadId?: string | null; onThreadChange?: (id: string) => void};
type Turn = {id: string; text: string; reply: string | null; status: string};
type Thread = {id: string; agent: Agent; turns: Turn[]};
type Summary = {id: string; title: string | null};
const root = '/admin/conversations';
const done = ['completed', 'failed', 'blocked', 'delivery_rejected'];

export function AgentConversation(props: Props) {
  return <Conversation key={props.agent} {...props}/>;
}

function Conversation({api, agent, context = '', disabled = false, threadId, onThreadChange}: Props) {
  const [threads, setThreads] = useState<Summary[]>([]);
  const [thread, setThread] = useState<Thread | null>(null);
  const [reason, setReason] = useState('Checking connection…');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  let target = '';
  try {target = JSON.parse(context).card || '';} catch {target = context;}
  const draft = drafts[target] || '';
  const setDraft = (text: string) => setDrafts(old => ({...old, [target]: text}));
  const [sendingTarget, setSendingTarget] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [generation, refresh] = useState(0);
  const [selected, select] = useState(threadId || '');
  const [chooseNew, setChooseNew] = useState(false);
  const pending = useRef<{thread: string; text: string; context: string; request_id: string} | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => {mounted.current = false;}; }, []);
  useEffect(() => {select(threadId || ''); setChooseNew(false);}, [threadId]);

  useEffect(() => {
    let current = true;
    setBusy(true);
    setError('');
    const load = async () => {
      const [availability, list] = await Promise.all([api(root + '/agents'), api(root + '?agent=' + agent)]);
      const entry = availability.agents.find((a: {id: string}) => a.id === agent);
      const id = selected || (chooseNew ? '' : list.threads[0]?.id);
      const data = id ? await api(root + '/' + id) : null;
      if (!current) return;
      setReason(entry?.available ? '' : entry?.reason || 'Agent unavailable.');
      setThreads(list.threads);
      if (data && data.agent !== agent) throw new Error('This conversation belongs to another agent.');
      setThread(data);
    };
    load().catch(e => {if (current) {setThread(null); setError(e.message); setReason('Unable to load conversation.');}})
      .finally(() => {if (current) setBusy(false);});
    return () => {current = false;};
  }, [api, agent, selected, chooseNew, generation]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {await action();} catch (e) {if (mounted.current) setError((e as Error).message);}
    finally {if (mounted.current) {setBusy(false); setSendingTarget(null);}}
  }

  async function send() {
    if (disabled || busy) return;
    const originTarget = target;
    const originContext = context;
    const originDraft = draft;
    setSendingTarget(originTarget || 'Free-form question');
    const active: Thread = thread || await api(root, 'POST', {agent});
    if (!mounted.current) return;
    setThread(active);
    const previous = pending.current;
    const payload = previous?.thread === active.id && previous.text === originDraft && previous.context === originContext
      ? previous : {thread: active.id, text: originDraft, context: originContext, request_id: crypto.randomUUID()};
    pending.current = payload;
    const data = await api(root + '/' + active.id + '/messages', 'POST',
      {text: payload.text, context: payload.context, request_id: payload.request_id});
    if (!mounted.current) return;
    pending.current = null;
    setThread(data);
    setDrafts(old => ({...old, [originTarget]: ''}));
    select(active.id);
    onThreadChange?.(active.id);
    refresh(n => n + 1);
  }

  const last = thread?.turns.at(-1);
  useEffect(() => {
    if (agent !== 'owner' || !last || done.includes(last.status)) return;
    const timer = window.setInterval(() => refresh(n => n + 1), 2000);
    return () => window.clearInterval(timer);
  }, [agent, last?.status, last?.id]);
  return <section aria-label={agent === 'owner' ? 'Your agent conversation' : agent === 'muse' ? 'Muse conversation' : 'Grokbot conversation'}>
    <label>Conversation <select disabled={busy || disabled} value={thread?.id || ''} onChange={e => {
      const id = e.target.value;
      setThread(null); setDraft(''); select(id); setChooseNew(!id);
      if (id) onThreadChange?.(id);
    }}>
      <option value="">New conversation</option>
      {threads.map(t => <option key={t.id} value={t.id}>{t.title?.slice(0, 80) || 'Empty conversation'}</option>)}
    </select></label>
    <button type="button" disabled={busy || disabled} onClick={() => refresh(n => n + 1)}>Refresh</button>
    {reason && <p role="status">{reason}</p>}
    {sendingTarget && <p role="status">Sending about {sendingTarget}</p>}
    {error && <p role="alert">{error}</p>}
    {thread?.turns.map(t => <article key={t.id}>
      <p><strong>You</strong> {t.text}</p>
      <p><strong>{agent === 'owner' ? 'Your agent' : agent === 'muse' ? 'Muse' : 'Grokbot'}</strong> {t.reply || t.status.replaceAll('_', ' ')}</p>
      {t.reply && t.status !== 'completed' && <small>{t.status}</small>}
      {agent === 'grokbot' && ['sending', 'queued', 'delivery_unknown', 'delivery_rejected'].includes(t.status) &&
        <button type="button" disabled={busy || disabled} onClick={() => void run(async () => {
          setThread(await api(root + '/' + thread.id + '/turns/' + t.id + '/retry', 'POST'));
        })}>Retry delivery</button>}
    </article>)}
    <form onSubmit={e => {e.preventDefault(); if (!busy && !disabled) void run(send);}}>
      <label>Message<textarea maxLength={4000} value={draft} disabled={busy || disabled} onChange={e => setDraft(e.target.value)}/></label>
      <button disabled={busy || disabled || !!reason || !draft.trim() || (!!last && !done.includes(last.status))}>Send</button>
    </form>
  </section>;
}
