import {useEffect, useRef, useState} from 'react';
import * as THREE from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import type {Book,BookOrigin} from './bookshelf';
import './book-scene3d.css';
import {createLeaf} from './book-leaf';
import {bindingColor} from './book-binding';
import {spineLabel} from './book-labels';

type Props = {book:Book; opened:boolean; returning:boolean; inspecting:boolean; origin:BookOrigin; getSourceRect:()=>DOMRect|null; onReady:()=>void; onPreviewEnter:()=>void; onPreviewLeave:()=>void; onInspect:()=>void; onOpen:()=>void; onReturned:()=>void; onAsk:()=>void};
const W=2.55, H=3.72, D=.22;
const spreads = ['Title page', 'Reading notes', 'Colophon'];
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
 paperBase(c,'READING NOTES','02');c.fillStyle='#b7a172';c.font='240px "DM Serif Display", Georgia';c.fillText('“',100,440);
 if(book.comment){c.fillStyle=ink;const size=book.comment.length>280?48:book.comment.length>100?62:94;c.font=`${size}px "DM Serif Display", Georgia`;const y=lines(c,book.comment,100,540,804,size*1.3,7);c.fillStyle=muted;c.font='30px "DM Sans Variable", sans-serif';lines(c,book.comment_source,100,y+55,804,36,3)}
 else{c.fillStyle=ink;c.font='68px "DM Serif Display", Georgia';lines(c,'An unwritten margin.',100,550,790,80);c.font='30px "DM Sans Variable", sans-serif';c.fillStyle=muted;lines(c,'No personal comment recorded yet.',100,760,770,45)}

})}
function detailTexture(book:Book){return texture(c=>{
 paperBase(c,'LIBRARY RECORD','03');c.fillStyle='#94672e';c.font='23px "DM Mono", monospace';c.fillText(book.category==='work'?'WORK & IDEAS':book.category==='personal'?'OTHER WORLDS':'FROM THE LIBRARY',95,235);
 c.fillStyle=ink;c.font='76px "DM Serif Display", Georgia';let y=lines(c,book.title.split(':')[0],95,340,824,86,4);
 if(book.title.includes(':')){c.fillStyle=muted;c.font='32px "DM Serif Display", Georgia';y=lines(c,book.title.split(':').slice(1).join(':').trim(),95,y+15,815,43,3)}
 c.fillStyle=muted;c.font='34px "DM Sans Variable", sans-serif';y=lines(c,book.author,95,y+35,820,39,2)+60;
 for(const [label,value] of [['READING',book.status==='unknown'?'Not recorded':book.status],['RECOMMENDATION',book.recommendation==='unknown'?'Not recorded':book.recommendation],['METADATA',book.source||'Not recorded']]){
  c.fillStyle='#b3a18466';c.fillRect(95,y,834,1);c.fillStyle=muted;c.font='24px "DM Mono", monospace';c.fillText(label,95,y+43);c.fillStyle=ink;c.font='34px "DM Sans Variable", sans-serif';y=lines(c,value,95,y+89,810,37,2)+30;
 }
 c.fillStyle='#97672d';c.font='25px "DM Mono", monospace';
})}
function endpaper(book: Book) {
 return texture(c => {
  c.fillStyle = bindingColor(book.id); c.fillRect(0, 0, 1024, 1480);
  c.fillStyle = '#110d0b50'; c.fillRect(0, 0, 1024, 1480);
  c.strokeStyle = '#edcf903c'; c.lineWidth = 1.2;
  // Nested engraved fans, tiled like a printed bookbinder's endpaper.
  for (let row = -1; row < 17; row++) {
   for (let col = -1; col < 10; col++) {
    const x = col * 132 + (row % 2) * 66, y = row * 94;
    for (let r = 8; r <= 65; r += 8) {
     c.beginPath(); c.arc(x, y, r, Math.PI, 2 * Math.PI); c.stroke();
    }
   }
  }
  c.strokeStyle = '#e2c88b'; c.strokeRect(80, 80, 864, 1320);
  c.strokeStyle = '#e2c88b70'; c.strokeRect(90, 90, 844, 1300);
 });
}
function titlePage(book: Book) {
 return texture(c => {
  paperBase(c,'TITLE PAGE','01'); c.textAlign = 'center'; c.fillStyle = ink;
  c.font = '76px "DM Serif Display", Georgia';
  const y = lines(c, book.title, 512, 500, 790, 89, 6);
  c.fillStyle = muted; c.fillRect(477, y + 42, 70, 2);
  c.font = 'italic 42px Georgia'; lines(c, book.author, 512, y + 136, 790, 56, 3);
 });
}
function colophon(book: Book) {
 return texture(c => {
  paperBase(c,'COLOPHON','04'); c.fillStyle = ink; c.font='78px Georgia';
  c.fillText('Colophon', 95, 320); let y = 440;
  const fields = [['Catalog source', book.source], ['Notes from', book.comment_source],
   ['Subjects', book.note_tags?.join(' · ')], ['Edition', book.sample ? 'Sample' : '']];
  for (const [label, value] of fields) {
   if (!value) continue;
   c.fillStyle=muted; c.font='24px monospace'; c.fillText(label!.toUpperCase(),95,y);
   c.fillStyle=ink; c.font='42px Georgia'; y=lines(c,value,95,y+62,820,52,3)+70;
  }
 });
}
function jacketTexture(book:Book,back=false){return texture(c=>{
 c.fillStyle=bindingColor(book.id);c.fillRect(0,0,1024,1480);const g=c.createLinearGradient(0,0,1024,1480);g.addColorStop(0,'#c8a56836');g.addColorStop(.5,'#00000000');g.addColorStop(1,'#001f1e80');c.fillStyle=g;c.fillRect(0,0,1024,1480);
 c.strokeStyle='#c8ab6c99';c.lineWidth=3;c.strokeRect(50,50,924,1380);c.fillStyle='#dfc897';c.font='23px "DM Mono", monospace';c.fillText('AFTERHOURS / READING ROOM',95,150);
 c.fillStyle='#f5e9ce';c.font='86px "DM Serif Display", Georgia';let y=lines(c,book.title.split(':')[0],95,back?450:430,810,100,7);
 c.fillStyle='#c9b98f';c.font='32px "DM Sans Variable", sans-serif';lines(c,book.author,95,y+110,800,48,3);
 if(back){c.fillStyle='#d6c7a1';c.font='26px "DM Serif Display", Georgia';lines(c,'A book from the Afterhours reading room.',95,1130,760,43);c.font='18px "DM Mono", monospace';c.fillText('OWNER-CURATED METADATA',95,1340)}
})}
function edgeTexture(){return texture(c=>{c.fillStyle='#e6dcc0';c.fillRect(0,0,256,512);for(let y=0;y<512;y+=3){c.fillStyle=y%9===0?'#9f8e684d':'#fff7e566';c.fillRect(0,y,256,1)}},256,512)}
function spineTexture(book:Book){return texture(c=>{
 c.fillStyle=bindingColor(book.id);c.fillRect(0,0,256,1480);c.save();c.translate(144,120);c.rotate(Math.PI/2);c.fillStyle='#e9dbb6';c.font='56px "DM Serif Display", Georgia';let text=spineLabel(book.title);while(c.measureText(text).width>1180)text=text.slice(0,-1);c.fillText(text,0,0);c.restore();c.fillStyle='#bd9f67';c.fillRect(50,1350,156,3);
},256,1480)}

export default function BookScene3D({book,opened,returning,inspecting,origin,getSourceRect,onReady,onPreviewEnter,onPreviewLeave,onInspect,onOpen,onReturned,onAsk}:Props){
 const host=useRef<HTMLDivElement>(null);const hoverHit=useRef<HTMLButtonElement>(null);const state=useRef({opened,returning,inspecting,getSourceRect,onReady,onPreviewEnter,onPreviewLeave,onInspect,onOpen,onReturned,onAsk});state.current={opened,returning,inspecting,getSourceRect,onReady,onPreviewEnter,onPreviewLeave,onInspect,onOpen,onReturned,onAsk};
 const [spread,setSpread]=useState(0), [turning,setTurning]=useState(false);
 const changePage=useRef<(direction:number)=>void>(()=>{});
 const commands=useRef({rotate:(_amount:number)=>{},reset:()=>{},zoom:(_amount:number)=>{}});
 const [failed,setFailed]=useState(false),[readable,setReadable]=useState(false);
 useEffect(()=>{setSpread(0);setTurning(false);setReadable(false)},[book.id,opened]);
 const turn=(direction:number)=>{
  if(returning || !opened)return;
  if(failed)setSpread(value=>clamp(value+direction,0,spreads.length-1));
  else changePage.current(direction);
 };
 useEffect(()=>{if(failed&&returning)onReturned()},[failed,returning,onReturned]);
 useEffect(()=>{
  const el=host.current;if(!el)return;let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,powerPreference:'high-performance'})}catch{setFailed(true);return}
  let disposed=false;const textures:THREE.Texture[]=[];const register=(t:THREE.Texture)=>{textures.push(t);return t};
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.75));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.VSMShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.9;renderer.setClearColor(0,0);
  const canvas=renderer.domElement;canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Interactive 3D model of '+book.title+'. Drag to rotate, use arrow keys to turn, Enter to open, Page Down or Page Up to turn pages, and Home to reset.');canvas.dataset.renderer='webgl2';el.append(canvas);
  const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(32,1,.05,100);camera.position.set(0,.12,7.7);
  const envGenerator=new THREE.PMREMGenerator(renderer);const room=new RoomEnvironment();const environment=envGenerator.fromScene(room,.04);scene.environment=environment.texture;scene.environmentIntensity=.28;room.dispose();envGenerator.dispose();
  scene.add(new THREE.HemisphereLight('#fff2d8','#28372e',.85));const key=new THREE.DirectionalLight('#ffe3b1',2.3);key.position.set(-3,6,6);key.castShadow=true;key.shadow.mapSize.set(1536,1536);key.shadow.camera.left=-8;key.shadow.camera.right=8;key.shadow.camera.top=8;key.shadow.camera.bottom=-8;key.shadow.normalBias=.03;key.shadow.bias=-.0004;key.shadow.radius=5;key.shadow.blurSamples=8;scene.add(key);
  const rim=new THREE.DirectionalLight('#d4ad88',1.3);rim.position.set(5,2,-3);scene.add(rim);const fill=new THREE.DirectionalLight('#fff8ee',.45);fill.position.set(1,-1,7);scene.add(fill);
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(200,200),new THREE.ShadowMaterial({opacity:.35}));ground.rotation.x=-Math.PI/2;ground.position.y=-2.2;ground.receiveShadow=true;scene.add(ground);
  const ring=new THREE.Mesh(new THREE.RingGeometry(2.3,2.31,100),new THREE.MeshBasicMaterial({color:'#b28e55',transparent:true,opacity:.12,side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.y=-2.18;scene.add(ring);
  const root=new THREE.Group();scene.add(root);
  const cloth=new THREE.MeshPhysicalMaterial({color:bindingColor(book.id),roughness:.72,sheen:.45,sheenColor:new THREE.Color('#a78d56'),clearcoat:.08});
  const edges=register(edgeTexture());const pageEdge=new THREE.MeshStandardMaterial({map:edges,roughness:.92,color:'#f7efd9'});const pageSurface=new THREE.MeshStandardMaterial({color:paper,roughness:.9});
  const block=new THREE.Mesh(new THREE.BoxGeometry(W-.055,H-.07,D),[pageEdge,cloth,pageEdge,pageEdge,pageSurface,pageEdge]);block.castShadow=true;block.receiveShadow=true;root.add(block);
  const backMaterial=new THREE.MeshPhysicalMaterial({map:register(jacketTexture(book,true)),roughness:.54,clearcoat:.2});const back=new THREE.Mesh(new THREE.BoxGeometry(W+.035,H+.035,.045),[cloth,cloth,cloth,cloth,cloth,backMaterial]);back.position.z=-D/2-.022;back.castShadow=true;root.add(back);
  const coverMaterial=new THREE.MeshPhysicalMaterial({map:register(jacketTexture(book)),roughness:.65,clearcoat:.12,clearcoatRoughness:.6});
  const liner=register(endpaper(book));
  const pageArt=[
   [liner,register(titlePage(book))],
   [register(noteTexture(book)),register(detailTexture(book))],
   [register(colophon(book)),liner],
  ];
  const verso=new THREE.MeshStandardMaterial({map:pageArt[0][0],roughness:.93});
  const recto=new THREE.MeshStandardMaterial({map:pageArt[0][1],roughness:.92});
  const hinge=new THREE.Group();hinge.position.set(-W/2,0,D/2+.02);root.add(hinge);
  const cover=new THREE.Mesh(new THREE.BoxGeometry(W+.025,H+.025,.046),[cloth,cloth,cloth,cloth,coverMaterial,verso]);
  cover.position.x=W/2;cover.castShadow=true;cover.receiveShadow=true;hinge.add(cover);
  const detail=new THREE.Mesh(new THREE.PlaneGeometry(W-.065,H-.085),recto);
  detail.position.z=D/2+.009; detail.receiveShadow=true;root.add(detail);
  const leaf=createLeaf(W-.065,H-.085);leaf.group.visible=false;
  leaf.group.position.set(-W/2+.0325,0,D/2+.05);root.add(leaf.group);
  // Several unbound tips follow the cover with progressively later lift.
  // These thin flyleaves fan out during opening and fall back onto the stack.
  const flyleaves=Array.from({length:3},(_,i)=>{
   const sheet=createLeaf(W-.074-i*.007,H-.092-i*.005);
   sheet.front.color.set(i===0?'#e8d7ad':'#f4e8ce');sheet.back.color.copy(sheet.front.color);
   sheet.group.position.set(-W/2+.037,0,D/2+.016+i*.003);
   sheet.group.visible=false;root.add(sheet.group);return sheet;
  });
  const reverseArt=pageArt.slice(1).map(([image])=>{
   const reverse=register(image.clone()); reverse.channel=1;return reverse;
  });
  let settled=0,turnProgress=0,turnGoal=0,from=0,to=0,dragCorner=0,peeling=false;
  function presentSpread(index:number) {
   settled=index; from=index;to=index;peeling=false;
   verso.map=pageArt[index][0]; recto.map=pageArt[index][1];
   leaf.group.visible=false;setSpread(index);setTurning(false);
  }
  function startTurn(direction:number,drag=false) {
   const dest=settled+direction;
   if(from!==to || !state.current.opened || state.current.returning || dest<0 || dest>=spreads.length)return false;
   from=settled;to=dest;peeling=drag;dragCorner=1;
   const first=Math.min(from,to),last=first+1;
   // Both static pages beneath the lifted leaf and both sides of the leaf
   // keep their own artwork. No texture swap is visible at the halfway point.
   verso.map=pageArt[first][0];recto.map=pageArt[last][1];
   leaf.front.map=pageArt[first][1];leaf.back.map=reverseArt[first];
   leaf.front.needsUpdate=true;leaf.back.needsUpdate=true;
   turnProgress=direction>0?0:1;turnGoal=direction>0?1:0;
   leaf.group.visible=true;setTurning(true);return true;
  }
  changePage.current=direction=>{startTurn(direction)};
  const spineFallback=new THREE.MeshStandardMaterial({map:register(spineTexture(book)),roughness:.8});const spineGeom=new THREE.PlaneGeometry(D+.065,H+.01,12,24);const spine=new THREE.Mesh(spineGeom,spineFallback);spine.rotation.y=-Math.PI/2;spine.position.x=-W/2-.004;root.add(spine);
  const loader=new THREE.TextureLoader();loader.setCrossOrigin('anonymous');
  if(book.cover)loader.load('/api/bookshelf/cover/'+book.id,t=>{if(disposed){t.dispose();return}t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=renderer.capabilities.getMaxAnisotropy();register(t);coverMaterial.map=t;coverMaterial.needsUpdate=true},undefined,()=>{});
  if(book.spine){const ref=book.spine;loader.load(ref.url,t=>{if(disposed){t.dispose();return}t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=8;register(t);const uv=spineGeom.attributes.uv;for(let i=0;i<uv.count;i++){const u=uv.getX(i),v=1-uv.getY(i);const top=new THREE.Vector2(...ref.points[0]).lerp(new THREE.Vector2(...ref.points[1]),u),bottom=new THREE.Vector2(...ref.points[3]).lerp(new THREE.Vector2(...ref.points[2]),u);const point=top.lerp(bottom,v);uv.setXY(i,point.x/ref.width,1-point.y/ref.height)}uv.needsUpdate=true;spineFallback.map=t;spineFallback.needsUpdate=true},undefined,()=>{})}
  let width=0,height=0;const resize=()=>{const nextWidth=el.clientWidth,nextHeight=el.clientHeight;if(nextWidth<1||nextHeight<1)return;width=nextWidth;height=nextHeight;renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix()};const observer=new ResizeObserver(resize);observer.observe(el);resize();
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');let reduce=reduced.matches;const motionChange=()=>{reduce=reduced.matches};reduced.addEventListener('change',motionChange);
  let dragX=0,dragY=0,zoom=0,dragging=false,moved=false,startX=0,startY=0,lastX=0,lastY=0;const pointer=new THREE.Vector2(),ray=new THREE.Raycaster();
  const reset=()=>{dragX=0;dragY=0;zoom=0};commands.current={rotate:a=>{dragX+=a},reset,zoom:a=>{zoom=clamp(zoom+a,-1.3,2)}};
  const pageHit=(e:PointerEvent)=>{
   const bounds=canvas.getBoundingClientRect();
   pointer.set((e.clientX-bounds.left)/width*2-1,-(e.clientY-bounds.top)/height*2+1);
   ray.setFromCamera(pointer,camera);
   const hit=ray.intersectObjects([detail,cover])[0];
   if(!hit?.uv || !state.current.opened || angle<.95)return 0;
   const right=hit.object===detail;
   return right&&hit.uv.x>.58?1:!right&&hit.uv.x<.42?-1:0;
  };
  const down=(e:PointerEvent)=>{if(e.button!==0||!state.current.inspecting)return;dragging=true;moved=false;startX=lastX=e.clientX;startY=lastY=e.clientY;const side=pageHit(e);if(side && startTurn(side,true))dragCorner=e.clientY<height/2?-1:1;canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing'};
  const move=(e:PointerEvent)=>{if(!dragging)return;if(Math.hypot(e.clientX-startX,e.clientY-startY)>5)moved=true;if(peeling){const span=Math.min(width*.42,340);turnProgress=clamp((from<to?0:1)+(startX-e.clientX)/span,0,1);return}dragX+=(e.clientX-lastX)*.007;dragY=clamp(dragY+(e.clientY-lastY)*.004,-.5,.5);lastX=e.clientX;lastY=e.clientY};
  const up=(e:PointerEvent)=>{if(!dragging)return;dragging=false;canvas.style.cursor='grab';if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);if(peeling){peeling=false;const commit=!moved || (from<to?turnProgress>.18:turnProgress<.82);turnGoal=commit?(from<to?1:0):(from<to?0:1);return}if(!moved&&!state.current.returning){const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);if(!state.current.opened&&ray.intersectObject(root,true).length)state.current.onOpen();}};
  const cancel=()=>{if(peeling){peeling=false;turnGoal=from<to?0:1}dragging=false;canvas.style.cursor='grab'};
  const keys=(e:KeyboardEvent)=>{if(state.current.opened&&['PageDown','PageUp'].includes(e.key)){e.preventDefault();startTurn(e.key==='PageDown'?1:-1);return}if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','Enter','+','-'].includes(e.key))e.preventDefault();if(e.key==='ArrowLeft')dragX-=.18;if(e.key==='ArrowRight')dragX+=.18;if(e.key==='ArrowUp')dragY=clamp(dragY-.12,-.5,.5);if(e.key==='ArrowDown')dragY=clamp(dragY+.12,-.5,.5);if(e.key==='Home')reset();if(e.key==='Enter'&&!state.current.opened)state.current.onOpen();if(e.key==='+')zoom=clamp(zoom+.25,-1.3,2);if(e.key==='-')zoom=clamp(zoom-.25,-1.3,2)};
  canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',cancel);canvas.addEventListener('keydown',keys);const lost=(e:Event)=>{e.preventDefault();renderer.setAnimationLoop(null);setFailed(true)};canvas.addEventListener('webglcontextlost',lost);
  const createdAt=performance.now();let angle=0,angleVelocity=0,previous=0,returnAt:number|null=null,returned=false,frame=0,wasOpened=false,ready=false;
  const pose={x:0,y:0,z:0,scale:1,yaw:origin.face==='spine'?Math.PI/2:-.12,pitch:0};
  const velocity={x:0,y:0,z:0,scale:0,yaw:0,pitch:0};let initialized=false,inspectionMix=0,inspectedAt:number|null=null;
  const spring=(key:keyof typeof pose,target:number,dt:number,frequency=13)=>{if(reduce){pose[key]=target;velocity[key]=0;return}const offset=pose[key]-target,decay=Math.exp(-frequency*dt),temp=(velocity[key]+frequency*offset)*dt;pose[key]=target+(offset+temp)*decay;velocity[key]=(velocity[key]-frequency*temp)*decay};
  renderer.setAnimationLoop(()=>{
   if(disposed||document.hidden||width<1||height<1)return;const elapsed=(performance.now()-createdAt)/1000,dt=Math.min(.033,Math.max(0,elapsed-previous));previous=elapsed;
   if(state.current.opened!==wasOpened){wasOpened=state.current.opened;reset();presentSpread(0)}
   if(state.current.returning&&returnAt===null)returnAt=elapsed;if(!state.current.returning){returnAt=null;returned=false}
   const retreat=returnAt!==null,returnTime=retreat?elapsed-returnAt!:0;
   if(state.current.inspecting && inspectedAt===null)inspectedAt=elapsed;
   const target=(state.current.opened&&!retreat)?1:0;
   if(reduce){angle=target;angleVelocity=0}else{angleVelocity+=(target-angle)*82*dt;angleVelocity*=Math.exp(-15*dt);angle+=angleVelocity*dt}
   const open=clamp(angle,0,1.025);hinge.rotation.y=-Math.PI*.988*open;
   const fanning=open>.035 && open<.94 && !reduce;
   flyleaves.forEach((sheet,i)=>{
    sheet.group.visible=fanning;
    if(fanning) {
     const phase=clamp((open-.035-i*.045)/(.90-i*.045),0,1);
     sheet.bend(Math.sin(phase*Math.PI)**1.8*(.19-i*.045),1);
    }
   });
   if(from!==to) {
    if(retreat){presentSpread(settled)}
    else{
     const distance=turnGoal-turnProgress;
     if(reduce&&!peeling)turnProgress=turnGoal;
     else if(!peeling)turnProgress+=Math.sign(distance)*Math.min(Math.abs(distance),dt*1.32);
     leaf.bend(smooth(turnProgress),dragCorner);
     if(!peeling && turnProgress===turnGoal)presentSpread(turnGoal===1?Math.max(from,to):Math.min(from,to));
    }
   }
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
   const beat=reduce?1:clamp((elapsed-(inspectedAt??elapsed))/.93,0,1);
   const lift=Math.sin(Math.PI*beat);
   inspectionMix=reduce?Number(state.current.inspecting&&!retreat):
    THREE.MathUtils.damp(inspectionMix,Number(state.current.inspecting&&!retreat),6,dt);
   const inspectionScale=Math.min(height*.61,555)*units/H;
   const openScale=Math.min(inspectionScale,(width-32)*units/(W*2.2));
   const inspection={x:open*W/2*openScale,y:height*.05*units+lift*.27,z:lift*.72,scale:THREE.MathUtils.lerp(inspectionScale,openScale,smooth(open)),yaw:.20*(1-open)-.035*open+dragX+.32*lift,pitch:-.045+dragY-.17*lift};
   const atShelf=retreat&&(!state.current.inspecting||returnTime>.28);
   const destination=atShelf?{x:sourceX,y:anchorY,z:sourceZ,scale:sourceScale,yaw:initialYaw,pitch:0}:state.current.inspecting?inspection:{x:hoverX,y:anchorY+8*units*hoverProgress,z:hoverZ,scale:hoverScale,yaw:hoverYaw,pitch:-.025*hoverProgress};
   if(!initialized){Object.assign(pose,{x:sourceX,y:anchorY,z:sourceZ,scale:sourceScale,yaw:initialYaw,pitch:0});initialized=true}
   for(const key of Object.keys(pose) as (keyof typeof pose)[])spring(key,destination[key],dt,state.current.inspecting?9.5:16);
   root.position.set(pose.x,pose.y,pose.z);root.scale.setScalar(pose.scale*(1+zoom*.12));root.rotation.set(pose.pitch,pose.yaw,.042*lift*inspectionMix);key.intensity=2+.65*inspectionMix;
   ground.visible=state.current.inspecting;ring.visible=state.current.inspecting;ground.position.y=-H/2*pose.scale-.15;ring.position.y=ground.position.y+.02;ring.scale.setScalar(pose.scale);
   if(++frame%4===0){canvas.dataset.page=String(settled);canvas.dataset.turn=from===to?'idle':turnProgress.toFixed(2);canvas.dataset.phase=retreat?'returning':state.current.inspecting?(target?(Math.abs(open-1)<.015?'open':'opening'):'cover'):'hover';canvas.dataset.rotation=root.rotation.y.toFixed(3);canvas.dataset.open=open.toFixed(3)}
   renderer.render(scene,camera);
   if(!state.current.inspecting&&hoverHit.current){const box=new THREE.Box3().setFromObject(root);let left=Infinity,top=Infinity,right=-Infinity,bottom=-Infinity;for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z]){const p=new THREE.Vector3(x,y,z).project(camera),sx=(p.x+1)*width/2,sy=(1-p.y)*height/2;left=Math.min(left,sx);right=Math.max(right,sx);top=Math.min(top,sy);bottom=Math.max(bottom,sy)}Object.assign(hoverHit.current.style,{left:(left-10)+'px',top:(top-10)+'px',width:(right-left+20)+'px',height:(bottom-top+20)+'px'})}
   if(!ready){ready=true;state.current.onReady()}
   const error=Math.abs(pose.x-sourceX)+Math.abs(pose.y-anchorY)+Math.abs(pose.z-sourceZ)+Math.abs(pose.scale-sourceScale)+Math.abs(pose.yaw-initialYaw);
   if(retreat&&atShelf&&(reduce||(returnTime>.35&&error<.012))&&!returned){returned=true;state.current.onReturned()}
  });
  return()=>{disposed=true;renderer.setAnimationLoop(null);observer.disconnect();reduced.removeEventListener('change',motionChange);canvas.removeEventListener('pointerdown',down);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',up);canvas.removeEventListener('pointercancel',cancel);canvas.removeEventListener('keydown',keys);canvas.removeEventListener('webglcontextlost',lost);const materials=new Set<THREE.Material>(),geometries=new Set<THREE.BufferGeometry>();scene.traverse(o=>{if(o instanceof THREE.Mesh){geometries.add(o.geometry);for(const m of Array.isArray(o.material)?o.material:[o.material])materials.add(m)}});geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());environment.dispose();renderer.dispose();renderer.forceContextLoss();canvas.remove()};
 },[book.id]);
 const showText=opened&&(readable||failed);
 return <div className={'game-book-viewer '+(failed?'game-fallback':'')}>
  {!inspecting&&!failed&&<button ref={hoverHit} className="hover-book-hit" aria-label={'Pick up '+book.title} onPointerEnter={onPreviewEnter} onPointerLeave={onPreviewLeave} onFocus={onPreviewEnter} onBlur={onPreviewLeave} onClick={onInspect}/>}
  {!failed?<><div className="three-book-stage" ref={host}/><div className="inspection-hud"><span>{returning?'Returning to the shelf…':opened?'Drag a page corner to turn it':'Drag to rotate · select the cover to open'}</span><div className="inspection-tools" aria-label="3D book controls"><button aria-label="Rotate book left" onClick={()=>commands.current.rotate(-.3)}>↶</button><button aria-label="Reset book view" onClick={()=>commands.current.reset()}>◎</button><button aria-label="Rotate book right" onClick={()=>commands.current.rotate(.3)}>↷</button><button aria-label="Zoom in on book" onClick={()=>commands.current.zoom(.35)}>+</button><button aria-label="Zoom out from book" onClick={()=>commands.current.zoom(-.35)}>−</button>{opened&&<button className="read-notes-button" aria-expanded={readable} onClick={()=>setReadable(!readable)}>{readable?'Hide text':'Read text'}</button>}</div></div></>:inspecting&&!opened?<button className="fallback-jacket" onClick={onOpen} aria-label={'Open '+book.title}>{book.cover?<img src={book.cover} alt={'Cover of '+book.title}/>:<strong>{book.title}</strong>}</button>:null}
  {opened && inspecting && <nav className="folio-nav" aria-label="Book pages">
   <button aria-label="Previous spread" disabled={returning||turning||spread===0} onClick={()=>turn(-1)}>←</button>
   <span aria-live="polite">{spreads[spread]} <small>{spread+1} / {spreads.length}</small></span>
   <button aria-label="Next spread" disabled={returning||turning||spread===spreads.length-1} onClick={()=>turn(1)}>→</button>
  </nav>}
  {opened && <section className={showText?'readable-book-notes':'sr-only'} aria-label="Book notes and details" hidden={!showText}>
   <div><h3>{book.title}</h3><p>{book.author}</p>
    {spread===1 && (book.comment?<><blockquote>{book.comment}</blockquote><small>{book.comment_source}</small></>:<p>No personal comment recorded yet.</p>)}
    {spread===2 && book.spine && <a href={book.spine.source} target="_blank" rel="noreferrer">Spine photo reference ↗</a>}
   </div><div>
    {spread===1 && <dl>{[['Reading',book.status],['Recommendation',book.recommendation]].map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value==='unknown'?'Not recorded':value}</dd></div>)}</dl>}
    {spread===2 && <dl>{[['Catalog source',book.source],['Notes from',book.comment_source],['Subjects',book.note_tags?.join(' · ')],['Edition',book.sample?'Sample':'']].map(([key,value])=>value&&<div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>}
    <button className="readable-agent" onClick={onAsk}>Ask about this book ↗</button>
   </div>
  </section>}
 </div>
}
