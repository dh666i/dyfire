"""One stable browser environment shared by all account operations."""
import hashlib,json
from importlib.metadata import version

# Preserve the previously used Playwright/Ubuntu defaults explicitly. Changing
# a profile is a deliberate release decision, never randomized per account.
PROFILE={'locale':'en-US','timezone_id':'UTC','viewport':{'width':1280,'height':720},'color_scheme':'light'}


def options(state=None):
    value={**PROFILE,'viewport':dict(PROFILE['viewport'])}
    if state is not None:value['storage_state']=state
    return value


def credential_options(payload):
    return {**options(),**payload.context_options()}


def identity():
    return {'profile':'dyfire-browser-v1','playwright':version('playwright'),
        'profile_hash':hashlib.sha256(json.dumps(PROFILE,sort_keys=True).encode()).hexdigest()[:16]}
