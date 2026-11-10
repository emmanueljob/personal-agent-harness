import {useEffect,useRef,useState} from 'react';
import RFB from '@novnc/novnc';
type Api=(path:string,method?:string,body?:unknown)=>Promise<any>;
export function ChromeDesktop({api}:{api:Api}){
 const target=useRef<HTMLDivElement>(null),[state,setState]=useState('Connecting to private Chrome desktop…');
 useEffect(()=>{
  let stopped=false,rfb:any=null,timer:ReturnType<typeof setTimeout>,attempt=0;
  const connect=async()=>{
   try{
    const {ticket}=await api('/admin/cloud-browser/desktop-ticket','POST');if(stopped)return;
    const url=new URL('/api/admin/cloud-browser/desktop',window.location.href);url.protocol=window.location.protocol==='https:'?'wss:':'ws:';
    rfb=new RFB(target.current!,url.href,{wsProtocols:['desktop','ticket.'+ticket]});rfb.scaleViewport=true;rfb.resizeSession=false;rfb.showDotCursor=true;
    rfb.addEventListener('connect',()=>{attempt=0;if(!stopped)setState('Connected · Click Chrome to type normally')});
    rfb.addEventListener('disconnect',()=>{if(!stopped){setState('Desktop reconnecting…');timer=setTimeout(()=>void connect(),Math.min(30000,1000*2**Math.min(++attempt,5)))}});
    rfb.addEventListener('securityfailure',()=>{if(!stopped)setState('Private desktop authentication failed. Close and reopen the view.')});
   }catch(e){if(!stopped){setState((e as Error).message);timer=setTimeout(()=>void connect(),5000)}}
  };
  void connect();return()=>{stopped=true;clearTimeout(timer);rfb?.disconnect();target.current?.replaceChildren()};
 },[api]);
 return <div className="crm-native-desktop"><p role="status">{state}</p><div ref={target} className="crm-native-screen" aria-label="Private Chrome cloud desktop"/></div>
}
