/* Shared across the React app and human-readable agent pages. */
(async () => {
  const keys = ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'];
  let profile = {key:'default'};
  let utm = {};
  try { utm = JSON.parse(sessionStorage.getItem('portfolio-utm') || '{}'); } catch {}
  const params = new URLSearchParams(location.search);
  if (keys.some(k => params.has(k))) utm = Object.fromEntries(keys.filter(k => params.has(k)).map(k => [k,params.get(k).slice(0,160)]));
  try { sessionStorage.setItem('portfolio-utm',JSON.stringify(utm)); } catch {}
  let ids = {enabled:false};
  try { ids = await fetch('/api/visit-config').then(r=>r.json()); } catch {}
  const trackingAllowed = ids.enabled && navigator.doNotTrack !== '1' && !navigator.globalPrivacyControl;
  const track = (name, target='') => {
    if (!trackingAllowed) return;
    const url = new URL(location.href);
    for (const [key,value] of Object.entries(utm)) if (!url.searchParams.has(key)) url.searchParams.set(key,value);
    // Do not send conversation text, result tokens or arbitrary query parameters.
    for (const key of [...url.searchParams.keys()]) if (!keys.includes(key)) url.searchParams.delete(key);
    const payload = JSON.stringify({name,target,url:url.pathname+url.search+url.hash,profile_key:profile.key,utm,referrer:document.referrer});
    fetch('/api/visit-events',{method:'POST',headers:{'Content-Type':'application/json'},body:payload,keepalive:true}).catch(()=>{});
  };
  window.portfolioTrack = track;
  try {
    let saved = '';
    try { saved = JSON.parse(sessionStorage.getItem('portfolio-profile') || 'null')?.key || ''; } catch {}
    const r = await fetch('/api/personalization'+location.search,{headers:{'X-Portfolio-Profile':saved}});
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
