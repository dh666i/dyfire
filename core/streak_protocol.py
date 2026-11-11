"""Strict, read-only decoding of observed Douyin IM streak metadata.

Only the verified get_message_by_init envelope and conversation ext map are
read. Chat message bodies are never scanned for lookalike JSON or identifiers.
"""
from collections.abc import Mapping
import asyncio
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlparse

from core.protobuf_codec import ProtobufDecodeError, fields, singular


INIT_PATH = (6, 2043, 1, 1)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PENDING_RESPONSES = 4
STREAK_KEYS = frozenset(('a:consecutive_chat', 'a:consecutive_chat_data'))
_RECOVER = re.compile(r'重燃中\s*([0-9]{1,3})\s*/\s*([0-9]{1,3})\Z')
_COUNTDOWN = re.compile(r'[0-9]{1,3}\s*天后消失\Z')
_DAY_TEXT = re.compile(r'(?:火花\s*)?([0-9]{1,5})\s*天?\Z')
_UID = re.compile(r'[1-9][0-9]{0,24}\Z')
_SEC_UID = re.compile(r'[A-Za-z0-9_-]{1,256}\Z')


def _utf8(value, max_bytes=128 * 1024):
    if not isinstance(value, bytes) or len(value) > max_bytes:
        raise ProtobufDecodeError('invalid string size')
    try:
        return value.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ProtobufDecodeError('invalid utf8 string') from error


def _integer(value, *, maximum=99999):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum else None


def _json_object(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('duplicate JSON property')
            result[key] = item
        return result
    try:
        result = json.loads(value, object_pairs_hook=unique,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    except (ValueError, TypeError, RecursionError):
        return None
    return result if isinstance(result, dict) else None


def parse_ext_map(core_info):
    """Read only repeated field 11 map entries (field 42 is key metadata)."""
    result = {}
    for number, wire, value in fields(core_info):
        if number != 11:
            continue
        if wire != 2:
            raise ProtobufDecodeError('conversation ext is not a message')
        entry = list(fields(value, max_fields=16))
        key = _utf8(singular(entry, 1, 2), max_bytes=256)
        if key not in STREAK_KEYS:
            continue
        text = _utf8(singular(entry, 2, 2))
        if key in result and result[key] != text:
            raise ProtobufDecodeError('conflicting streak map entries')
        result[key] = text
    return result


def parse_flame_segment(data, *, observed_at):
    """Interpret exactly the time segment displayed now, never a future one."""
    if not isinstance(data, Mapping) or not isinstance(data.get('flame_infos'), list):
        return None
    if len(data['flame_infos']) > 100:
        return None
    current = []
    for segment in data['flame_infos']:
        if not isinstance(segment, Mapping):
            continue
        start = _integer(segment.get('start'), maximum=2**53 - 1)
        end = _integer(segment.get('end'), maximum=2**53 - 1)
        if start is not None and end is not None and start < end and start <= observed_at < end:
            current.append(segment)
    if len(current) != 1:
        return None
    segment = current[0]
    raw_text = segment.get('text')
    if not isinstance(raw_text, str) or len(raw_text) > 512:
        return None
    text = ' '.join(raw_text.split())
    level = segment.get('level')
    level = level.split('|', 1)[0] if isinstance(level, str) else ''
    state, progress = 'unknown', (None, None)
    if match := _RECOVER.fullmatch(text):
        first, total = map(int, match.groups())
        if total <= 0 or first > total:
            return None
        state, progress = 'recovering', (first, total)
    elif type(segment.get('state')) is int and segment.get('state') == 1 and level == 'normal' and _DAY_TEXT.fullmatch(text):
        state = 'active'
    elif segment.get('state') == 3 and level == 'to_recover' and (
        segment.get('simplify_text') == '待重燃' or _COUNTDOWN.fullmatch(text)
    ):
        state = 'broken'
    values = {_integer(segment.get(key)) for key in ('days', 'real_days') if key in segment}
    if None in values or len(values) > 1:
        return None
    days = next(iter(values)) if values else None
    if match := _DAY_TEXT.fullmatch(text):
        visible = int(match.group(1))
        if days is not None and visible != days:
            return None
        days = visible
    return {'days': days, 'state': state, 'recover_current': progress[0],
            'recover_total': progress[1], 'platform_text': text}


def parse_streak_ext(ext, *, observed_at):
    """Use the exact platform day count, and only current-segment status."""
    if not isinstance(ext, Mapping):
        return None
    days = None
    chat = ext.get('a:consecutive_chat')
    if chat is not None:
        if not isinstance(chat, str) or len(chat) > 256:
            return None
        parts = chat.split(':')
        if len(parts) != 5 or not all(part.isascii() and part.isdecimal() for part in parts):
            return None
        days = _integer(int(parts[0]))
        if days is None:
            return None
    data = _json_object(ext.get('a:consecutive_chat_data'))
    segment = parse_flame_segment(data, observed_at=observed_at)
    if segment is not None:
        if days is not None and segment['days'] is not None and days != segment['days']:
            return None
        if segment['days'] is None:
            segment['days'] = days
        return segment
    if days is None:
        return None
    return {'days': days, 'state': 'unknown', 'recover_current': None,
            'recover_total': None, 'platform_text': str(days)}


def conversation_messages(payload):
    """Traverse the observed envelope by protobuf boundaries, never heuristics."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise ProtobufDecodeError('invalid init response size')
    nodes = [payload]
    for position, field_number in enumerate(INIT_PATH):
        nested = []
        for node in nodes:
            matches = [(wire, value) for number, wire, value in fields(node) if number == field_number]
            if position != 2 and len(matches) > 1:
                raise ProtobufDecodeError('repeated init envelope field')
            for wire, value in matches:
                if wire != 2:
                    raise ProtobufDecodeError('init envelope must be a message')
                nested.append(value)
                if len(nested) > 2000:
                    raise ProtobufDecodeError('too many conversations')
        nodes = nested
    return nodes


def _uid(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if _UID.fullmatch(text) else None


def _conversation_participants(value):
    parts = value.split(':')
    if len(parts) != 4 or parts[:2] != ['0', '1']:
        return None
    first, second = _uid(parts[2]), _uid(parts[3])
    return (first, second) if first and second and first != second else None


def extract_init_streaks(payload, *, observed_at=None):
    """Discard message bodies; retain bounded identity/streak metadata only."""
    moment = observed_at or datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError('Streak observation time must include a timezone')
    observations = []
    for conversation in conversation_messages(payload):
        try:
            decoded = list(fields(conversation))
            if singular(decoded, 3, 0) != 1:
                continue
            conversation_id = _utf8(singular(decoded, 1, 2), max_bytes=128)
            participants = _conversation_participants(conversation_id)
            if participants is None:
                continue
            core_info = singular(decoded, 50, 2)
            core_fields = list(fields(core_info))
            if _utf8(singular(core_fields, 1, 2), max_bytes=128) != conversation_id:
                continue
            observation = parse_streak_ext(parse_ext_map(core_info), observed_at=moment.timestamp())
            if observation is None:
                continue
            observations.append({**observation, 'participants': participants,
                                 'conversation_id': conversation_id, 'source': 'douyin_im',
                                 'observed_at': moment.astimezone(timezone.utc).isoformat()})
        except ProtobufDecodeError:
            # An invalid conversation cannot borrow fields from its neighbor.
            continue
    return observations


class StreakCollector:
    """One collector per authenticated account; never shares identity state."""
    INIT_URL = ('imapi.douyin.com', '/v1/message/get_message_by_init')
    USERS_URL = ('www.douyin.com', '/aweme/v1/web/im/user/info')

    def __init__(self):
        self.uid_to_sec_uid = {}
        self._ambiguous_uids = set()
        self._streaks = {}
        self._pending = set()
        self._request_sequences = {}
        self._request_order = []
        self._sequence = 0
        self._latest_init_sequence = 0
        self.decode_errors = 0

    @classmethod
    def _kind(cls, url):
        try:
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
                return None
            endpoint = (parsed.hostname, parsed.path.rstrip('/'))
            return 'init' if endpoint == cls.INIT_URL else 'users' if endpoint == cls.USERS_URL else None
        except (TypeError, ValueError):
            return None

    def capture_request(self, request):
        if self._kind(request.url) != 'init':
            return
        self._sequence += 1
        self._latest_init_sequence = self._sequence
        key = id(request)
        self._request_sequences[key] = self._sequence
        self._request_order.append(key)
        while len(self._request_order) > 64:
            expired = self._request_order.pop(0)
            self._request_sequences.pop(expired, None)

    def capture(self, response):
        kind = self._kind(response.url)
        if kind is None or len(self._pending) >= MAX_PENDING_RESPONSES:
            return
        try:
            declared = response.headers.get('content-length')
            if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                self.decode_errors += 1
                return
        except (AttributeError, TypeError, ValueError):
            pass
        sequence = None
        if kind == 'init':
            request = getattr(response, 'request', None)
            sequence = self._request_sequences.pop(id(request), None) if request is not None else None
            if sequence is None:
                self._sequence += 1
                sequence = self._sequence
                self._latest_init_sequence = sequence
        task = asyncio.create_task(self.handle_response(response, sequence=sequence))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def handle_response(self, response, *, sequence=None):
        kind = self._kind(response.url)
        if kind is None or response.status != 200:
            return
        if kind == 'init' and sequence is not None and sequence < self._latest_init_sequence:
            return
        try:
            if kind == 'users':
                body = await response.json()
                users = body.get('data') if isinstance(body, dict) else None
                if not isinstance(users, list):
                    return
                for user in users[:2000]:
                    if not isinstance(user, dict):
                        continue
                    uid, sec_uid = _uid(user.get('uid')), user.get('sec_uid')
                    if not uid or not isinstance(sec_uid, str) or not _SEC_UID.fullmatch(sec_uid):
                        continue
                    if uid in self._ambiguous_uids:
                        continue
                    previous = self.uid_to_sec_uid.get(uid)
                    if previous and previous != sec_uid:
                        self.uid_to_sec_uid.pop(uid, None)
                        self._ambiguous_uids.add(uid)
                    elif len(self.uid_to_sec_uid) < 2000:
                        self.uid_to_sec_uid[uid] = sec_uid
                return
            raw = await response.body()
            observed_at = datetime.now(timezone.utc)
            for observation in extract_init_streaks(raw, observed_at=observed_at):
                key = observation['conversation_id']
                if sequence == self._latest_init_sequence and (key in self._streaks or len(self._streaks) < 2000):
                    self._streaks[key] = observation
            # raw is not retained: no chats, protobuf blobs or credentials saved.
        except (ProtobufDecodeError, ValueError, TypeError, RecursionError):
            self.decode_errors += 1
        except Exception:
            # Closed pages or interrupted response reads are not observations.
            self.decode_errors += 1

    def observations(self, trusted_self_uid):
        own_uid = _uid(trusted_self_uid)
        if own_uid is None:
            return []
        found, ambiguous = {}, set()
        for observation in self._streaks.values():
            participants = observation['participants']
            if participants.count(own_uid) != 1:
                continue
            peer = participants[1] if participants[0] == own_uid else participants[0]
            sec_uid = self.uid_to_sec_uid.get(peer)
            if not sec_uid or sec_uid in ambiguous:
                continue
            value = {key: item for key, item in observation.items() if key not in ('participants', 'conversation_id')}
            value['sec_uid'] = sec_uid
            if sec_uid in found and any(found[sec_uid][key] != value[key] for key in ('days', 'state', 'recover_current', 'recover_total')):
                found.pop(sec_uid)
                ambiguous.add(sec_uid)
            else:
                found[sec_uid] = value
        return list(found.values())

    async def finalize(self, trusted_self_uid):
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        return self.observations(trusted_self_uid)
