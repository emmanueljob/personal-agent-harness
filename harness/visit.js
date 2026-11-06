/* Shared across the React app and human-readable agent pages. */
(async () => {
  const keys = ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'];
  let profile = {key:'default'};
  const cleanPath = /^\/(?:r|hi)\//.test(location.pathname);
  let outreachCode = '';
  if(cleanPath){try{outreachCode=JSON.parse(document.getElementById('personalization')?.textContent||'null')?.outreach_code||'invalid-clean-link'}catch{outreachCode='invalid-clean-link'}}
  if(!cleanPath) {try {outreachCode=sessionStorage.getItem('portfolio-outreach-code')||''}catch{}}
  let utm = {};
  try { utm = JSON.parse(sessionStorage.getItem('portfolio-utm') || '{}'); } catch {}
  const params = new URLSearchParams(location.search);
  if (!cleanPath && keys.some(k=>params.has(k))) outreachCode='';
  if (keys.some(k => params.has(k))) utm = Object.fromEntries(keys.filter(k => params.has(k)).map(k => [k,params.get(k).slice(0,160)]));
  if(outreachCode) utm={};
  try { sessionStorage.setItem('portfolio-outreach-code',outreachCode);sessionStorage.setItem('portfolio-utm',JSON.stringify(utm)); } catch {}
  let ids = {enabled:false};
  try { ids = await fetch('/api/visit-config').then(r=>r.json()); } catch {}
  const trackingAllowed = ids.enabled && navigator.doNotTrack !== '1' && !navigator.globalPrivacyControl;
  let session = '';
  let lastActivity = 0;
  const analyticsSession = () => {
    const now = Date.now();
    try {
      session = sessionStorage.getItem('portfolio-analytics-session') || '';
      lastActivity = Number(sessionStorage.getItem('portfolio-analytics-last')) || 0;
    } catch {}
    if (!/^[a-f0-9]{12}7[a-f0-9]{19}$/.test(session) || now-lastActivity >= 30*60*1000 || now-parseInt(session.slice(0,12),16) >= 24*60*60*1000) {
      const random = crypto.randomUUID().replaceAll('-','');
      session = now.toString(16).padStart(12,'0')+'7'+random.slice(13,16)+'8'+random.slice(17);
    }
    lastActivity = now;
    try {sessionStorage.setItem('portfolio-analytics-session',session);sessionStorage.setItem('portfolio-analytics-last',String(now))} catch {}
    return session;
  };
  if (trackingAllowed) {
    analyticsSession();
  }
  let trackingQueue = Promise.resolve();
  const track = (name, target='') => {
    if (!trackingAllowed) return;
    const url = new URL(location.href);
    for (const [key,value] of Object.entries(utm)) if (!url.searchParams.has(key)) url.searchParams.set(key,value);
    // Do not send conversation text, result tokens or arbitrary query parameters.
    for (const key of [...url.searchParams.keys()]) if (outreachCode || !keys.includes(key)) url.searchParams.delete(key);
    const payload = JSON.stringify({name,target,outreach_code:outreachCode,session_id:analyticsSession(),theme:document.documentElement.dataset.theme==='light'?'light':'dark',url:url.pathname+url.search+url.hash,profile_key:profile.key,utm,referrer:document.referrer});
    // Serialize the first events so a newly issued browser cookie is reused.
    trackingQueue = trackingQueue.then(()=>fetch('/api/visit-events',{method:'POST',headers:{'Content-Type':'application/json'},body:payload,keepalive:true})).catch(()=>{});
  };
  window.portfolioTrack = track;
  window.addEventListener('portfolio:book-opened',event=>{if(!event.detail?.error)track('book_opened')});
  try {
    let saved = '';
    try { saved = JSON.parse(sessionStorage.getItem('portfolio-profile') || 'null')?.key || ''; } catch {}
    const r = await fetch('/api/personalization'+location.search,{headers:{'X-Portfolio-Profile':saved,'X-Outreach-Link':outreachCode}});
    if (!r.ok) return;
    profile = await r.json();
    try { sessionStorage.setItem('portfolio-profile',JSON.stringify(profile)); } catch {}
    window.portfolioVisitor = profile;
    window.dispatchEvent(new CustomEvent('portfolio:profile-updated',{detail:profile}));
    const banner = document.createElement('aside'); banner.className='roles-banner'; banner.textContent=profile.banner;
    if(profile.banner) document.body.prepend(banner);
    const cta = document.createElement('aside'); cta.className='contact-cta'; cta.setAttribute('aria-label','Contact');
    const label = document.createElement('strong'); label.textContent=profile.contact_heading||"Hiring? Let's talk"; cta.append(label);
    for (const [target,href,text] of [['email',profile.email,profile.email_text||'Email me'],['booking',profile.booking,profile.cta === "Hiring? Let's talk" ? (profile.booking_text||'Book 15 min') : profile.cta]]) {
      if (!href) continue;
      const a=document.createElement('a');a.href=href;a.textContent=text;a.addEventListener('click',()=>track('cta_click',target));cta.append(a);
    }
    document.body.append(cta);
    document.addEventListener('click',event=>{
      const a=event.target.closest?.('.prose a');
      if(a && (a.href===profile.email || a.href===profile.booking)) track('cta_click',a.href===profile.email?'email':'booking');
    });
    track('profile_resolved'); track('pageview');
    let last=location.href;
    const page=()=>{if(last!==location.href){last=location.href;track('pageview')}};
    window.addEventListener('hashchange',page);window.addEventListener('popstate',page);
    const push=history.pushState.bind(history);history.pushState=(...args)=>{push(...args);page()};
    if (!trackingAllowed) return;
    const load=src=>{const s=document.createElement('script');s.async=true;s.src=src;document.head.append(s)};
    if (/^\d+$/.test(ids.linkedin)) {
      window._linkedin_partner_id=ids.linkedin;
      window._linkedin_data_partner_ids=[ids.linkedin];
      window.lintrk=function(a,b){window.lintrk.q.push([a,b])};window.lintrk.q=[];
      load('https://snap.licdn.com/li.lms-analytics/insight.min.js');
    }
    if (/^[a-z0-9]+$/i.test(ids.x)) {
      const twq=window.twq=function(){twq.exe?twq.exe.apply(twq,arguments):twq.queue.push([...arguments])};twq.queue=[];twq.version='1.1';
      load('https://static.ads-twitter.com/uwt.js');twq('config',ids.x);
    }
  } catch {}
})();
