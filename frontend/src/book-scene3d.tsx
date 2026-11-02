import {useEffect, useRef, useState} from 'react';
import * as THREE from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import type {Book,BookOrigin} from './bookshelf';
import './book-scene3d.css';
import {spineLabel} from './book-labels';

type Props = {book:Book; opened:boolean; returning:boolean; inspecting:boolean; origin:BookOrigin; getSourceRect:()=>DOMRect|null; onReady:()=>void; onPreviewEnter:()=>void; onPreviewLeave:()=>void; onInspect:()=>void; onOpen:()=>void; onReturned:()=>void; onAsk:()=>void};
const W=2.55, H=3.72, D=.22;
const paper='#f2e9d2', ink='#332d25', muted='#69583f';
const clamp=THREE.MathUtils.clamp;
const smooth=(n:number)=>{n=clamp(n,0,1);return n*n*(3-2*n)};

function lines(ctx:CanvasRenderingContext2D, text:string, x:number, y:number, width:number, lineHeight:number, maxLines=100){
 const words=text.split(/\s+/);let line='', count=0;
 for(let i=0;i<words.length;i++){
  const next=line?line+' '+words[i]:words[i];
  if(ctx.measureText(next).width>width&&line){ctx.fillText(line,x,y);y+=lineHeight;count++;line=words[i];if(count>=maxLines-1){const rest=words.slice(i).join(' ');let end=rest;while(ctx.measureText(end+'…').width>width&&end.length)end=end.slice(0,-1);ctx.fillText(end+'…',x,y);return y+lineHeight}}
  else line=next;
 }
 if(line)ctx.fillText(line,x,y);return y+lineHeight;
}
function texture(draw:(ctx:CanvasRenderingContext2D)=>void, width=1024,height=1480){
 const c=document.createElement('canvas');c.width=width;c.height=height;const ctx=c.getContext('2d')!;draw(ctx);
 const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=8;return t;
}
function paperBase(c:CanvasRenderingContext2D,label:string,page:string){
 c.fillStyle=paper;c.fillRect(0,0,1024,1480);
 for(let i=0;i<3200;i++){const x=(i*137.503)%1024,y=(i*71.871)%1480;c.fillStyle=i%2?'#ab957c0b':'#ffffff28';c.fillRect(x,y,1.5,1.5)}
 const shade=c.createLinearGradient(0,0,1024,0);shade.addColorStop(0,'#79634435');shade.addColorStop(.1,'#00000000');shade.addColorStop(.93,'#00000000');shade.addColorStop(1,'#a78c6218');c.fillStyle=shade;c.fillRect(0,0,1024,1480);
 c.fillStyle=muted;c.font='24px "DM Mono", monospace';c.fillText(label,95,96);c.fillStyle='#b3a18455';c.fillRect(95,135,834,1);
 c.textAlign='center';c.fillStyle=muted;c.fillText(page,512,1410);c.textAlign='left';
}
function noteTexture(book:Book){return texture(c=>{
 paperBase(c,'A NOTE IN THE MARGINS','01');c.fillStyle='#b7a172';c.font='240px "DM Serif Display", Georgia';c.fillText('“',100,440);
 if(book.comment){c.fillStyle=ink;const size=book.comment.length>280?48:book.comment.length>100?62:94;c.font=`${size}px "DM Serif Display", Georgia`;const y=lines(c,book.comment,100,540,804,size*1.3,7);c.fillStyle=muted;c.font='30px "DM Sans Variable", sans-serif';lines(c,book.comment_source,100,y+55,804,36,3)}
 else{c.fillStyle=ink;c.font='68px "DM Serif Display", Georgia';lines(c,'An unwritten margin.',100,550,790,80);c.font='30px "DM Sans Variable", sans-serif';c.fillStyle=muted;lines(c,'No personal comment recorded yet.',100,760,770,45)}
 c.fillStyle='#ad956766';c.fillRect(100,1150,90,2);c.fillStyle=muted;c.font='30px "DM Sans Variable", sans-serif';lines(c,'A place on this shelf does not establish reading, completion, or recommendation.',100,1215,790,35,3);
})}
function detailTexture(book:Book){return texture(c=>{
 paperBase(c,'AFTERHOURS / READING ROOM','02');c.fillStyle='#94672e';c.font='23px "DM Mono", monospace';c.fillText(book.category==='work'?'WORK & IDEAS':book.category==='personal'?'OTHER WORLDS':'FROM THE LIBRARY',95,235);
 c.fillStyle=ink;c.font='76px "DM Serif Display", Georgia';let y=lines(c,book.title.split(':')[0],95,340,824,86,4);
 if(book.title.includes(':')){c.fillStyle=muted;c.font='32px "DM Serif Display", Georgia';y=lines(c,book.title.split(':').slice(1).join(':').trim(),95,y+15,815,43,3)}
 c.fillStyle=muted;c.font='34px "DM Sans Variable", sans-serif';y=lines(c,book.author,95,y+35,820,39,2)+60;
 for(const [label,value] of [['READING',book.status==='unknown'?'Not recorded':book.status],['RECOMMENDATION',book.recommendation==='unknown'?'Not recorded':book.recommendation],['METADATA',book.source||'Not recorded']]){
  c.fillStyle='#b3a18466';c.fillRect(95,y,834,1);c.fillStyle=muted;c.font='24px "DM Mono", monospace';c.fillText(label,95,y+43);c.fillStyle=ink;c.font='34px "DM Sans Variable", sans-serif';y=lines(c,value,95,y+89,810,37,2)+30;
 }
 c.fillStyle='#97672d';c.font='25px "DM Sans Variable", sans-serif';c.fillText('Continue the conversation  ↗',95,1320);
})}
function jacketTexture(book:Book,back=false){return texture(c=>{
 c.fillStyle='#30493e';c.fillRect(0,0,1024,1480);const g=c.createLinearGradient(0,0,1024,1480);g.addColorStop(0,'#c8a56836');g.addColorStop(.5,'#00000000');g.addColorStop(1,'#001f1e80');c.fillStyle=g;c.fillRect(0,0,1024,1480);
 c.strokeStyle='#c8ab6c99';c.lineWidth=3;c.strokeRect(50,50,924,1380);c.fillStyle='#dfc897';c.font='23px "DM Mono", monospace';c.fillText('AFTERHOURS / READING ROOM',95,150);
 c.fillStyle='#f5e9ce';c.font='86px "DM Serif Display", Georgia';let y=lines(c,book.title.split(':')[0],95,back?450:430,810,100,7);
 c.fillStyle='#c9b98f';c.font='32px "DM Sans Variable", sans-serif';lines(c,book.author,95,y+110,800,48,3);
 if(back){c.fillStyle='#d6c7a1';c.font='26px "DM Serif Display", Georgia';lines(c,'A book from the Afterhours reading room.',95,1130,760,43);c.font='18px "DM Mono", monospace';c.fillText('OWNER-CURATED METADATA',95,1340)}
})}
function edgeTexture(){return texture(c=>{c.fillStyle='#e6dcc0';c.fillRect(0,0,256,512);for(let y=0;y<512;y+=3){c.fillStyle=y%9===0?'#9f8e684d':'#fff7e566';c.fillRect(0,y,256,1)}},256,512)}
function spineTexture(book:Book){return texture(c=>{
 c.fillStyle='#31483c';c.fillRect(0,0,256,1480);c.save();c.translate(144,120);c.rotate(Math.PI/2);c.fillStyle='#e9dbb6';c.font='56px "DM Serif Display", Georgia';let text=spineLabel(book.title);while(c.measureText(text).width>1180)text=text.slice(0,-1);c.fillText(text,0,0);c.restore();c.fillStyle='#bd9f67';c.fillRect(50,1350,156,3);
},256,1480)}

export default function BookScene3D({book,opened,returning,inspecting,origin,getSourceRect,onReady,onPreviewEnter,onPreviewLeave,onInspect,onOpen,onReturned,onAsk}:Props){
 const host=useRef<HTMLDivElement>(null);const hoverHit=useRef<HTMLButtonElement>(null);const state=useRef({opened,returning,inspecting,getSourceRect,onReady,onPreviewEnter,onPreviewLeave,onInspect,onOpen,onReturned,onAsk});state.current={opened,returning,inspecting,getSourceRect,onReady,onPreviewEnter,onPreviewLeave,onInspect,onOpen,onReturned,onAsk};
 const commands=useRef({rotate:(_amount:number)=>{},reset:()=>{},zoom:(_amount:number)=>{}});
 const [failed,setFailed]=useState(false),[readable,setReadable]=useState(false),[narrow,setNarrow]=useState(()=>matchMedia('(max-width:620px)').matches);
 useEffect(()=>{const mq=matchMedia('(max-width:620px)');const update=()=>setNarrow(mq.matches);mq.addEventListener('change',update);return()=>mq.removeEventListener('change',update)},[]);
 useEffect(()=>{if(failed&&returning)onReturned()},[failed,returning,onReturned]);
 useEffect(()=>{
  const el=host.current;if(!el)return;let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,powerPreference:'high-performance'})}catch{setFailed(true);return}
  let disposed=false;const textures:THREE.Texture[]=[];const register=(t:THREE.Texture)=>{textures.push(t);return t};
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.75));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.VSMShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.9;renderer.setClearColor(0,0);
  const canvas=renderer.domElement;canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Interactive 3D model of '+book.title+'. Drag to rotate, use arrow keys to turn, Enter to open, and Home to reset.');canvas.dataset.renderer='webgl2';el.append(canvas);
  const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(32,1,.05,100);camera.position.set(0,.12,7.7);
  const envGenerator=new THREE.PMREMGenerator(renderer);const room=new RoomEnvironment();const environment=envGenerator.fromScene(room,.04);scene.environment=environment.texture;scene.environmentIntensity=.38;room.dispose();envGenerator.dispose();
  scene.add(new THREE.HemisphereLight('#fff2d8','#28372e',.85));const key=new THREE.DirectionalLight('#ffe3b1',2.3);key.position.set(-3,6,6);key.castShadow=true;key.shadow.mapSize.set(1536,1536);key.shadow.camera.left=-8;key.shadow.camera.right=8;key.shadow.camera.top=8;key.shadow.camera.bottom=-8;key.shadow.normalBias=.03;key.shadow.bias=-.0004;key.shadow.radius=5;key.shadow.blurSamples=8;scene.add(key);
  const rim=new THREE.DirectionalLight('#b2dbd0',1.4);rim.position.set(5,2,-3);scene.add(rim);const fill=new THREE.DirectionalLight('#fff8ee',.45);fill.position.set(1,-1,7);scene.add(fill);
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(200,200),new THREE.ShadowMaterial({opacity:.35}));ground.rotation.x=-Math.PI/2;ground.position.y=-2.2;ground.receiveShadow=true;scene.add(ground);
  const ring=new THREE.Mesh(new THREE.RingGeometry(2.3,2.31,100),new THREE.MeshBasicMaterial({color:'#b28e55',transparent:true,opacity:.12,side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.y=-2.18;scene.add(ring);
  const root=new THREE.Group();scene.add(root);
  const cloth=new THREE.MeshPhysicalMaterial({color:'#344d40',roughness:.72,sheen:.45,sheenColor:new THREE.Color('#a78d56'),clearcoat:.08});
  const edges=register(edgeTexture());const pageEdge=new THREE.MeshStandardMaterial({map:edges,roughness:.92,color:'#f7efd9'});const pageSurface=new THREE.MeshStandardMaterial({color:paper,roughness:.9});
  const block=new THREE.Mesh(new THREE.BoxGeometry(W-.055,H-.07,D),[pageEdge,cloth,pageEdge,pageEdge,pageSurface,pageEdge]);block.castShadow=true;block.receiveShadow=true;root.add(block);
  const backMaterial=new THREE.MeshPhysicalMaterial({map:register(jacketTexture(book,true)),roughness:.54,clearcoat:.2});const back=new THREE.Mesh(new THREE.BoxGeometry(W+.035,H+.035,.045),[cloth,cloth,cloth,cloth,cloth,backMaterial]);back.position.z=-D/2-.022;back.castShadow=true;root.add(back);
  const coverMaterial=new THREE.MeshPhysicalMaterial({map:register(jacketTexture(book)),roughness:.65,clearcoat:.12,clearcoatRoughness:.6});
  const noteMaterial=new THREE.MeshStandardMaterial({map:register(noteTexture(book)),roughness:.86});const detailMaterial=new THREE.MeshStandardMaterial({map:register(detailTexture(book)),roughness:.88});
  const hinge=new THREE.Group();hinge.position.set(-W/2,0,D/2+.02);root.add(hinge);
  const cover=new THREE.Mesh(new THREE.BoxGeometry(W+.025,H+.025,.046),[cloth,cloth,cloth,cloth,coverMaterial,noteMaterial]);cover.position.x=W/2;cover.castShadow=true;cover.receiveShadow=true;hinge.add(cover);
  // The first flexible leaf rolls away behind the cover, leaving the notes spread.
  const leafGeometry=new THREE.PlaneGeometry(W-.045,H-.065,26,2);leafGeometry.translate(W/2,0,0);const leaf=new THREE.Mesh(leafGeometry,new THREE.MeshStandardMaterial({map:detailMaterial.map,roughness:.9,side:THREE.DoubleSide}));leaf.position.set(-W/2,0,D/2+.045);leaf.castShadow=true;root.add(leaf);
  const detail=new THREE.Mesh(new THREE.PlaneGeometry(W-.065,H-.085),detailMaterial);detail.position.z=D/2+.009;root.add(detail);
  const spineFallback=new THREE.MeshStandardMaterial({map:register(spineTexture(book)),roughness:.8});const spineGeom=new THREE.PlaneGeometry(D+.065,H+.01,12,24);const spine=new THREE.Mesh(spineGeom,spineFallback);spine.rotation.y=-Math.PI/2;spine.position.x=-W/2-.004;root.add(spine);
  const loader=new THREE.TextureLoader();loader.setCrossOrigin('anonymous');
  if(book.cover)loader.load('/api/bookshelf/cover/'+book.id,t=>{if(disposed){t.dispose();return}t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=renderer.capabilities.getMaxAnisotropy();register(t);coverMaterial.map=t;coverMaterial.needsUpdate=true},undefined,()=>{});
  if(book.spine){const ref=book.spine;loader.load(ref.url,t=>{if(disposed){t.dispose();return}t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=8;register(t);const uv=spineGeom.attributes.uv;for(let i=0;i<uv.count;i++){const u=uv.getX(i),v=1-uv.getY(i);const top=new THREE.Vector2(...ref.points[0]).lerp(new THREE.Vector2(...ref.points[1]),u),bottom=new THREE.Vector2(...ref.points[3]).lerp(new THREE.Vector2(...ref.points[2]),u);const point=top.lerp(bottom,v);uv.setXY(i,point.x/ref.width,1-point.y/ref.height)}uv.needsUpdate=true;spineFallback.map=t;spineFallback.needsUpdate=true},undefined,()=>{})}
  void document.fonts.ready.then(()=>{if(disposed)return;const nextNote=register(noteTexture(book)),nextDetail=register(detailTexture(book));noteMaterial.map=nextNote;detailMaterial.map=nextDetail;noteMaterial.needsUpdate=true;detailMaterial.needsUpdate=true;(leaf.material as THREE.MeshStandardMaterial).map=nextDetail;(leaf.material as THREE.MeshStandardMaterial).needsUpdate=true});
  let width=0,height=0;const resize=()=>{const nextWidth=el.clientWidth,nextHeight=el.clientHeight;if(nextWidth<1||nextHeight<1)return;width=nextWidth;height=nextHeight;renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix()};const observer=new ResizeObserver(resize);observer.observe(el);resize();
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');let reduce=reduced.matches;const motionChange=()=>{reduce=reduced.matches};reduced.addEventListener('change',motionChange);
  let dragX=0,dragY=0,zoom=0,dragging=false,moved=false,startX=0,startY=0,lastX=0,lastY=0;const pointer=new THREE.Vector2(),ray=new THREE.Raycaster();
  const reset=()=>{dragX=0;dragY=0;zoom=0};commands.current={rotate:a=>{dragX+=a},reset,zoom:a=>{zoom=clamp(zoom+a,-1.3,2)}};
  const down=(e:PointerEvent)=>{if(e.button!==0||!state.current.inspecting)return;dragging=true;moved=false;startX=lastX=e.clientX;startY=lastY=e.clientY;canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing'};
  const move=(e:PointerEvent)=>{if(!dragging)return;if(Math.hypot(e.clientX-startX,e.clientY-startY)>5)moved=true;dragX+=(e.clientX-lastX)*.007;dragY=clamp(dragY+(e.clientY-lastY)*.004,-.5,.5);lastX=e.clientX;lastY=e.clientY};
  const up=(e:PointerEvent)=>{if(!dragging)return;dragging=false;canvas.style.cursor='grab';if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);if(!moved&&!state.current.returning){const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);if(!state.current.opened&&ray.intersectObject(root,true).length)state.current.onOpen();if(state.current.opened){const hit=ray.intersectObject(detail)[0];if(hit?.uv&&hit.uv.y<.18)state.current.onAsk()}}};
  const cancel=()=>{dragging=false;canvas.style.cursor='grab'};
  const keys=(e:KeyboardEvent)=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','Enter','+','-'].includes(e.key))e.preventDefault();if(e.key==='ArrowLeft')dragX-=.18;if(e.key==='ArrowRight')dragX+=.18;if(e.key==='ArrowUp')dragY=clamp(dragY-.12,-.5,.5);if(e.key==='ArrowDown')dragY=clamp(dragY+.12,-.5,.5);if(e.key==='Home')reset();if(e.key==='Enter'&&!state.current.opened)state.current.onOpen();if(e.key==='+')zoom=clamp(zoom+.25,-1.3,2);if(e.key==='-')zoom=clamp(zoom-.25,-1.3,2)};
  canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',cancel);canvas.addEventListener('keydown',keys);const lost=(e:Event)=>{e.preventDefault();renderer.setAnimationLoop(null);setFailed(true)};canvas.addEventListener('webglcontextlost',lost);
  const createdAt=performance.now();let angle=0,angleVelocity=0,previous=0,returnAt:number|null=null,returned=false,frame=0,wasOpened=false,ready=false;
  const pose={x:0,y:0,z:0,scale:1,yaw:origin.face==='spine'?Math.PI/2:-.12,pitch:0};
  const velocity={x:0,y:0,z:0,scale:0,yaw:0,pitch:0};let initialized=false;
  const spring=(key:keyof typeof pose,target:number,dt:number,frequency=13)=>{if(reduce){pose[key]=target;velocity[key]=0;return}const offset=pose[key]-target,decay=Math.exp(-frequency*dt),temp=(velocity[key]+frequency*offset)*dt;pose[key]=target+(offset+temp)*decay;velocity[key]=(velocity[key]-frequency*temp)*decay};
  renderer.setAnimationLoop(()=>{
   if(disposed||document.hidden||width<1||height<1)return;const elapsed=(performance.now()-createdAt)/1000,dt=Math.min(.033,Math.max(0,elapsed-previous));previous=elapsed;
   if(state.current.opened!==wasOpened){wasOpened=state.current.opened;reset()}
   if(state.current.returning&&returnAt===null)returnAt=elapsed;if(!state.current.returning){returnAt=null;returned=false}
   const retreat=returnAt!==null,returnTime=retreat?elapsed-returnAt!:0;
   const target=(state.current.opened&&!retreat)?1:0;
   if(reduce){angle=target;angleVelocity=0}else{angleVelocity+=(target-angle)*82*dt;angleVelocity*=Math.exp(-15*dt);angle+=angleVelocity*dt}
   const open=clamp(angle,0,1.025);hinge.rotation.y=-Math.PI*.988*open;
   const leafProgress=smooth((open-.12)/.73);leaf.rotation.y=-Math.PI*.97*leafProgress;leaf.visible=open>.03&&open<.98;
   const vertices=leafGeometry.attributes.position;for(let i=0;i<vertices.count;i++){const x=vertices.getX(i);vertices.setZ(i,Math.sin(x/W*Math.PI)*Math.sin(leafProgress*Math.PI)*.24)}vertices.needsUpdate=true;leafGeometry.computeVertexNormals();
   // One camera and one persistent volume from the shelf to the reading room.
   // Project the source's actual viewport position into the camera plane.
   camera.position.set(0,0,10);camera.lookAt(0,0,0);
   const worldHeight=20*Math.tan(THREE.MathUtils.degToRad(16)),units=worldHeight/height;
   const rect=canvas.getBoundingClientRect(),sourceRect=state.current.getSourceRect();const sourceXPixel=sourceRect?sourceRect.left+sourceRect.width/2:origin.x,sourceYPixel=sourceRect?sourceRect.top+sourceRect.height/2:origin.y;const anchorX=(sourceXPixel-rect.left-width/2)*units,anchorY=-(sourceYPixel-rect.top-height/2)*units;
   const sourceScale=(sourceRect?.height??origin.height)*units/H;const initialYaw=origin.face==='spine'?Math.PI/2:-.12;
   const hoverProgress=reduce?1:smooth(elapsed/.58);
   const hoverYaw=origin.face==='spine'?THREE.MathUtils.lerp(initialYaw,.66,smooth((elapsed-.055)/.5)):-.18;
   const hoverScale=sourceScale*(1+.08*hoverProgress);
   const spinePivot=origin.face==='spine'?W/2:0;
   const sourceX=anchorX+Math.cos(initialYaw)*spinePivot*sourceScale;
   const sourceZ=-Math.sin(initialYaw)*spinePivot*sourceScale;
   const hoverX=clamp(anchorX+Math.cos(hoverYaw)*spinePivot*hoverScale,-worldHeight*camera.aspect/2+W*hoverScale*.6,worldHeight*camera.aspect/2-W*hoverScale*.6);
   const hoverZ=-Math.sin(hoverYaw)*spinePivot*hoverScale+.14*hoverProgress;
   const inspectionScale=Math.min(height*.61,555)*units/H;
   const openScale=Math.min(inspectionScale,(width-32)*units/(W*2.2));
   const inspection={x:open*W/2*openScale,y:height*THREE.MathUtils.lerp(.045,width<620?.22:.045,smooth(open))*units,z:0,scale:THREE.MathUtils.lerp(inspectionScale,openScale,smooth(open)),yaw:.20*(1-open)-.05*open+dragX,pitch:-.045+dragY};
   const atShelf=retreat&&(!state.current.inspecting||returnTime>.28);
   const destination=atShelf?{x:sourceX,y:anchorY,z:sourceZ,scale:sourceScale,yaw:initialYaw,pitch:0}:state.current.inspecting?inspection:{x:hoverX,y:anchorY+8*units*hoverProgress,z:hoverZ,scale:hoverScale,yaw:hoverYaw,pitch:-.025*hoverProgress};
   if(!initialized){Object.assign(pose,{x:sourceX,y:anchorY,z:sourceZ,scale:sourceScale,yaw:initialYaw,pitch:0});initialized=true}
   for(const key of Object.keys(pose) as (keyof typeof pose)[])spring(key,destination[key],dt,state.current.inspecting?9.5:16);
   root.position.set(pose.x,pose.y,pose.z);root.scale.setScalar(pose.scale*(1+zoom*.12));root.rotation.set(pose.pitch,pose.yaw,0);
   ground.visible=state.current.inspecting;ring.visible=state.current.inspecting;ground.position.y=-H/2*pose.scale-.15;ring.position.y=ground.position.y+.02;ring.scale.setScalar(pose.scale);
   if(++frame%4===0){canvas.dataset.phase=retreat?'returning':state.current.inspecting?(target?(Math.abs(open-1)<.015?'open':'opening'):'cover'):'hover';canvas.dataset.rotation=root.rotation.y.toFixed(3);canvas.dataset.open=open.toFixed(3)}
   renderer.render(scene,camera);
   if(!state.current.inspecting&&hoverHit.current){const box=new THREE.Box3().setFromObject(root);let left=Infinity,top=Infinity,right=-Infinity,bottom=-Infinity;for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z]){const p=new THREE.Vector3(x,y,z).project(camera),sx=(p.x+1)*width/2,sy=(1-p.y)*height/2;left=Math.min(left,sx);right=Math.max(right,sx);top=Math.min(top,sy);bottom=Math.max(bottom,sy)}Object.assign(hoverHit.current.style,{left:(left-10)+'px',top:(top-10)+'px',width:(right-left+20)+'px',height:(bottom-top+20)+'px'})}
   if(!ready){ready=true;state.current.onReady()}
   const error=Math.abs(pose.x-sourceX)+Math.abs(pose.y-anchorY)+Math.abs(pose.z-sourceZ)+Math.abs(pose.scale-sourceScale)+Math.abs(pose.yaw-initialYaw);
   if(retreat&&atShelf&&(reduce||(returnTime>.35&&error<.012))&&!returned){returned=true;state.current.onReturned()}
  });
  return()=>{disposed=true;renderer.setAnimationLoop(null);observer.disconnect();reduced.removeEventListener('change',motionChange);canvas.removeEventListener('pointerdown',down);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',up);canvas.removeEventListener('pointercancel',cancel);canvas.removeEventListener('keydown',keys);canvas.removeEventListener('webglcontextlost',lost);const materials=new Set<THREE.Material>(),geometries=new Set<THREE.BufferGeometry>();scene.traverse(o=>{if(o instanceof THREE.Mesh){geometries.add(o.geometry);for(const m of Array.isArray(o.material)?o.material:[o.material])materials.add(m)}});geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());environment.dispose();renderer.dispose();renderer.forceContextLoss();canvas.remove()};
 },[book.id]);
 const showText=opened&&(readable||failed||narrow);
 return <div className={'game-book-viewer '+(failed?'game-fallback':'')}>
  {!inspecting&&!failed&&<button ref={hoverHit} className="hover-book-hit" aria-label={'Pick up '+book.title} onPointerEnter={onPreviewEnter} onPointerLeave={onPreviewLeave} onFocus={onPreviewEnter} onBlur={onPreviewLeave} onClick={onInspect}/>}
  {!failed?<><div className="three-book-stage" ref={host}/><div className="inspection-hud"><span>{returning?'Returning to the shelf…':opened?'Drag to explore the open book':'Drag to turn · click the book to open'}</span><div className="inspection-tools" aria-label="3D book controls"><button aria-label="Rotate book left" onClick={()=>commands.current.rotate(-.3)}>↶</button><button aria-label="Reset book view" onClick={()=>commands.current.reset()}>◎</button><button aria-label="Rotate book right" onClick={()=>commands.current.rotate(.3)}>↷</button><button aria-label="Zoom in on book" onClick={()=>commands.current.zoom(.35)}>+</button><button aria-label="Zoom out from book" onClick={()=>commands.current.zoom(-.35)}>−</button>{opened&&!narrow&&<button className="read-notes-button" aria-expanded={readable} onClick={()=>setReadable(!readable)}>Read notes</button>}</div></div></>:inspecting?<button className="fallback-jacket" onClick={onOpen} aria-label={'Open '+book.title}>{book.cover?<img src={book.cover} alt={'Cover of '+book.title}/>:<strong>{book.title}</strong>}</button>:null}
  {opened&&<section className={showText?'readable-book-notes':'sr-only'} aria-label="Book notes and details" inert={!showText} aria-hidden={!showText}><div><span className="eyebrow">IN THE MARGINS</span>{book.comment?<><blockquote>{book.comment}</blockquote><small>{book.comment_source}</small></>:<p>No personal comment recorded yet.</p>}</div><div><h3>{book.title}</h3><p>{book.author}</p><dl><div><dt>Reading</dt><dd>{book.status==='unknown'?'Not recorded':book.status}</dd></div><div><dt>Recommendation</dt><dd>{book.recommendation==='unknown'?'Not recorded':book.recommendation}</dd></div><div><dt>Metadata</dt><dd>{book.source||'Not recorded'}</dd></div></dl>{book.spine&&<a href={book.spine.source} target="_blank" rel="noreferrer">Spine photo reference ↗</a>}<button className="readable-agent" onClick={onAsk}>Continue with the portfolio agent ↗</button></div></section>}
 </div>
}
