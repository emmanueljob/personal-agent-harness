import {ObservabilityStudio} from './observability';
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AnimatePresence, motion, MotionConfig} from 'motion/react';
import Markdown from 'react-markdown';
import {emptyPortfolio, type Portfolio} from './portfolio';
import './style.css';
import {initialProfile, trackQuestion, trackUI} from './personalization';
import {Bookshelf,ShelfStudio,type Book} from './bookshelf';
import Architecture from './architecture';
import {LearningStudio,type FeedbackSeed} from './learning';
import {registerPortfolioTools} from './webmcp';

type Mode='public'|'private';
const sections=[['conversation','Conversation'],['work','The work'],['person','The person'],['bookshelf','The bookshelf'],['behind-the-curtain','The machinery']] as const;
type Section=typeof sections[number][0];
const sectionFromHash=():Section=>sections.find(([id])=>'#'+id===location.hash)?.[0]||'conversation';
type Turn={id:string;prompt:string;status:string;result:string|null;error:string|null;created:number};
type Artifact={id:string;title:string;body:string};
type Event={id:number;turn_id:string;kind:string;label:string;created:number};
type ToolRun={turn_id:string;name:string;sources:{title:string;url:string}[];fetched_at?:string;ui_action?:{type:string;book_ids:string[]};books?:{id:string;title:string}[]};
type Thread={id:string;mode:Mode;tool_calls?:ToolRun[];turns:Turn[];artifacts:Artifact[];events:Event[]};
type Routine={id:string;thread_id:string;prompt:string;interval_seconds:number;enabled:number};
type Status={public_only?:boolean;owner_studio?:boolean;knowledge_backend?:string;provider:string;model:string;executor:string;model_configured:boolean};
const Arrow=()=> <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>;
function ThemeIcon({light}:{light:boolean}){return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">{light?<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>:<path d="M20 15.2A8.8 8.8 0 0 1 8.8 4a8.8 8.8 0 1 0 11.2 11.2Z"/>}</svg>}
function Orb({busy}:{busy:boolean}){return <div className={'orb '+(busy?'orb-active':'')} aria-hidden="true"><div className="orb-aura"/><div className="orb-ring ring-back"/><div className="orb-sphere"/><div className="orb-texture"/><div className="orb-ring ring-front"/><span className="orb-light"/></div>}
function Working({turn,events}:{turn:Turn;events:Event[]}){
 const recent=events.filter(e=>e.turn_id===turn.id);const phase=recent.at(-1)?.label||(turn.status==='queued'?'Waiting for the worker':'Starting this turn');
 const [elapsed,setElapsed]=useState(()=>Math.max(0,Math.floor(Date.now()/1000-turn.created)));
 useEffect(()=>{const t=setInterval(()=>setElapsed(Math.max(0,Math.floor(Date.now()/1000-turn.created))),1000);return()=>clearInterval(t)},[turn.created]);
 return <div className="working"><div className="working-line"><div className="ink-wave" aria-hidden="true">{Array.from({length:9},(_,i)=><i key={i} style={{animationDelay:(i*.09)+'s'}}/>)}</div><AnimatePresence mode="wait"><motion.p key={phase} initial={{opacity:0,y:3}} animate={{opacity:1,y:0}} exit={{opacity:0}} role="status">{phase}<span className="ellipsis" aria-hidden="true">…</span></motion.p></AnimatePresence><span className="elapsed">{elapsed}s</span></div>{recent.length>0&&<details className="activity"><summary>Activity <span>{recent.length}</span></summary><ol>{recent.map(e=><li key={e.id}>{e.label}</li>)}</ol></details>}</div>
}

function ToolTrace({turnId,events,runs}:{turnId:string;events:Event[];runs:ToolRun[]}){
 const steps=events.filter(e=>e.turn_id===turnId&&(e.kind.startsWith('tool')||e.kind.startsWith('knowledge')));
 const calls=runs.filter(r=>r.turn_id===turnId);const sources=Array.from(new Map(calls.flatMap(c=>c.sources).map(s=>[s.url,s])).values());
 if(!steps.length&&!calls.length)return null;
 return <details className="tool-trace"><summary>Tools & sources <span>{calls.length?calls.map(c=>c.name==='search_web'?'Web search':c.name==='browse_bookshelf'?'Bookshelf':'Page reader').filter((v,i,a)=>a.indexOf(v)===i).join(' · '):'Knowledge search'}</span></summary><ol>{steps.map(e=><li key={e.id}>{e.label}</li>)}</ol>{sources.length>0&&<div className="research-sources">{sources.map(s=><a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title||new URL(s.url).hostname}<span>↗</span></a>)}</div>}</details>
}

function App(){
 const [visitor,setVisitor]=useState(initialProfile);
 useEffect(()=>{const sync=(event:globalThis.Event)=>setVisitor((event as CustomEvent).detail);window.addEventListener('portfolio:profile-updated',sync);const current=(window as Window & {portfolioVisitor?:ReturnType<typeof initialProfile>}).portfolioVisitor;if(current)setVisitor(current);return()=>window.removeEventListener('portfolio:profile-updated',sync)},[]);
 const [activeTab,setActiveTab]=useState<Section>(sectionFromHash);
 const navigate=useCallback((section:Section)=>{if(location.hash!=='#'+section)history.pushState(null,'','#'+section);setActiveTab(section);window.scrollTo({top:0,behavior:'instant'})},[]);
 useEffect(()=>{const sync=()=>{setActiveTab(sectionFromHash());window.scrollTo({top:0,behavior:'instant'})};window.addEventListener('hashchange',sync);window.addEventListener('popstate',sync);return()=>{window.removeEventListener('hashchange',sync);window.removeEventListener('popstate',sync)}},[]);
 const tabKey=(event:React.KeyboardEvent<HTMLButtonElement>,index:number)=>{let next:number;if(event.key==='ArrowRight')next=(index+1)%sections.length;else if(event.key==='ArrowLeft')next=(index+sections.length-1)%sections.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=sections.length-1;else return;event.preventDefault();navigate(sections[next][0]);document.getElementById('tab-'+sections[next][0])?.focus()};
 const [books,setBooks]=useState<Book[]>([]);
 const [shelfSelection,setShelfSelection]=useState<string[]|null>(null);
 const showShelf=(ids:string[])=>{setShelfSelection(ids);navigate('bookshelf');requestAnimationFrame(()=>document.getElementById('tab-bookshelf')?.focus())};
 const browserState=useRef({section:activeTab,selected_book_ids:shelfSelection});browserState.current={section:activeTab,selected_book_ids:shelfSelection};
 const bookCatalog=useRef(books);bookCatalog.current=books;
 const webActions=useRef({showBooks:showShelf,openBook:async(id:string)=>{if(!bookCatalog.current.some(b=>b.id===id))throw new Error('Book is not currently published.');showShelf([id]);await new Promise<void>((resolve,reject)=>{const onReady=(event:globalThis.Event)=>{const detail=(event as CustomEvent).detail;if(detail?.book_id!==id)return;window.removeEventListener('portfolio:book-opened',onReady);clearTimeout(timeout);detail.error?reject(new Error(detail.error)):resolve()};const timeout=setTimeout(()=>{window.removeEventListener('portfolio:book-opened',onReady);reject(new Error('Book inspector did not open.'))},5000);window.addEventListener('portfolio:book-opened',onReady);requestAnimationFrame(()=>requestAnimationFrame(()=>window.dispatchEvent(new CustomEvent('portfolio:open-book',{detail:{book_id:id}}))))})},state:()=>browserState.current});
 webActions.current.showBooks=showShelf;
 useEffect(()=>{let stopped=false;let cleanup:(()=>void)|undefined;void registerPortfolioTools({showBooks:ids=>webActions.current.showBooks(ids),openBook:id=>webActions.current.openBook(id),state:()=>browserState.current}).then(dispose=>{if(stopped)dispose();else cleanup=dispose}).catch(()=>{});return()=>{stopped=true;cleanup?.()}},[]);
 useEffect(()=>{const id=new URLSearchParams(location.search).get('book');if(id&&books.some(b=>b.id===id))showShelf([id])},[books]);
 const [availableTools,setAvailableTools]=useState<{name:string;description:string}[]>([]);
 const reloadBooks=()=>fetch('/api/bookshelf').then(r=>r.json()).then(d=>setBooks(d.books)).catch(()=>{});
 useEffect(()=>{void reloadBooks()},[]);
 const [portfolio,setPortfolio]=useState<Portfolio>(emptyPortfolio);
 const [theme,setTheme]=useState<'dark'|'light'>(()=>{const saved=localStorage.getItem('afterhours-theme');return saved==='light'||saved==='dark'?saved:'dark'});
 const context=activeTab==='work'||activeTab==='person'?activeTab:null;
 const [projectId,setProjectId]=useState('');
 const project=portfolio.projects.find(p=>p.id===projectId)||portfolio.projects[0];
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('afterhours-theme',theme)},[theme]);
 const [mode,setMode]=useState<Mode>('public');
 const [threadId,setThreadId]=useState<string|null>(()=>sessionStorage.getItem('afterhours-thread-public'));
 const [thread,setThread]=useState<Thread|null>(null);
 const lastShelfAction=useRef('');
 useEffect(()=>{lastShelfAction.current='';setShelfSelection(null)},[threadId]);
 useEffect(()=>{if(!books.length)return;const action=thread?.tool_calls?.filter(c=>c.ui_action?.type==='bookshelf_selection').at(-1);if(!action)return;const key=action.turn_id+JSON.stringify(action.ui_action);if(lastShelfAction.current===key)return;lastShelfAction.current=key;const ids=action.ui_action!.book_ids.filter(id=>books.some(b=>b.id===id));setShelfSelection(ids.length?ids:null)},[thread,books]);
 const [token,setToken]=useState('');
 const [prompt,setPrompt]=useState('');
 const [sending,setSending]=useState(false);
 const [notice,setNotice]=useState('');
 const [status,setStatus]=useState<Status|null>(null);
 const [ownerOpen,setOwnerOpen]=useState(false);
 const [ownerTab,setOwnerTab]=useState<'workspace'|'profile'|'routines'|'brain'|'bookshelf'|'learning'|'observability'>('workspace');
 const [feedbackSeed,setFeedbackSeed]=useState<FeedbackSeed|null>(null);
 const [profileMode,setProfileMode]=useState<Mode>('public');
 const [profile,setProfile]=useState('');
 const [routinePrompt,setRoutinePrompt]=useState('');
 const [interval,setIntervalMinutes]=useState(60);
 const [noteTitle,setNoteTitle]=useState('');
 const [noteBody,setNoteBody]=useState('');
 const [noteScope,setNoteScope]=useState<Mode>('private');
 const [routines,setRoutines]=useState<Routine[]>([]);
 const [artifact,setArtifact]=useState<Artifact|null>(null);
 const requestRef=useRef<{prompt:string,id:string}|null>(null);
 const viewVersion=useRef(0);
 const endRef=useRef<HTMLDivElement>(null);
 const inputRef=useRef<HTMLTextAreaElement>(null);
 const dialogRef=useRef<HTMLDialogElement>(null);
 const artifactDialogRef=useRef<HTMLDialogElement>(null);
 const api=useCallback(async(path:string,method='GET',body?:unknown)=>{
   const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:body===undefined?undefined:JSON.stringify(body)});
   const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;
 },[token]);
 useEffect(()=>{api('/status').then(setStatus).catch(()=>setNotice('The server is unavailable.'));api('/tools').then(setAvailableTools).catch(()=>{});api('/portfolio').then(data=>{const p=data as Portfolio;if(visitor.questions.length)p.suggestions=visitor.questions.map(question=>({label:question,question}));if(visitor.featured.length)p.projects.sort((a,b)=>{const rank=(id:string)=>{const n=visitor.featured.indexOf(id);return n<0?999:n};return rank(a.id)-rank(b.id)});setPortfolio(p)}).catch(()=>setNotice('Portfolio data is unavailable.'))},[api,visitor]);
 useEffect(()=>{document.title=portfolio.name==='Your name'?'Portfolio':portfolio.name+' — Portfolio'},[portfolio.name]);
 useEffect(()=>{
   if(!threadId)return;
   let active=true;let timer:ReturnType<typeof setTimeout>;
   const load=async()=>{try{const d=await api('/threads/'+threadId);if(active){setThread(d);setNotice(n=>n==='Private workspace requires OWNER_TOKEN.'?'':n)}}catch(e){if(active)setNotice((e as Error).message)}finally{if(active)timer=setTimeout(load,800)}};
   load();return()=>{active=false;clearTimeout(timer)};
 },[threadId,api]);
 const lastTurn=thread?.turns.at(-1);
 const pending=thread?.turns.filter(t=>['queued','running'].includes(t.status))||[];
 useEffect(()=>{if(activeTab!=='conversation')return;endRef.current?.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})},[lastTurn?.id,lastTurn?.status,activeTab]);
 useEffect(()=>{const d=dialogRef.current;if(ownerOpen&&!d?.open)d?.showModal();if(!ownerOpen&&d?.open)d.close()},[ownerOpen]);
 useEffect(()=>{const d=artifactDialogRef.current;if(artifact&&!d?.open)d?.showModal();if(!artifact&&d?.open)d.close()},[artifact]);
 const perform=async(fn:()=>Promise<unknown>,success?:string)=>{try{await fn();if(success)setNotice(success)}catch(e){setNotice((e as Error).message)}};
 const newThread=async(m=mode)=>{const version=viewVersion.current;const d=await api('/threads','POST',{mode:m});if(version!==viewVersion.current)return d.id as string;setThreadId(d.id);setThread({...d,turns:[],artifacts:[],events:[]});sessionStorage.setItem('afterhours-thread-'+m,d.id);return d.id as string};
 const switchMode=(m:Mode)=>{navigate('conversation');viewVersion.current++;setArtifact(null);setPrompt('');setMode(m);setThread(null);setThreadId(sessionStorage.getItem('afterhours-thread-'+m));setNotice('');requestRef.current=null};
 const send=async(text=prompt)=>{
   if(!text.trim()||sending)return;const version=viewVersion.current;setSending(true);setNotice('');
   try{
     const tid=threadId||await newThread();if(version!==viewVersion.current)return;
     if(requestRef.current?.prompt!==text)requestRef.current={prompt:text,id:crypto.randomUUID()};
     await api('/threads/'+tid+'/turns','POST',{prompt:text,request_id:requestRef.current.id,profile_key:visitor.key});
     if(mode==='public')trackQuestion();
     const updated=await api('/threads/'+tid);if(version===viewVersion.current){requestRef.current=null;setPrompt('');setThread(updated);}
   }catch(e){if(version===viewVersion.current)setNotice((e as Error).message)}finally{setSending(false)}
 };
 const ask=(text:string)=>{if(mode==='public')trackUI('suggestion_used');setPrompt(text);navigate('conversation');requestAnimationFrame(()=>{inputRef.current?.focus();inputRef.current?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'nearest'})})};
 const loadProfile=()=>perform(async()=>setProfile((await api('/profiles/'+profileMode)).body));
 const loadRoutines=()=>perform(async()=>setRoutines(await api('/routines')));
 const download=(a:Artifact)=>{const url=URL.createObjectURL(new Blob([a.body],{type:'text/markdown'}));const link=document.createElement('a');link.href=url;link.download=a.title.replace(/[^\p{L}\p{N} _-]/gu,'').slice(0,80)+'.md';link.click();URL.revokeObjectURL(url)};
 return <MotionConfig reducedMotion="user"><div className="afterhours-app" data-theme={theme}>
 <header className="topbar"><a className="identity" href="#conversation" onClick={e=>{e.preventDefault();navigate('conversation')}} aria-label={portfolio.name+'’s AI portfolio'}><span className="identity-orb"/><span>{portfolio.name} <span className="identity-divider">/</span> <span className="identity-edition">portfolio</span></span></a><nav aria-label="Portfolio navigation"><div className="section-tabs" role="tablist" aria-label="Portfolio sections">{sections.map(([id,label],i)=><button key={id} id={'tab-'+id} role="tab" aria-selected={activeTab===id} aria-controls={'panel-'+id} tabIndex={activeTab===id?0:-1} onClick={()=>navigate(id)} onKeyDown={e=>tabKey(e,i)}>{label}</button>)}</div><button className="theme-toggle" onClick={()=>{setTheme(theme==='dark'?'light':'dark');requestAnimationFrame(()=>trackUI('theme_changed'))}} aria-label={theme==='dark'?'Switch to light mode':'Switch to dark mode'}><ThemeIcon light={theme==='dark'}/><span>{theme==='dark'?'Light':'Dark'}</span></button></nav></header>
 <div hidden={activeTab==='bookshelf'||activeTab==='behind-the-curtain'} className={'app-body portfolio-panel '+(context?'with-context':'')}>
 <main id="panel-conversation" role="tabpanel" aria-labelledby="tab-conversation" tabIndex={0} hidden={activeTab!=='conversation'} className={'agent-stage '+(thread?.turns.length?'has-conversation':'')}>
 <div className="conversation-bar"><span className="mode-caption"><span className="status-dot"/>{mode==='public'?'A CONVERSATION WITH '+portfolio.shortName.toUpperCase()+'’S DIGITAL TWIN':'YOUR PRIVATE STUDIO'}</span><button className="fresh-button" onClick={()=>{viewVersion.current++;void perform(()=>newThread())}}><span>↺</span> Start fresh</button></div>
 <div className={'presence '+(pending.length?'presence-busy':'')}><Orb busy={pending.length>0}/>{!thread?.turns.length&&<span className="presence-caption">Engineering depth. A little curiosity.</span>}</div>
 {!thread?.turns.length&&<motion.div className="welcome" initial={{opacity:0,y:8}} animate={{opacity:1,y:0}}><h1>{mode==='public'?(visitor.headline||<>Start with a <em>question.</em></>):<>What are we <em>making?</em></>}</h1><p>{mode==='public'?<>{visitor.pitch||portfolio.intro}</>:<>Your career, your ideas, and room to experiment.<br className="desktop-break"/> Let’s make something worth keeping.</>}</p></motion.div>}
 {visitor.company&&activeTab==='conversation'&&!thread?.turns.length&&<section className="first90"><h2>3 things I'd ship in my first 90 days at {visitor.company}</h2><ol>{visitor.first90.map(item=><li key={item}>{item}</li>)}</ol></section>}
 {Boolean(thread?.turns.length)&&<section className="transcript" aria-label="Conversation history">{thread?.turns.map(t=><article key={t.id} className="turn"><div className="reader-question"><span className="eyebrow">YOU</span><p>{t.prompt}</p></div><div className="reply"><div className="reply-meta"><span className="small-orb"/><span className="eyebrow">DIGITAL TWIN · AI</span><span className="reply-source">{mode==='public'?'PUBLISHED SOURCES':'PRIVATE STUDIO'}</span></div>{t.status==='completed'?<motion.div initial={{opacity:0,y:5}} animate={{opacity:1,y:0}} className="prose"><Markdown>{t.result||''}</Markdown></motion.div>:t.status==='failed'?<div className="failure"><p>{t.error||'This turn could not finish.'}</p><button className="inline-button" onClick={()=>perform(async()=>{await api('/turns/'+t.id+'/retry','POST');if(mode==='public')trackUI('turn_retried')})}>Resume this turn ↗</button></div>:<Working turn={t} events={thread.events}/>}{(thread.tool_calls||[]).filter(c=>c.turn_id===t.id&&c.books?.length).slice(-1).map(c=><div key={c.turn_id+'-books'} className="agent-book-result"><span>SELECTED FROM THE SHELF</span><p>{c.books!.map(b=>b.title.split(':')[0]).join(' · ')}</p><button onClick={()=>showShelf(c.ui_action!.book_ids)}>View {c.books!.length===1?'this book':c.books!.length+' books'} on the shelf ↗</button></div>)}{t.status==='completed'&&status&&(!status.public_only||(status.owner_studio&&!!token))&&<button className="inline-button teach-reply" onClick={()=>{setFeedbackSeed({id:t.id,prompt:t.prompt,result:t.result});setOwnerTab('learning');setOwnerOpen(true)}}>Correct this reply / teach the twin ↗</button>}{['completed','failed'].includes(t.status)&&<ToolTrace turnId={t.id} events={thread.events} runs={thread.tool_calls||[]}/>}</div></article>)}<div ref={endRef}/></section>}
 <div className="composer-area"><form className="composer" onSubmit={e=>{e.preventDefault();void send()}}><label className="sr-only" htmlFor="message">Your question</label><textarea id="message" ref={inputRef} value={prompt} onChange={e=>setPrompt(e.target.value)} rows={1} maxLength={16000} placeholder={mode==='public'?'What would you like to know?':'What are we working on?'} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}}/><button className="send-button" type="submit" disabled={sending||!prompt.trim()} aria-label="Send message"><Arrow/></button></form>
 {mode==='public'&&status?.provider==='ollama'&&<p className="inference-note">An experiment: this agent runs on a local AI model on my laptop, so replies can be slow.</p>}
 {mode==='public'&&<p className="conversation-notice">Conversations are saved and may be reviewed by the site owner to improve this demo. Please avoid sharing sensitive information.</p>}
 {!thread?.turns.length&&<div className="suggestions">{(mode==='public'?portfolio.suggestions:[{label:'Draft a career introduction',question:'Draft a short career introduction using my résumé and save it as an artifact.'},{label:'Explore an AI product idea',question:'Help me explore a product idea based on my background and interests.'}]).map(q=><button key={q.label} onClick={()=>ask(q.question)}>{q.label}<span>↗</span></button>)}</div>}
 {!thread?.turns.length&&availableTools.some(t=>t.name==='search_web')&&<div className="research-prompts" aria-label="Try the agent’s research tools"><span>Put it to work</span><button onClick={()=>ask('Show me No Rules Rules on the bookshelf, using your bookshelf tool.')}>Let the agent pick a book ↗</button><button onClick={()=>ask('Search the web for a recent article about AI agent engineering, read an original source, and compare one idea with the work described in this portfolio. Cite your sources.')}>Research an idea ↗</button><button onClick={()=>ask('Read https://agilemanifesto.org/principles.html and compare two principles with the leadership notes in your knowledge base. Cite the webpage and the relevant notes.')}>Read & compare ↗</button></div>}
 <div className="session-note"><span>{pending.length?'Your turn is saved. Working on a response.':status?.provider==='ollama'?(status.public_only?`Local model on ${portfolio.shortName}’s Mac · Conversations saved`:'Private inference on this Mac · Conversations saved'):'AI portfolio guide · Conversations saved'}</span>{Boolean(thread?.turns.length)&&<span>Enter to send · Shift + Enter for a new line</span>}</div>
 </div>
 {Boolean(thread?.artifacts.length)&&<section className="artifact-shelf" aria-label="Generated documents"><span className="eyebrow">MADE IN THIS CONVERSATION</span><div className="artifact-grid">{thread?.artifacts.map(a=><motion.button key={a.id} className="artifact-card" whileHover={{y:-3}} onClick={()=>setArtifact(a)}><span className="document-mark">↗</span><span><strong>{a.title}</strong><small>Open document</small></span></motion.button>)}</div></section>}
 </main>
 {context&&<aside id={'panel-'+context} role="tabpanel" aria-labelledby={'tab-'+context} tabIndex={0} className="context-panel" aria-label={context==='work'?'Selected work':'About '+portfolio.name}><div className="context-heading"><span className="eyebrow">{context==='work'?'A CLOSER LOOK':'THE PERSON'}</span></div>
 {context==='work'?(project?<><div className="project-art" aria-hidden="true"><div className="project-ring"/><div className="project-core"/><span>{portfolio.focus||'SELECTED WORK'}</span></div><div className="project-picker" aria-label="Choose a project">{portfolio.projects.map(p=><button key={p.id} aria-pressed={projectId===p.id} className={projectId===p.id?'selected':''} onClick={()=>setProjectId(p.id)}>{p.title}</button>)}</div><span className="eyebrow accent">{project.eyebrow}</span><h2>{project.title}</h2><p className="company-line">{project.company}</p><p>{project.description}</p><div className="project-outcome">{project.outcome}</div><p>{project.detail}</p>{project.id==='mojo'&&<a className="inline-button" href="https://trymojo.ai" target="_blank" rel="noreferrer">Explore Mojo ↗</a>}<button className="inline-button" onClick={()=>ask(project.question)}>Ask about this work ↗</button><small className="source-note">Source: {portfolio.source||'Published profile'} · {portfolio.sourceDate}</small></>:<p>No public projects have been configured.</p>):<><span className="eyebrow accent">{portfolio.role}</span><h2>{portfolio.shortName}<br/><em>{portfolio.surname}{portfolio.surname?'.':''}</em></h2><p className="person-focus">{portfolio.focusLines.map((line,i)=><React.Fragment key={line}>{i>0&&<br/>}{line}</React.Fragment>)}</p><p>{portfolio.summary}</p><p className="location">{portfolio.location}</p><a className="inline-button" href={portfolio.linkedin} target="_blank" rel="noreferrer">LinkedIn ↗</a><div className="career-list">{portfolio.career.map(c=><div className="career-item" key={c.company}><span className="eyebrow">{c.dates}</span><h3>{c.company}</h3><strong>{c.role}</strong><p>{c.note}</p></div>)}</div><div className="skills">{portfolio.skills.map(s=><span key={s}>{s}</span>)}</div><div className="education"><span className="eyebrow">EDUCATION</span><p>{portfolio.education}</p></div><button className="inline-button" onClick={()=>ask(portfolio.careerQuestion)}>Explore the background ↗</button><small className="source-note">Source: {portfolio.source||'Published profile'} · {portfolio.sourceDate}</small></>}
 </aside>}
 </div>
 <div className="portfolio-panel" id="panel-bookshelf" role="tabpanel" aria-labelledby="tab-bookshelf" tabIndex={0} hidden={activeTab!=='bookshelf'}><Bookshelf books={books} ask={ask} enabled={activeTab==='bookshelf'} selection={shelfSelection} clearSelection={()=>setShelfSelection(null)}/></div>
 <div className="portfolio-panel" id="panel-behind-the-curtain" role="tabpanel" aria-labelledby="tab-behind-the-curtain" tabIndex={0} hidden={activeTab!=='behind-the-curtain'}><Architecture ask={ask}/></div>
 <footer className="footer"><span className="footer-signature">{portfolio.footer}</span><div><a className="owner-link" href="/agents">For agents ↗</a><span className="footer-meta">AI GUIDE · {portfolio.resumeLabel}</span>{status&&(!status.public_only||status.owner_studio)&&<a href="/admin" className="owner-link">Admin portal ↗</a>}{status&&(!status.public_only||status.owner_studio)&&<button onClick={()=>{if(status.public_only)setOwnerTab('learning');setOwnerOpen(true)}} className="owner-link">Owner studio ↗</button>}</div></footer>
 <AnimatePresence>{notice&&<motion.div className="notice" initial={{opacity:0,y:5}} animate={{opacity:1,y:0}} exit={{opacity:0}} role="status"><span>{notice}</span><button aria-label="Dismiss notification" onClick={()=>setNotice('')}>×</button></motion.div>}</AnimatePresence>
 <dialog ref={dialogRef} className={'owner-dialog'+(ownerTab==='observability'?' observability-dialog':'')} onCancel={()=>setOwnerOpen(false)} onClose={()=>setOwnerOpen(false)}><div className="dialog-heading"><div><span className="eyebrow">Behind the scenes</span><h2>The notebook.</h2></div><button className="close-button" onClick={()=>setOwnerOpen(false)} aria-label="Close owner panel">×</button></div><div className="owner-access"><label htmlFor="owner-token">Owner access</label><input id="owner-token" type="password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Enter your owner password" autoComplete="off"/><small>Kept in memory for this session. Use the same owner password on this Mac and the live site.</small></div><div className="owner-tabs" role="tablist">{(['workspace','profile','routines','brain','bookshelf','learning','observability'] as const).filter(t=>!status?.public_only||t==='learning'||t==='observability').map(t=><button key={t} role="tab" aria-selected={ownerTab===t} className={ownerTab===t?'selected':''} onClick={()=>setOwnerTab(t)}>{t==='learning'?'Reading & listening':t}</button>)}</div><div className="owner-content">
 {ownerTab==='observability'&&<ObservabilityStudio key={token} api={api}/>}
 {ownerTab==='learning'&&<LearningStudio api={api} seed={feedbackSeed}/>}
 {ownerTab==='bookshelf'&&<ShelfStudio api={api} onPublished={()=>void reloadBooks()}/>}
 {ownerTab==='workspace'&&<><h3>Choose an edition.</h3><p>The public edition uses published facts. The private notebook uses separate context and can make documents.</p><div className="mode-choices">{(['public','private'] as Mode[]).map(m=><button key={m} className={mode===m?'selected':''} onClick={()=>{switchMode(m);setOwnerOpen(false)}}>{m==='public'?'Public edition':'Private notebook'} <span>↗</span></button>)}</div><div className="system-note">{status?.model} · {status?.executor}<br/>Public and private conversations are separate.</div></>}
 {ownerTab==='profile'&&<><h3>The facts behind the conversation.</h3><label htmlFor="profile-mode">Profile to edit</label><select id="profile-mode" value={profileMode} onChange={e=>{setProfileMode(e.target.value as Mode);setProfile('')}}><option value="public">Public · published information</option><option value="private">Private · owner context</option></select><button className="inline-button" onClick={loadProfile}>Load current profile ↗</button><label htmlFor="profile">Verified information</label><textarea id="profile" rows={8} value={profile} onChange={e=>setProfile(e.target.value)} placeholder="Your background, projects, strengths, and links. Only add information you want this edition to use."/><button className="solid-button" onClick={()=>perform(()=>api('/profiles/'+profileMode,'PUT',{body:profile}),'Profile saved. New turns will use the updated facts.')}>Save profile ↗</button><small>Existing turns keep their original context when retried.</small></>}
 {ownerTab==='brain'&&<><h3>A little more context.</h3><p>Add interests, preferences, career stories, or things you want the agent to remember. Notes live in your separate knowledge folder, outside this codebase.</p><label htmlFor="note-title">Title</label><input id="note-title" value={noteTitle} onChange={e=>setNoteTitle(e.target.value)} placeholder="A thing worth remembering"/><label htmlFor="note-body">Knowledge note</label><textarea id="note-body" rows={6} value={noteBody} onChange={e=>setNoteBody(e.target.value)} placeholder="Add facts and context in your own words."/><label htmlFor="note-scope">Who may use this?</label><select id="note-scope" value={noteScope} onChange={e=>setNoteScope(e.target.value as Mode)}><option value="private">Private · owner assistant only</option><option value="public">Public · visitors’ agent can retrieve this</option></select><small>{noteScope==='public'?'This note will be available to visitor conversations.':'Private notes are excluded from visitor retrieval.'}</small><button className="solid-button" disabled={!noteTitle.trim()||!noteBody.trim()} onClick={()=>perform(async()=>{await api('/brain/notes','POST',{title:noteTitle,body:noteBody,scope:noteScope});setNoteTitle('');setNoteBody('')},'Knowledge note saved.')}>Save knowledge ↗</button><div className="system-note">Current provider: {status?.knowledge_backend==='gbrain'?'GBrain · local keyword search':'local files'}.<br/>Public and private knowledge are searched separately.</div></>}
 {ownerTab==='routines'&&<><h3>Make a little room for routine.</h3><p>Routines run in the private notebook. They resume when this Mac and the local services are running.</p><label htmlFor="routine">A repeating prompt</label><textarea id="routine" rows={3} value={routinePrompt} onChange={e=>setRoutinePrompt(e.target.value)} placeholder="Draft a weekly career reflection and save it."/><label htmlFor="minutes">Repeat every (minutes)</label><input id="minutes" type="number" min={1} max={525600} value={interval} onChange={e=>setIntervalMinutes(Number(e.target.value))}/><button className="solid-button" disabled={!routinePrompt.trim()} onClick={()=>perform(async()=>{if(mode!=='private')throw Error('Switch to the private notebook first.');const tid=threadId||await newThread('private');await api('/threads/'+tid+'/routines','POST',{prompt:routinePrompt,interval_seconds:interval*60});setRoutines(await api('/routines'))},'Routine saved.')}>Create routine ↗</button><button className="inline-button" onClick={loadRoutines}>Refresh routines ↻</button>{routines.map(r=><div className="routine-entry" key={r.id}><span className="eyebrow">{r.enabled?'Active':'Disabled'} · Every {r.interval_seconds/60} min</span><p>{r.prompt}</p>{Boolean(r.enabled)&&<div><button className="inline-button" onClick={()=>perform(()=>api('/routines/'+r.id+'/trigger','POST'),'Routine turn queued.')}>Run now ↗</button><button className="inline-button" onClick={()=>perform(async()=>{await api('/routines/'+r.id,'DELETE');setRoutines(await api('/routines'))},'Routine disabled.')}>Disable</button></div>}</div>)}</>}
 </div>{notice&&<p className="dialog-notice" role="status">{notice}</p>}</dialog>
 <dialog ref={artifactDialogRef} className="artifact-dialog" onCancel={()=>setArtifact(null)} onClose={()=>setArtifact(null)}>{artifact&&<><div className="dialog-heading"><div><span className="eyebrow">Made in the notebook</span><h2>{artifact.title}</h2></div><button className="close-button" aria-label="Close document" onClick={()=>setArtifact(null)}>×</button></div><div className="prose artifact-body"><Markdown>{artifact.body}</Markdown></div><button className="solid-button" onClick={()=>download(artifact)}>Download Markdown ↓</button></>}</dialog>
 </div></MotionConfig>
}

const AdminPortal=React.lazy(()=>import('./admin'));
createRoot(document.getElementById('root')!).render(<React.StrictMode>{location.pathname==='/admin'?<React.Suspense fallback={<p>Opening admin portal…</p>}><AdminPortal/></React.Suspense>:<App/>}</React.StrictMode>);
