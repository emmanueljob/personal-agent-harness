export type Personalization = {
 key:string; headline:string; pitch:string; featured:string[]; questions:string[];
 cta:string; banner:string; company:string; first90:string[]; email:string; booking:string;
};
const fallback:Personalization={key:'default',headline:'',pitch:'',featured:[],questions:[],cta:"Hiring? Let's talk",banner:'Open to roles in product and app engineering.',company:'',first90:[],email:'',booking:''};
export function initialProfile():Personalization {
 let value:Personalization|undefined;
 try { value=JSON.parse(document.getElementById('personalization')?.textContent || 'null') || undefined; } catch {}
 const tagged=location.pathname.startsWith('/r/') || ['utm_content','utm_campaign','utm_source','utm_medium','utm_term'].some(k=>new URLSearchParams(location.search).has(k));
 if (!tagged) {
   try { const saved=JSON.parse(sessionStorage.getItem('portfolio-profile') || 'null');if(saved?.key&&saved.key!==value?.key)value=saved; } catch {}
 }
 const result={...fallback,...value};
 try {sessionStorage.setItem('portfolio-profile',JSON.stringify(result));}catch{}
 return result;
}
export function trackQuestion() {
 (window as Window & {portfolioTrack?:(name:string)=>void}).portfolioTrack?.('guide_question_asked');
}

export function trackUI(name:'theme_changed'|'suggestion_used'|'turn_retried') {
 (window as Window & {portfolioTrack?:(name:string)=>void}).portfolioTrack?.(name);
}
