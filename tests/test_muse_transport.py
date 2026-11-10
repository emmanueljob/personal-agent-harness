import asyncio
import uuid

from harness.muse_transport import MUSE, MuseTransport, marker


def test_two_turns_with_interleaved_and_duplicate_events():
    first, second = (str(uuid.uuid4()) for _ in range(2))
    events = []
    sent = []

    def event(turn, answer):
        return dict(id=turn, text=marker(turn)+'\n'+answer, chat_jid=MUSE, sender=MUSE, from_me=False)

    async def upstream(name, args):
        if name == 'connection_status':
            return {'connected': True, 'logged_in': True}
        if name == 'send_message':
            assert args['recipient'] == MUSE
            sent.append(args['text'])
            return {'id': str(len(sent)), 'chat_jid': MUSE}
        assert name == 'list_messages' and args['chat_jid'] == MUSE
        return {'messages': events}

    async def scenario():
        transport = MuseTransport(upstream)
        assert await transport.ready()
        await transport.send(first, 'Which fictional company?')
        response = event(first, 'Example Co')
        events.extend([response, response])
        assert await transport.replies([first]) == {first: 'Example Co'}
        await transport.send(second, 'Prior answer: Example Co. Why follow up?')
        events.extend([{**event(second, 'echo'), 'from_me': True},
                       {**event(second, 'unrelated'), 'sender': 'another-bot'},
                       {**event(second, 'wrong chat'), 'chat_jid': 'another-chat'},
                       {**event(second, 'untagged'), 'text': 'unrelated note'}])
        assert await transport.replies([second]) == {}
        events.append(event(second, 'Synthetic reason'))
        assert await transport.replies([second]) == {second: 'Synthetic reason'}
        events.append({**event(second, 'Different answer'), 'id': 'other-event'})
        assert await transport.replies([second]) == {}
        assert marker(first) in sent[0] and marker(second) in sent[1]

    asyncio.run(scenario())
