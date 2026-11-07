"""Collect structural facts only: never page text, URLs or input values."""
import asyncio
import json


async def structural_evidence(page):
    if page is None:
        return {}
    try:
        value = await asyncio.wait_for(page.evaluate("""() => {
          const visible = s => [...document.querySelectorAll(s)].some(e => {
            const r=e.getBoundingClientRect(); return r.width>0 && r.height>0;
          });
          return {
            dom_ready: document.readyState === 'complete',
            editor: visible('[contenteditable="true"],textarea'),
            password: visible('input[type="password"]'),
            challenge: visible('[class*="captcha"],[id*="captcha"],input[autocomplete="one-time-code"]')
          };
        }"""), timeout=1.5)
        return {key:value[key] for key in ('dom_ready','editor','password','challenge')
                if isinstance(value,dict) and type(value.get(key)) is bool}
    except Exception:
        return {'unavailable':True}


def encode(value):
    allowed = {'dom_ready','editor','password','challenge','unavailable'}
    return json.dumps({k:v for k,v in (value or {}).items() if k in allowed and type(v) is bool},separators=(',',':'))


def decode(raw):
    try:
        value=json.loads(raw or '{}')
        return json.loads(encode(value)) if isinstance(value,dict) else {}
    except (ValueError,TypeError):
        return {}
