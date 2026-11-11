"""Read platform streak evidence without sending, guessing identities or days.

The DOM reader sees one rendered conversation window. Its caller owns scrolling
and supplies identity mappings captured from the *same account/session*. Missing
evidence produces no observation, never a synthetic zero or an increment.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
import re


STREAK_ROWS_SCRIPT = r"""() => {
  const rowSelector='[data-e2e="conversation-item"],.conversationConversationItemwrapper';
  const visible=el=>!!el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden';
  const clean=value=>(value||'').replace(/\s+/g,' ').trim().slice(0,512);
  const rows=[...document.querySelectorAll(rowSelector)].filter(visible);
  return rows.slice(0,300).map(row=>{
    const own=el=>el.closest(rowSelector)===row;
    const identity={sec_uids:[],uids:[],conversation_ids:[]};
    const add=(key,value)=>{const text=clean(value);if(text&&!identity[key].includes(text))identity[key].push(text)};
    const nodes=[row,...row.querySelectorAll('[data-sec-uid],[data-uid],[data-user-id],[data-conversation-id]')].filter(own);
    for(const node of nodes){
      add('sec_uids',node.getAttribute('data-sec-uid'));
      add('uids',node.getAttribute('data-uid'));add('uids',node.getAttribute('data-user-id'));
      add('conversation_ids',node.getAttribute('data-conversation-id'));
    }
    for(const link of row.querySelectorAll('a[href*="/user/"]')){
      if(!own(link))continue;
      try{const url=new URL(link.getAttribute('href'),location.href);
        if(url.protocol==='https:'&&['www.douyin.com','douyin.com'].includes(url.hostname)){
          const match=url.pathname.match(/^\/user\/([^/]+)\/?$/);
          if(match)add('sec_uids',decodeURIComponent(match[1]));
        }
      }catch{}
    }
    const signals=[];
    const addSignal=(kind,text)=>{text=clean(text);if(text&&!signals.some(s=>s.kind===kind&&s.text===text))signals.push({kind,text})};
    // Specific streak components only: never scrape arbitrary row text,
    // unread counters, message previews or numeric nicknames.
    const badges=[...row.querySelectorAll('[class*="commonStreak"],[data-e2e="streak-badge"]')].filter(el=>own(el)&&visible(el));
    for(const badge of badges){
      if(badge.matches('[class*="commonStreaknormalText"],[data-e2e="streak-badge"]'))addSignal('badge',badge.textContent);
      addSignal('aria',badge.getAttribute('aria-label'));addSignal('title',badge.getAttribute('title'));
    }
    const title=row.querySelector('.conversationConversationItemtitle');
    const nickname=clean(title?.textContent||row.getAttribute('data-nickname'));
    for(const key of ['aria-label','title']){
      let label=clean(row.getAttribute(key));
      // A nickname can itself contain “火花100天”. It is not platform evidence.
      if(nickname&&label===nickname)continue;
      if(nickname&&label.startsWith(nickname))label=label.slice(nickname.length).replace(/^[\s，,:：;；·]+/,'');
      if(/火花|连续聊天/.test(label))addSignal(key==='aria-label'?'aria':'title',label);
    }
    return {identity,signals};
  }).filter(row=>row.signals.length);
}"""

_IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
_BARE_DAYS = re.compile(r"(?:🔥\s*)?([0-9]{1,5})\s*天?\Z")
_SEMANTIC_DAYS = re.compile(r"(?:火花(?:天数)?|连续聊天)\s*[:：]?\s*([0-9]{1,5})\s*天?\Z")
_RECOVERY = re.compile(r"(?:火花)?\s*重燃中\s*[:：]?\s*([0-9]{1,3})\s*/\s*([0-9]{1,3})\s*(?:天)?\Z")
_STATE_TEXT = {
    '火花已点亮': 'active', '火花点亮中': 'active', '火花已熄灭': 'broken',
    '火花已中断': 'broken', '火花已断开': 'broken', '火花重燃中': 'recovering',
    '重燃中': 'recovering',
}


def _identifier(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value if _IDENTITY.fullmatch(value) else None


def _identity(row, uid_to_sec_uid, conversation_to_sec_uid):
    identity = row.get('identity')
    if not isinstance(identity, Mapping):
        return None
    direct = identity.get('sec_uids', [])
    uids = identity.get('uids', [])
    conversations = identity.get('conversation_ids', [])
    if not all(isinstance(values, list) for values in (direct, uids, conversations)):
        return None
    candidates = set()
    for value in direct:
        identifier = _identifier(value)
        if identifier is None:
            return None
        candidates.add(identifier)
    for values, mapping in ((uids, uid_to_sec_uid), (conversations, conversation_to_sec_uid)):
        for value in values:
            identifier = _identifier(value)
            if identifier is None:
                return None
            if identifier in mapping:
                resolved = _identifier(mapping[identifier])
                if resolved is None:
                    return None
                candidates.add(resolved)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _signal(signal):
    if not isinstance(signal, Mapping) or signal.get('kind') not in ('badge', 'aria', 'title'):
        return None
    text = signal.get('text')
    if not isinstance(text, str) or len(text) > 512:
        return None
    text = ' '.join(text.split())
    # Only whole semantic segments qualify. Do not extract 123 from a name,
    # message, timestamp, negative number, decimal or a 100000-day suffix.
    days, states, recovery, evidence = set(), set(), set(), []
    for segment in re.split(r'[，,；;·\n]+', text):
        segment = segment.strip()
        match = _SEMANTIC_DAYS.fullmatch(segment)
        if match is None and signal['kind'] == 'badge':
            match = _BARE_DAYS.fullmatch(segment)
        if match:
            days.add(int(match.group(1))); evidence.append(segment)
        elif match := _RECOVERY.fullmatch(segment):
            current, total = map(int, match.groups())
            if total > 0 and 0 <= current <= total:
                recovery.add((current, total)); states.add('recovering'); evidence.append(segment)
        elif segment in _STATE_TEXT:
            states.add(_STATE_TEXT[segment]); evidence.append(segment)
    if not evidence:
        return None
    return days, states, recovery, evidence


def parse_streak_rows(rows, *, uid_to_sec_uid=None, conversation_to_sec_uid=None, observed_at=None):
    """Parse DOM evidence with caller-supplied account-local identity mappings.

    Unknown fields are None, state defaults to unknown. Conflicting day counts
    or identity mappings discard the observation, rather than picking a maximum.
    This function never uses nicknames and never derives state from icon color.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    uid_to_sec_uid = uid_to_sec_uid if isinstance(uid_to_sec_uid, Mapping) else {}
    conversation_to_sec_uid = conversation_to_sec_uid if isinstance(conversation_to_sec_uid, Mapping) else {}
    moment = observed_at or datetime.now(timezone.utc)
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace('Z', '+00:00'))
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError('Streak observation time must include a timezone')
    timestamp = moment.astimezone(timezone.utc).isoformat()
    collected = {}
    for row in rows[:1000]:
        if not isinstance(row, Mapping):
            continue
        sec_uid = _identity(row, uid_to_sec_uid, conversation_to_sec_uid)
        if sec_uid is None or not isinstance(row.get('signals'), list):
            continue
        for raw in row['signals'][:50]:
            parsed = _signal(raw)
            if parsed is None:
                continue
            bucket = collected.setdefault(sec_uid, [set(), set(), set(), []])
            for index in range(3):
                bucket[index].update(parsed[index])
            bucket[3].extend(parsed[3])
    result = []
    for sec_uid, (days, states, recovery, evidence) in collected.items():
        if len(days) > 1 or len(recovery) > 1:
            continue
        state = next(iter(states)) if len(states) == 1 else 'unknown'
        progress = next(iter(recovery)) if recovery and state == 'recovering' else (None, None)
        result.append({
            'sec_uid': sec_uid, 'days': next(iter(days)) if days else None,
            'state': state, 'recover_current': progress[0], 'recover_total': progress[1],
            'platform_text': ' · '.join(dict.fromkeys(evidence))[:512],
            'source': 'douyin_dom', 'observed_at': timestamp,
        })
    return result


async def read_streak_observations(page, **kwargs):
    """Read only the current DOM window; no clicks, navigation or requests."""
    return parse_streak_rows(await page.evaluate(STREAK_ROWS_SCRIPT), **kwargs)
