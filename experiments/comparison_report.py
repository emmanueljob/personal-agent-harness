"""Render private comparison results as a self-contained, non-executable report."""
from html import escape


def render(report):
    cases={}
    for result in report['results']:cases.setdefault(result['case'],{})[result['engine']]=result
    sections=[]
    for name,engines in cases.items():
        cards=[]
        for engine in ('baseline','deepagents'):
            result=engines.get(engine)
            if not result:continue
            checks=', '.join(key.replace('_',' ')+': '+('pass' if value else 'FAIL') for key,value in result['checks'].items())
            activities=', '.join(f'{key}: {value}' for key,value in result['history']['completed'].items())
            answer=result['turn'].get('result') or 'No completed answer.'
            recovery='Worker recovery verified; completed tool was not repeated.' if result.get('recovery_verified') else ''
            artifacts=''.join(f'<details><summary>Generated document: {escape(a["title"])}</summary><pre>{escape(a["body"])}</pre></details>' for a in result.get('artifacts',[]))
            cards.append(f'<article><span class="label">{escape(engine)}</span><h3>{escape(result["status"])}</h3><div class="numbers"><b>{result["seconds"]}s</b><span>{result["model_calls"]} completed model calls</span><span>{result["prompt_tokens"]:,} input tokens</span><span>{result["output_tokens"]:,} output tokens</span></div><p class="checks">{escape(checks)}</p><p>{escape(recovery)}</p><pre>{escape(answer)}</pre>{artifacts}<details><summary>Execution evidence</summary><p>{escape(activities)}</p><p>Initial context SHA-256: {escape(result["initial_context_sha256"])}</p></details></article>')
        sections.append(f'<section id="{escape(name)}"><h2>{escape(name.title())}</h2><div class="pair">'+''.join(cards)+'</div></section>')
    controls=''.join('<li>'+escape(item)+'</li>' for item in report['controls'])
    limits=''.join('<li>'+escape(item)+'</li>' for item in report['limitations'])
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Agent runtime comparison</title><style>*{box-sizing:border-box}body{margin:0;background:#f7f5ee;color:#2c3028;font:14px/1.7 system-ui}main{max-width:1250px;margin:auto;padding:45px 30px}.label{font:10px monospace;letter-spacing:2px;text-transform:uppercase;color:#87602d}h1{font:54px/1.05 Georgia;margin:18px 0}header>p{max-width:760px;color:#656e5e}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{padding:24px;border:1px solid #d6daca;background:#fffdf6;min-width:0}h2{font:32px Georgia;margin:42px 0 18px}h3{font:24px Georgia;margin:12px 0}.numbers{display:flex;flex-wrap:wrap;gap:8px 17px;border-block:1px solid #d6daca;padding:15px 0;font-size:11px}.numbers b{font-size:20px;color:#87602d}.numbers span{align-self:center}pre{font:13px/1.9 system-ui;white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;color:#87602d}details p{font:10px/1.8 monospace;overflow-wrap:anywhere}.checks{font-size:11px;color:#53694b}footer{margin:40px 0;color:#656e5e}.notes{display:grid;grid-template-columns:1fr 1fr;gap:30px}@media(max-width:700px){.pair,.notes{grid-template-columns:1fr}h1{font-size:38px}main{padding:30px 18px}}</style><main><header><span class="label">Private local experiment</span><h1>Two runtimes.<br>The same application.</h1><p>The existing LangChain/LangGraph turn runner versus Deep Agents with per-call Temporal Activities. These are small smoke cases, not a performance ranking. Shared model caching, run order and a deliberately interrupted worker affect timings.</p></header>'''+''.join(sections)+f'<footer><div class="notes"><div><h2>Controls</h2><ul>{controls}</ul></div><div><h2>Limits</h2><ul>{limits}</ul></div></div></footer></main></html>'
