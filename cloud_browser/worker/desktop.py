"""A native Chrome/X11 desktop. Browser control belongs to the owner.

Chrome runs directly, without Playwright, automation flags, fingerprint overrides,
or a headless mode. The private DevTools endpoint is used only for explicitly
started session capture and sign-out. It is never proxied to the admin client.
"""
import asyncio
import contextlib
import json
import os
import pwd
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

WIDTH=1280
HEIGHT=900
KEYS={'Enter':'Return','Tab':'Tab','Backspace':'BackSpace','Escape':'Escape','ControlOrMeta+A':'ctrl+a','ArrowDown':'Down','ArrowUp':'Up'}


def browser_environment(root):
    # Never give browser/desktop children the worker or CRM bearer credentials.
    return {'PATH':os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin'),'HOME':pwd.getpwuid(os.getuid()).pw_dir,'LANG':'en_US.UTF-8','DISPLAY':':99','XAUTHORITY':str(root/'xauthority'),'XDG_RUNTIME_DIR':str(root/'run')}


def chrome_command(root):
    return ['google-chrome-stable','--user-data-dir='+str(root/'chrome-desktop'),'--no-first-run','--no-default-browser-check','--disable-dev-shm-usage','--remote-debugging-address=127.0.0.1','--remote-debugging-port=9222','--start-maximized','https://www.linkedin.com/login']


def linkedin_cookies(rows):
    return {row['name']:row['value'] for row in rows if row.get('domain','').lstrip('.') in ('linkedin.com','www.linkedin.com') or row.get('domain','').endswith('.linkedin.com')}


class Desktop:
    def __init__(self,root):
        self.root=Path(root);self.env=browser_environment(self.root);self.processes=[]
        self.version='';self.user_agent=''

    async def run(self,*command,data=None,timeout=15):
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=self.env)
        try:out,_=await asyncio.wait_for(process.communicate(data),timeout)
        except BaseException:
            process.kill();await process.wait();raise
        if process.returncode:raise RuntimeError('Desktop command failed.')
        return out

    async def spawn(self,*command):
        fd=os.open(self.root/'desktop-errors.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        try:process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=fd,env=self.env)
        finally:os.close(fd)
        self.processes.append(process)
        return process

    async def start(self):
        run=self.root/'run';run.mkdir(mode=0o700,exist_ok=True);os.chmod(run,0o700)
        auth=self.root/'xauthority';fd=os.open(auth,os.O_CREAT|os.O_WRONLY,0o600);os.close(fd);os.chmod(auth,0o600)
        await self.run('xauth','-f',str(auth),'add',':99','.',secrets.token_hex(16))
        await self.spawn('Xvfb',':99','-screen','0',f'{WIDTH}x{HEIGHT}x24','-nolisten','tcp','-auth',str(auth))
        for _ in range(60):
            try:await self.run('xdpyinfo',timeout=2);break
            except RuntimeError:await asyncio.sleep(.25)
        else:raise RuntimeError('Private display did not start.')
        await self.spawn('openbox','--sm-disable')
        chrome=await self.spawn(*chrome_command(self.root))
        async with httpx.AsyncClient(trust_env=False,timeout=2) as client:
            for _ in range(120):
                if chrome.returncode is not None:raise RuntimeError('Chrome did not start. The sandbox remains enabled.')
                try:
                    response=await client.get('http://127.0.0.1:9222/json/version');response.raise_for_status();version=response.json()
                    self.version=version.get('Browser','');self.user_agent=version.get('User-Agent','');break
                except (httpx.HTTPError,ValueError):await asyncio.sleep(.5)
            else:raise RuntimeError('Chrome did not become ready.')
        await self.spawn('x11vnc','-display',':99','-auth',str(auth),'-localhost','-rfbport','5900','-forever','-shared','-nopw','-noxdamage','-quiet')
        for _ in range(60):
            try:
                reader,writer=await asyncio.open_connection('127.0.0.1',5900)
                banner=await asyncio.wait_for(reader.read(12),2);writer.close();await writer.wait_closed()
                if banner.startswith(b'RFB '):return
            except (OSError,asyncio.TimeoutError):pass
            await asyncio.sleep(.25)
        raise RuntimeError('Private desktop did not become ready.')

    def healthy(self):return bool(self.processes) and all(p.returncode is None for p in self.processes)

    async def close(self):
        # Preserve the display while Chrome exits and commits its profile. Killing
        # Xvfb first can turn a normal shutdown into a browser crash.
        for process in reversed(self.processes):
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):process.terminate()
            try:await asyncio.wait_for(process.wait(),8)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):process.kill()
                await process.wait()

    async def cdp(self,method):
        if method not in ('Storage.getCookies','Network.clearBrowserCookies'):raise ValueError('Unsupported session operation.')
        async with httpx.AsyncClient(trust_env=False,timeout=5) as client:
            response=await client.get('http://127.0.0.1:9222/json/version');response.raise_for_status();data=response.json()
        url=data['webSocketDebuggerUrl'];parsed=urlsplit(url)
        if parsed.scheme!='ws' or parsed.hostname!='127.0.0.1' or parsed.port!=9222 or not parsed.path.startswith('/devtools/browser/'):raise ValueError('Unexpected local browser endpoint.')
        async with connect(url,proxy=None,max_size=2_000_000,open_timeout=5) as socket:
            await socket.send(json.dumps({'id':1,'method':method}))
            async with asyncio.timeout(10):
                async for raw in socket:
                    result=json.loads(raw)
                    if result.get('id')==1:
                        if 'error' in result:raise ValueError('Browser session operation failed.')
                        return result.get('result',{})
        raise ValueError('Browser session operation did not finish.')

    async def session(self):
        result=await self.cdp('Storage.getCookies')
        return linkedin_cookies(result.get('cookies',[])),self.user_agent

    async def clear_cookies(self):await self.cdp('Network.clearBrowserCookies')

    async def screenshot(self):return await self.run('import','-window','root','-quality','65','jpeg:-')

    async def control(self,body):
        if body.action=='navigate':
            parsed=urlsplit(body.url)
            if parsed.scheme!='https' or parsed.hostname not in ('linkedin.com','www.linkedin.com') or parsed.port not in (None,443):raise ValueError('Use a LinkedIn HTTPS URL.')
            await self.run('xdotool','key','--clearmodifiers','ctrl+l')
            await self.paste(body.url)
            await self.run('xdotool','key','Return')
        elif body.action=='click':await self.run('xdotool','mousemove',str(body.x),str(body.y),'click','1')
        elif body.action=='type':await self.paste(body.text)
        elif body.action=='key' and body.key:await self.run('xdotool','key','--clearmodifiers',KEYS[body.key])
        elif body.action=='scroll':await self.run('xdotool','click','--repeat',str(max(1,abs(body.delta)//100)),'--delay','30','5' if body.delta>0 else '4')

    async def paste(self,text):
        # Text is stdin, never a process argument, log, screenshot file, or profile preference.
        process=await asyncio.create_subprocess_exec('xclip','-selection','clipboard','-in','-quiet',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=self.env)
        try:
            process.stdin.write(text.encode());await process.stdin.drain();process.stdin.close()
            await asyncio.sleep(.1)
            await self.run('xdotool','key','--clearmodifiers','ctrl+v')
            await asyncio.sleep(.15)
        finally:
            # Terminating the clipboard owner clears the private clipboard.
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):process.terminate()
            await process.wait()
