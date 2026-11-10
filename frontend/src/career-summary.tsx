import {useEffect, useState} from 'react';

type Api = (path:string, method?:string, body?:unknown) => Promise<any>;
type Task = {task_id:string; question:string; status:string};
type Narrative = {summary:string; questions:string[]};
type Provider = 'grokbot' | 'muse';
type Exchange = {id:string; question:string; answer:string|null; status:string; created:number; updated:number; reviewed:boolean};
const statusText:Record<string,string> = {
  queued:'Ready to send', sending:'Sending question', delivered:'Webhook received the question. Waiting for Grokbot to acknowledge.',
  acknowledged:'Grokbot acknowledged the question. Waiting for its reply.', completed:'Answer received',
  blocked:'Grokbot reported a blocker', failed:'Grokbot reported a failure', delivery_unknown:'Delivery could not be confirmed',
  delivery_rejected:'Webhook declined delivery', handoff:'Prepared for WhatsApp. Paste the reply when it arrives.'
};
type Progress = {pending:string[]; tasks:Task[]};
const base = '/admin/crm/career';
const waiting = ['delivered','acknowledged','sending'];

export function CareerSummary({api,revision,updated}:{api:Api;revision:number;updated:()=>void}) {
  const [brief,setBrief] = useState<Narrative>(), [summaryError,setSummaryError] = useState('');
  const [provider,setProvider] = useState<Provider>('grokbot');
  const [messages,setMessages] = useState<Exchange[]>([]), [more,setMore] = useState(false), [limit,setLimit] = useState(10);
  const [epoch,setEpoch] = useState(0);
  const [question,setQuestion] = useState(''), [answer,setAnswer] = useState('');
  const [muse,setMuse] = useState(false), [notice,setNotice] = useState(''), [error,setError] = useState('');
  const [busy,setBusy] = useState(false), [requestId,setRequestId] = useState<string>(()=>crypto.randomUUID());
  const [summaryRetry,setSummaryRetry] = useState(0);

  useEffect(()=>{
    let active=true;
    setBrief(undefined); setSummaryError('');
    api(base+'/narrative','POST').then(data=>{if(active)setBrief(data)})
      .catch(e=>{if(active)setSummaryError(e.message)});
    return ()=>{active=false};
  },[api,revision,summaryRetry]);

  useEffect(()=>{
    let active=true;
    let timer:ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [data, thread]:[Progress, {messages:Exchange[];more:boolean}]=await Promise.all([
          api(base+'/questions'),api(base+'/conversations/'+provider+'?limit='+limit)]);
        if(!active)return;
        setMessages(thread.messages); setMore(thread.more);
        if(data.pending.length){
          for(const id of data.pending){
            if(!active)return;
            await api(base+'/questions/'+id+'/process','POST');
          }
          if(active){updated(); setNotice('Reply reviewed. Sources and preferences are up to date.'); setEpoch(n=>n+1); return}
        }
        if(active)timer=setTimeout(refresh,data.tasks.some(t=>waiting.includes(t.status)) ? 5000 : 20000);
      }catch(e){if(active)setError((e as Error).message)}
    }
    void refresh();
    return ()=>{active=false;clearTimeout(timer)};
  },[api,epoch,updated,provider,limit]);

  function changeQuestion(text:string){
    setQuestion(text); setRequestId(crypto.randomUUID()); setNotice('');
  }
  async function run(action:()=>Promise<void>){
    setError('');setNotice('');setBusy(true);
    try{await action()}catch(e){setError((e as Error).message)}
    finally{setBusy(false);setEpoch(n=>n+1)}
  }
  async function askGrok(){
    const task=await api('/admin/grok/assignments','POST',{request_id:requestId,action:'career_question',question});
    if (!task.attempts || ['delivery_unknown','delivery_rejected','queued'].includes(task.status))
      await api('/admin/grok/assignments/'+task.id+'/dispatch','POST');
    changeQuestion('');
  }
  const musePrompt=question.trim()+'\nPlease use our previous conversations. Quote what I said, include dates when available, and flag anything you are unsure of.';
  return <section className="crm-surface crm-career-section"><h2>Career summary</h2>
    {brief?.summary && brief.summary.split(/\n+/).filter(Boolean).map((p,i)=><p key={i}>{p}</p>)}
    {!brief && !summaryError && <p role="status">Writing your summary…</p>}
    {summaryError && <p role="alert">{summaryError} <button onClick={()=>setSummaryRetry(n=>n+1)}>Retry summary</button></p>}
    {brief && !brief.summary && <p>Your summary will appear once there are sourced preferences.</p>}
    <h3>Conversations</h3>
    <div className="crm-filter-tabs">{(['grokbot','muse'] as const).map(p=><button key={p} aria-pressed={provider===p}
      disabled={busy || muse} onClick={()=>{if(p!==provider){setProvider(p);setMessages([]);setMore(false);setLimit(10);setNotice('')}}}>{p==='grokbot'?'Grokbot':'Muse'}</button>)}</div>
    {!messages.length && <p>No career questions to show yet.</p>}
    {messages.map((m,i)=><article key={m.id}><small>You · {new Date(m.created*1000).toLocaleString()}</small>
      <p><strong>{m.question}</strong></p>
      <small>{statusText[m.status] || m.status} · {new Date(m.updated*1000).toLocaleTimeString()}</small>
      {m.answer && <><details open={i===0}><summary>{provider==='muse'?'Muse':'Grokbot'}’s reply</summary><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{m.answer}</p></details>
        {m.status==='completed' && <small>{m.reviewed?'Reviewed for your career brief':'Awaiting career review'}</small>}</>}
      {provider==='grokbot' && m.status==='blocked' && m.answer?.includes("unsupported action 'career_question'") &&
        <p>Update the existing Grokbot routine to allow <code>career_question</code>. The routine instructions are in Connections. After updating Grokbot, ask the question again.</p>}
      {m.status==='handoff' && <button disabled={busy||muse} onClick={()=>{setQuestion(m.question);setRequestId(m.id);setAnswer('');setMuse(true)}}>Continue WhatsApp handoff</button>}
      {['queued','delivery_rejected','delivery_unknown'].includes(m.status) && <button disabled={busy}
        onClick={()=>void run(async()=>{await api('/admin/grok/assignments/'+m.id+'/dispatch','POST')})}>Retry delivery</button>}
    </article>)}
    {more && <button onClick={()=>setLimit(n=>n+10)}>Show older questions</button>}
    <h3>Ask a follow-up</h3>
    {!muse && brief?.questions.map(q=><p key={q}><button onClick={()=>changeQuestion(q)}>{q}</button></p>)}
    <label className="admin-field">Question<textarea rows={2} maxLength={300} value={question} readOnly={muse || busy}
      placeholder="What tradeoffs have I made between team leadership and hands-on work?" onChange={e=>changeQuestion(e.target.value)}/></label>
    {!muse && provider==='grokbot' && <button disabled={busy || !question.trim()} onClick={()=>void run(askGrok)}>Ask Grokbot</button>}
    {!muse && provider==='muse' && <button disabled={busy || !question.trim()} onClick={()=>void run(async()=>{await api(base+'/questions/muse/prepare','POST',{request_id:requestId,question});setMuse(true)})}>Prepare question for Muse</button>}
    {muse && <><p>Copy the question into your Muse conversation, then paste its reply here.</p>
      <button onClick={()=>void run(async()=>{await navigator.clipboard.writeText(musePrompt);setNotice('Question copied for Muse.')})}>Copy question</button>
      <label className="admin-field">Muse’s reply<textarea rows={4} maxLength={20000} value={answer} onChange={e=>setAnswer(e.target.value)}/></label>
      <button disabled={busy||!answer.trim()} onClick={()=>void run(async()=>{
        await api(base+'/questions/muse','POST',{request_id:requestId,question,answer});
        setAnswer('');setMuse(false);changeQuestion('');setNotice('Muse reply saved. Reviewing its evidence…');
      })}>Use Muse’s reply</button>
      <button disabled={busy} onClick={()=>setMuse(false)}>Back</button></>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error} <button onClick={()=>{setError('');setEpoch(n=>n+1)}}>Refresh replies</button></p>}
  </section>;
}
