/** Progressive enhancement: current WebMCP draft + older navigator hosts. */
type Tool = {name:string;description:string;inputSchema:Record<string,unknown>;annotations?:Record<string,unknown>;execute:(args:Record<string,unknown>)=>Promise<unknown>};
type ModelContext={registerTool:(tool:Tool,options?:{signal:AbortSignal})=>unknown;unregisterTool?:(name:string)=>unknown};
type Actions={showBooks:(ids:string[])=>void;openBook:(id:string)=>Promise<void>;state:()=>unknown};
export async function registerPortfolioTools(actions:Actions):Promise<()=>void>{
 const context=(document as unknown as {modelContext?:ModelContext}).modelContext||(navigator as unknown as {modelContext?:ModelContext}).modelContext;
 if(!context?.registerTool)return ()=>{};
 const registered:string[]=[];
 const registration=new AbortController();
 const request=async(path:string,options?:RequestInit)=>{const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Public tool unavailable.');return data};
 const read=(path:string,args:Record<string,unknown>)=>request(path+'?'+new URLSearchParams(Object.entries(args).filter(([,v])=>v!==undefined).map(([k,v])=>[k,String(v)])));
 const execute:Record<string,Tool['execute']>={
  get_profile:()=>request('/api/public/profile'),
  search_knowledge:args=>read('/api/public/knowledge',args),
  browse_bookshelf:args=>read('/api/public/bookshelf',args),
  search_library:args=>read('/api/public/library',args),
  ask_portfolio_agent:args=>request('/api/public/questions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)}),
  get_answer:args=>request('/api/public/answers/'+encodeURIComponent(String(args.turn_id)),{headers:{'X-Result-Token':String(args.result_token)}}),
 };
 const catalog=await request('/api/public/tools');
 const add=async(tool:Tool)=>{await context.registerTool(tool,{signal:registration.signal});registered.push(tool.name)};
 for(const tool of catalog.tools){if(!execute[tool.name])continue;await add({name:tool.name,description:tool.description,inputSchema:tool.inputSchema,annotations:{readOnlyHint:tool.name!=='ask_portfolio_agent',untrustedContentHint:true},execute:execute[tool.name]})}
 await add({name:'show_books',description:'Select up to eight currently published book IDs on this browser page. Temporary UI selection only; never edits the library.',inputSchema:{type:'object',properties:{book_ids:{type:'array',items:{type:'string',maxLength:80},minItems:1,maxItems:8}},required:['book_ids'],additionalProperties:false},annotations:{readOnlyHint:false},execute:async args=>{const ids=args.book_ids;if(!Array.isArray(ids)||!ids.length||ids.length>8||ids.some(id=>typeof id!=='string'))throw new Error('Use one to eight published IDs.');const books=await request('/api/bookshelf');const allowed=new Set(books.books.map((b:{id:string})=>b.id));if(ids.some(id=>!allowed.has(id)))throw new Error('Book is not currently published.');actions.showBooks(ids);return {status:'selected',book_ids:ids}}});
 await add({name:'open_book',description:'Open one published book in this browser page’s 3D bookshelf inspector. Does not change library data.',inputSchema:{type:'object',properties:{book_id:{type:'string',minLength:1,maxLength:80}},required:['book_id'],additionalProperties:false},annotations:{readOnlyHint:false},execute:async args=>{if(typeof args.book_id!=='string'||args.book_id.length>80)throw new Error('Use a published book ID.');await actions.openBook(args.book_id);return {status:'opened',book_id:args.book_id}}});
 await add({name:'get_site_state',description:'Read this browser page’s active portfolio section and current bookshelf selection. Never returns conversation history, owner credentials, or private-mode state.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true},execute:async()=>actions.state()});
 return ()=>{registration.abort();for(const name of registered)context.unregisterTool?.(name)};
}
