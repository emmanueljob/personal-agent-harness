import {useEffect, useRef, useState} from 'react';

type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Agent = 'grokbot' | 'muse';
type Props = {api: Api; agent: Agent; context?: string; threadId?: string | null; onThreadChange?: (id: string) => void};
type Turn = {id: string; text: string; reply: string | null; status: string};
type Thread = {id: string; agent: Agent; turns: Turn[]};
type Summary = {id: string; title: string | null};
const root = '/admin/conversations';
const done = ['completed', 'failed', 'blocked', 'delivery_rejected'];

export function AgentConversation(props: Props) {
  return <Conversation key={props.agent} {...props}/>;
}

function Conversation({api, agent, context = '', threadId, onThreadChange}: Props) {
  const [threads, setThreads] = useState<Summary[]>([]);
  const [thread, setThread] = useState<Thread | null>(null);
  const [reason, setReason] = useState('Checking connection…');
  const [draft, setDraft] = useState('');
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
    finally {if (mounted.current) setBusy(false);}
  }

  async function send() {
    const active: Thread = thread || await api(root, 'POST', {agent});
    if (!mounted.current) return;
    setThread(active);
    const previous = pending.current;
    const payload = previous?.thread === active.id && previous.text === draft && previous.context === context
      ? previous : {thread: active.id, text: draft, context, request_id: crypto.randomUUID()};
    pending.current = payload;
    const data = await api(root + '/' + active.id + '/messages', 'POST',
      {text: payload.text, context: payload.context, request_id: payload.request_id});
    if (!mounted.current) return;
    pending.current = null;
    setThread(data);
    setDraft('');
    select(active.id);
    onThreadChange?.(active.id);
    refresh(n => n + 1);
  }

  const last = thread?.turns.at(-1);
  return <section aria-label={agent === 'muse' ? 'Muse conversation' : 'Grokbot conversation'}>
    <label>Conversation <select disabled={busy} value={thread?.id || ''} onChange={e => {
      const id = e.target.value;
      setThread(null); setDraft(''); select(id); setChooseNew(!id);
      if (id) onThreadChange?.(id);
    }}>
      <option value="">New conversation</option>
      {threads.map(t => <option key={t.id} value={t.id}>{t.title?.slice(0, 80) || 'Empty conversation'}</option>)}
    </select></label>
    <button type="button" disabled={busy} onClick={() => refresh(n => n + 1)}>Refresh</button>
    {reason && <p role="status">{reason}</p>}
    {error && <p role="alert">{error}</p>}
    {thread?.turns.map(t => <article key={t.id}>
      <p><strong>You</strong> {t.text}</p>
      <p><strong>{agent === 'muse' ? 'Muse' : 'Grokbot'}</strong> {t.reply || t.status.replaceAll('_', ' ')}</p>
      {t.reply && t.status !== 'completed' && <small>{t.status}</small>}
      {agent === 'grokbot' && ['sending', 'queued', 'delivery_unknown', 'delivery_rejected'].includes(t.status) &&
        <button type="button" disabled={busy} onClick={() => void run(async () => {
          setThread(await api(root + '/' + thread.id + '/turns/' + t.id + '/retry', 'POST'));
        })}>Retry delivery</button>}
    </article>)}
    <form onSubmit={e => {e.preventDefault(); if (!busy) void run(send);}}>
      <label>Message<textarea maxLength={4000} value={draft} disabled={busy} onChange={e => setDraft(e.target.value)}/></label>
      <button disabled={busy || !!reason || !draft.trim() || (!!last && !done.includes(last.status))}>Send</button>
    </form>
  </section>;
}
