"""Turn correlation at the local, paired Muse companion boundary."""
import uuid

# The proof companion restricts WhatsApp sends to this Muse bot.
MUSE = '1807055946647697@bot'


def marker(turn_id):
    return 'HARNESS_REPLY_' + str(uuid.UUID(turn_id))


class MuseTransport:
    def __init__(self, upstream):
        self.upstream = upstream

    async def ready(self):
        status = await self.upstream('connection_status', {})
        return status.get('connected') is True and status.get('logged_in') is True

    async def send(self, turn_id, prompt):
        prefix = marker(turn_id)
        text = (f'{prompt}\nBegin your reply with this exact line, then answer on the next line:\n{prefix}')
        receipt = await self.upstream('send_message', {'recipient': MUSE, 'text': text})
        if receipt.get('chat_jid') != MUSE or not receipt.get('id'):
            raise ValueError('The companion did not confirm delivery to Muse.')

    async def replies(self, turn_ids):
        expected = {marker(turn_id): turn_id for turn_id in turn_ids}
        if not expected:
            return {}
        archive = await self.upstream('list_messages', {'chat_jid': MUSE, 'limit': 100})
        matches = {}
        for event in archive.get('messages', []):
            if (event.get('chat_jid') != MUSE or event.get('sender') != MUSE
                    or event.get('from_me') is not False or not event.get('id')):
                continue
            text = event.get('text')
            if not isinstance(text, str):
                continue
            prefix, _, answer = text.partition('\n')
            turn = expected.get(prefix.rstrip('\r'))
            if turn and answer.strip() and len(answer) <= 20000:
                matches.setdefault(turn, set()).add((event['id'], answer.strip()))
        # Multiple distinct responses need inspection. Never guess by arrival order.
        return {turn: next(iter(values))[1] for turn, values in matches.items() if len(values) == 1}
