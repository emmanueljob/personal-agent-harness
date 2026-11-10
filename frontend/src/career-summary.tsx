import {useEffect, useState} from 'react';

type Api = (path:string, method?:string, body?:unknown) => Promise<any>;
type Task = {task_id:string; question:string; status:string};
type Narrative = {summary:string; questions:string[]};
type Reply = {id:string; provider:string; question:string};
type Progress = {pending:string[]; tasks:Task[]; recent:Reply[]};
const base = '/admin/crm/career';
const waiting = ['delivered','acknowledged','sending'];

export function CareerSummary({api,revision,updated}:{api:Api;revision:number;updated:()=>void}) {
  const [brief,setBrief] = useState<Narrative>(), [summaryError,setSummaryError] = useState('');
  const [recent,setRecent] = useState<Reply[]>([]);
  const [tasks,setTasks] = useState<Task[]>([]), [epoch,setEpoch] = useState(0);
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
        const data:Progress=await api(base+'/questions');
        if(!active)return;
        setTasks(data.tasks); setRecent(data.recent);
        if(data.pending.length){
          for(const id of data.pending){
            if(!active)return;
            await api(base+'/questions/'+id+'/process','POST');
          }
          if(active){updated(); setNotice('Reply reviewed. Sources and preferences are up to date.'); setEpoch(n=>n+1); return}
        }
        if(active && data.tasks.some(t=>waiting.includes(t.status)))timer=setTimeout(refresh,5000);
      }catch(e){if(active)setError((e as Error).message)}
    }
    void refresh();
    return ()=>{active=false;clearTimeout(timer)};
  },[api,epoch,updated]);

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
  }
  const musePrompt=question.trim()+'\nPlease use our previous conversations. Quote what I said, include dates when available, and flag anything you are unsure of.';
  return <section className="crm-surface crm-career-section"><h2>Career summary</h2>
    {brief?.summary && brief.summary.split(/\n+/).filter(Boolean).map((p,i)=><p key={i}>{p}</p>)}
    {!brief && !summaryError && <p role="status">Writing your summary…</p>}
    {summaryError && <p role="alert">{summaryError} <button onClick={()=>setSummaryRetry(n=>n+1)}>Retry summary</button></p>}
    {brief && !brief.summary && <p>Your summary will appear once there are sourced preferences.</p>}
    <h3>Ask a follow-up</h3>
    {!muse && brief?.questions.map(q=><p key={q}><button onClick={()=>changeQuestion(q)}>{q}</button></p>)}
    <label className="admin-field">Question<textarea rows={2} maxLength={300} value={question} readOnly={muse || busy}
      placeholder="What tradeoffs have I made between team leadership and hands-on work?" onChange={e=>changeQuestion(e.target.value)}/></label>
    {!muse && <><button disabled={busy || !question.trim()} onClick={()=>void run(askGrok)}>Ask Grokbot</button>
      <button disabled={busy || !question.trim()} onClick={()=>{setMuse(true);setNotice('')}}>Ask Muse via WhatsApp</button></>}
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
    {recent.length>0 && <details><summary>Reviewed answers</summary>{recent.map(r=><p key={r.id}>{r.provider} · {r.question}</p>)}<small>Full answers are available in Sources.</small></details>}
    {tasks.length>0 && <details open><summary>Recent Grokbot questions</summary>{tasks.map(t=><p key={t.task_id}>{t.question} · {t.status.replaceAll('_',' ')}
      {['queued','delivery_rejected','delivery_unknown'].includes(t.status) && <button disabled={busy}
        onClick={()=>void run(async()=>{await api('/admin/grok/assignments/'+t.task_id+'/dispatch','POST')})}>Retry delivery</button>}</p>)}</details>}
  </section>;
}
