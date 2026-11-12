import {useEffect, useRef, useState} from 'react';

type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Agent = 'grokbot' | 'muse' | 'owner';
type Props = {api: Api; agent: Agent; context?: string; disabled?: boolean; threadId?: string | null; onThreadChange?: (id: string) => void};
type HelperTask = {id: string; helper: string; status: string; reason: string; report: string | null; deliveryVerified: boolean; role?: string};
type HelperRequest = {id: string; status: string; tasks: HelperTask[]; answerStatus: string | null; answer: string | null};
type Turn = {id: string; text: string; reply: string | null; status: string; target?: string; delegations?: HelperRequest[]};
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
  let target = '', targetLabel = 'Free-form question';
  try {
    const scope = JSON.parse(context);
    target = scope.card || '';
    targetLabel = scope.title || target || 'Free-form question';
  } catch {target = context; targetLabel = context || targetLabel;}
  const draft = drafts[target] || '';
  const setDraft = (text: string) => setDrafts(old => ({...old, [target]: text}));
  const [sendingTarget, setSendingTarget] = useState<string | null>(null);
  const [error, setError] = useState(''), [pollError, setPollError] = useState('');
  const [busy, setBusy] = useState(false);
  const foregroundBusy = useRef(false);
  foregroundBusy.current=busy;
  const [generation, refresh] = useState(0);
  const [selected, select] = useState(threadId || '');
  const [chooseNew, setChooseNew] = useState(false);
  const pending = useRef<{thread: string; text: string; context: string; request_id: string} | null>(null);
  const mounted = useRef(true);
  const historyElement = useRef<HTMLDivElement>(null), followHistory = useRef(true);
  const sendLock = useRef(false), composing = useRef(false);
  useEffect(() => { mounted.current = true; return () => {mounted.current = false;}; }, []);
  useEffect(() => {select(threadId || ''); setChooseNew(false);}, [threadId]);

  useEffect(() => {
    let current = true;
    setBusy(true);
    setError(''); setPollError('');
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
    setSendingTarget(targetLabel);
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
  const helperPending = !!thread?.turns.some(t => t.delegations?.some(job =>
    job.tasks.some(task => !['completed', 'failed', 'blocked', 'unavailable', 'delivery_rejected'].includes(task.status))
    || job.answerStatus === 'queued' || job.answerStatus === 'running'
    || (job.answerStatus === null && t.status === 'completed')));
  const canSend = !busy && !disabled && !reason && !!draft.trim()
    && (!last || done.includes(last.status));
  useEffect(() => {
    if (agent==='owner' && followHistory.current && historyElement.current) {
      historyElement.current.scrollTop=historyElement.current.scrollHeight;
    }
  }, [agent,last?.id,last?.status,last?.reply,sendingTarget,error,pollError,reason]);

  useEffect(() => {
    if (agent !== 'owner' || !thread || !last || (done.includes(last.status) && !helperPending)) return;
    let current = true, polling = false;
    const poll = async () => {
      if (polling || foregroundBusy.current || sendLock.current) return;
      polling = true;
      try {
        const data: Thread = await api(root+'/'+thread.id);
        if (!current || foregroundBusy.current || sendLock.current) return;
        if (data.id!==thread.id || data.agent!==agent) throw new Error('This response belongs to another conversation.');
        setThread(previous => {
          const latest = previous?.turns.at(-1);
          return previous?.id===thread.id && latest?.id===last.id && latest?.status===last.status ? data : previous;
        });
        setPollError('');
      } catch (e) {
        if (current && !foregroundBusy.current && !sendLock.current) setPollError('Could not refresh the reply. '+(e as Error).message);
      } finally {polling = false}
    };
    const timer = window.setInterval(() => {void poll()}, 2000);
    return () => {current = false; window.clearInterval(timer)};
  }, [api, agent, thread?.id, last?.status, last?.id, helperPending]);
  const history = <>
    {reason && <p role="status">{reason}</p>}
    {sendingTarget && <p role="status">{agent === 'owner' ? 'Sending…' : 'Sending about '+sendingTarget}</p>}
    {error && <p role="alert">{error}</p>}
    {pollError && <p role="alert">{pollError}</p>}
    {thread?.turns.map(t => <article key={t.id}>
      {agent !== 'owner' && t.target && <small>About {t.target}</small>}
      <p><strong>You</strong> {t.text}</p>
      <p><strong>{agent === 'owner' ? 'Your agent' : agent === 'muse' ? 'Muse' : 'Grokbot'}</strong> {t.reply || t.status.replaceAll('_', ' ')}</p>
      {t.reply && t.status !== 'completed' && <small>{t.status}</small>}
      {t.delegations?.map(job => <section key={job.id} aria-label="Helper request">
        <p>{job.status === 'completed' ? 'Helper reports received' : 'Helper request · '+job.status}</p>
        {job.tasks.map(task => <div key={task.id}>
          <p>{task.helper === 'muse' ? 'Muse' : 'Grokbot'} · {task.status.replaceAll('_', ' ')}{task.role === 'sender' ? ' · Assigned sender' : task.role === 'draft_review' ? ' · Draft/review only' : ''}</p>
          {task.reason && <p>{task.reason}</p>}
          {task.report && <details><summary>Helper report · delivery unverified</summary>
            <p style={{whiteSpace: 'pre-wrap'}}>{task.report}</p></details>}
        </div>)}
        {job.answerStatus === 'failed' && <p role="alert">The personal agent could not compose the results. The helper reports are saved.</p>}
        {(job.answerStatus === 'failed' || job.tasks.some(task =>
          task.status === 'unavailable' || (task.helper === 'grokbot' && ['delivery_unknown', 'delivery_rejected', 'sending'].includes(task.status)))) &&
        <button type="button" disabled={busy || disabled} onClick={() => void run(async () => {
          await api('/admin/helper-requests/'+job.id+'/retry', 'POST');
          setThread(await api(root+'/'+thread!.id));
        })}>Retry available work</button>}
      </section>)}

      {((agent === 'grokbot' && ['sending', 'queued', 'delivery_unknown', 'delivery_rejected'].includes(t.status)) || (agent === 'owner' && t.status === 'failed')) &&
        <button type="button" disabled={busy || disabled} onClick={() => void run(async () => {
          setThread(await api(root + '/' + thread.id + '/turns/' + t.id + '/retry', 'POST'));
        })}>{agent === 'owner' ? 'Retry response' : 'Retry delivery'}</button>}
    </article>)}
  </>;
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
    {agent==='owner' ? <div className="cc-owner-history" role="log" aria-label="Conversation history"
      aria-live="polite" aria-relevant="additions text" tabIndex={0} ref={historyElement}
      onScroll={event => {
        const element=event.currentTarget;
        followHistory.current=element.scrollHeight-element.scrollTop-element.clientHeight<36;
      }}>{history}</div> : history}
    <form onSubmit={e => {
      e.preventDefault();
      if (!canSend || sendLock.current) return;
      sendLock.current = true;
      void run(send).finally(() => {sendLock.current = false;});
    }}>
      <label>Message<textarea aria-label="Message" maxLength={4000} value={draft} disabled={busy || disabled} onChange={e => setDraft(e.target.value)}
        onCompositionStart={() => {composing.current = true;}}
        onCompositionEnd={() => {composing.current = false;}}
        onKeyDown={e => {
          if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing || composing.current || e.keyCode === 229) return;
          e.preventDefault();
          if (canSend) e.currentTarget.form?.requestSubmit();
        }}/></label>
      <button disabled={!canSend}>Send</button>
    </form>
  </section>;
}
