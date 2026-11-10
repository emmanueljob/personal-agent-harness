"""Read-side LinkedIn protocol; unknown schemas fail closed, never imply an empty inbox.

Protocol references: allman-cli (MIT), see THIRD_PARTY_NOTICES.md.
No send-message or mark-read operations are implemented.
"""
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

BASE='https://www.linkedin.com'
GRAPHQL=BASE+'/voyager/api/voyagerMessagingGraphQL/graphql'
QUERIES={'conversations':'messengerConversations.45338e053010d1c19147f92de6de3ae6','messages':'messengerMessages.90abe2bc64df3bc3e1323a1479989b49'}

class SchemaError(ValueError):pass
class LoginRequired(ValueError):pass


def profile(urn):return str(urn or '').replace('urn:li:fs_miniProfile:','urn:li:fsd_profile:')


def bare_thread(urn):
    value=str(urn or '')
    if value.startswith('urn:li:messagingThread:'):return value.split(':',3)[3]
    match=re.fullmatch(r'urn:li:msg_conversation:\(urn:li:fsd_profile:[^,]+,([^()]+)\)',value)
    if match:return match[1]
    raise SchemaError('Unrecognized conversation identifier.')


def thread_urn(urn):return 'urn:li:messagingThread:'+bare_thread(urn)


def me(value):
    scope=value.get('data') if isinstance(value.get('data'),dict) else value
    mini=scope.get('miniProfile')
    reference=scope.get('*miniProfile')
    if reference:
        candidates=[row for row in value.get('included',[]) if isinstance(row,dict) and row.get('entityUrn')==reference]
        if len(candidates)!=1:raise SchemaError('Signed-in profile reference could not be resolved.')
        if mini and mini.get('entityUrn')!=reference:raise SchemaError('Signed-in profile identities disagree.')
        mini=candidates[0]
    urn=profile(mini.get('entityUrn')) if isinstance(mini,dict) else ''
    if not re.fullmatch(r'urn:li:fsd_profile:[A-Za-z0-9_-]+',urn):raise SchemaError('Cannot identify the signed-in LinkedIn account.')
    return urn


def collection(value,key):
    result=value.get('data',{}).get('data',{}).get(key)
    if not isinstance(result,dict) or not isinstance(result.get('*elements'),list) or not isinstance(value.get('included'),list):raise SchemaError('LinkedIn response format changed; capture needs attention.')
    included={r['entityUrn']:r for r in value['included'] if isinstance(r,dict) and isinstance(r.get('entityUrn'),str)}
    rows=[]
    for urn in result['*elements']:
        if urn not in included:raise SchemaError('LinkedIn response has unresolved records.')
        rows.append(included[urn])
    return rows,included,result.get('metadata') or {}


def text(value):return value.get('text','') if isinstance(value,dict) else value if isinstance(value,str) else ''


def participant(row):
    member=row.get('participantType',{}).get('member',{})
    address={'name':(' '.join(filter(None,[text(member.get('firstName')),text(member.get('lastName'))])))[:200],'profile_url':member.get('profileUrl') or ''}
    if address['profile_url']:
        parsed=urlsplit(address['profile_url'])
        if parsed.scheme!='https' or parsed.hostname not in ('www.linkedin.com','linkedin.com') or not parsed.path.startswith('/in/'):address['profile_url']=''
    return profile(row.get('hostIdentityUrn')),address


def conversations(value):
    rows,included,meta=collection(value,'messengerConversationsByCategoryQuery')
    result=[]
    for row in rows:
        urn=thread_urn(row.get('backendUrn') or row.get('entityUrn'))
        peers=[included[ref] for ref in row.get('*conversationParticipants',[]) if ref in included]
        result.append({'thread':urn,'peers':peers,'subject':text(row.get('title'))[:1000]})
    return result,meta.get('nextCursor')


def messages(value,account,thread,peers,subject=''):
    rows,included,meta=collection(value,'messengerMessagesByAnchorTimestamp')
    roster={}
    for person in peers+list(included.values()):
        ident,address=participant(person)
        if ident:roster[ident]=address
    out=[];dates=[]
    for row in rows:
        sender_row=included.get(row.get('*sender') or row.get('*actor'))
        if not sender_row:raise SchemaError('Message sender could not be resolved.')
        sender,address=participant(sender_row)
        if not sender:raise SchemaError('Message sender has no profile identity.')
        ident=row.get('backendUrn')
        if not isinstance(ident,str) or not ident:raise SchemaError('Message has no stable backend identifier.')
        stamp=row.get('deliveredAt') or row.get('createdAt')
        if not isinstance(stamp,(int,float)) or stamp<=0:raise SchemaError('Message timestamp is missing.')
        dates.append(stamp)
        body=text(row.get('body')) or text(row.get('renderContentFallbackText'))
        outbound=sender==account
        recipients=[p for urn,p in roster.items() if urn!=sender]
        # Exclude included sender objects unrelated to the actual conversation.
        peer_ids=({participant(p)[0] for p in peers}|{account}) if peers else set(roster)
        recipients=[p for urn,p in roster.items() if urn!=sender and urn in peer_ids]
        if not recipients:recipients=[{'name':'LinkedIn member','profile_url':''}]
        out.append({'provider':'linkedin','account':account,'external_id':ident,'thread_id':thread_urn(thread),'direction':'outbound' if outbound else 'inbound','occurred_at':datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat(),'sender':address,'recipients':recipients,'subject':(text(row.get('subject')) or subject)[:1000],'text':body[:50000],'truncated':len(body)>50000 or bool(row.get('renderContent')),'source_url':BASE+'/messaging/thread/'+quote(bare_thread(thread),safe='')+'/'})
    return out,min(dates) if dates else None,bool(meta.get('previousCursor')) or len(rows)>=20


def query_url(kind,account,cursor='',thread='',anchor=0,query_id=''):
    query_id=query_id or QUERIES[kind]
    if not re.fullmatch(r'messenger(?:Conversations|Messages)\.[a-f0-9]+',query_id):raise SchemaError('Invalid messaging query identifier.')
    if kind=='conversations':
        variables='(query:(predicateUnions:List((conversationCategoryPredicate:(category:INBOX)))),count:20,mailboxUrn:'+quote(account,safe='')
        if cursor:variables+=',nextCursor:'+quote(cursor,safe='')
        else:variables+=',lastUpdatedBefore:'+str(int(time.time()*1000))
        variables+=')'
    else:
        conv='urn:li:msg_conversation:('+account+','+bare_thread(thread)+')'
        variables=f'(deliveredAt:{int(anchor)},conversationUrn:{quote(conv,safe="")},countBefore:20,countAfter:0)'
    return GRAPHQL+'?queryId='+query_id+'&variables='+variables


def sse_data(lines):
    """Parse SSE frames including multiline data. Callers bound each frame's size."""
    data=[]
    for line in lines:
        if line=='':
            if data:yield json.loads('\n'.join(data));data=[]
        elif line.startswith('data:'):data.append(line[5:].lstrip(' '))
    if data:yield json.loads('\n'.join(data))


def event_thread(value):
    decorated=value.get('com.linkedin.realtimefrontend.DecoratedEvent') or {}
    if 'messagesTopic' not in decorated.get('topic',''):return None
    payload=decorated.get('payload') or {}
    if isinstance(payload,str):payload=json.loads(payload)
    event=payload.get('event') or payload
    urn=event.get('conversationUrn') or payload.get('conversationUrn')
    if urn:
        try:return thread_urn(urn)
        except SchemaError:pass
    match=re.search(r'urn:li:fs_event:\(([^,]+),',str(event.get('entityUrn','')))
    return 'urn:li:messagingThread:'+match[1] if match else '*'
