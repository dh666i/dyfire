"""Explicit user actions only. No automated security-challenge solving."""
import math
from spark_console.services import ValidationError

KEYS={'Enter','Tab','Escape','Backspace','Delete','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','ControlOrMeta+A'}


def validate(kind,value):
    def coordinate(v):
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1:raise ValidationError('invalid_interaction')
        return float(v)
    if kind=='key':
        if value.get('key') not in KEYS:raise ValidationError('invalid_interaction')
        return {'key':value['key']}
    if kind=='scroll':
        amount=value.get('delta_y')
        if type(amount) is not int or not -1200<=amount<=1200:raise ValidationError('invalid_interaction')
        return {'x':coordinate(value.get('x',.5)),'y':coordinate(value.get('y',.5)),'delta_y':amount}
    if kind=='drag':
        points=value.get('points')
        if not isinstance(points,list) or not 2<=len(points)<=40:raise ValidationError('invalid_interaction')
        try:return {'points':[{'x':coordinate(p['x']),'y':coordinate(p['y'])} for p in points]}
        except (TypeError,KeyError):raise ValidationError('invalid_interaction') from None
    raise ValidationError('invalid_interaction')


async def deliver(page,kind,payload):
    from urllib.parse import urlsplit
    host=urlsplit(str(page.url)).hostname or ''
    if not (host=='douyin.com' or host.endswith('.douyin.com')):return False
    value=validate(kind,payload)
    viewport=page.viewport_size or {'width':1280,'height':720}
    if kind=='key':await page.keyboard.press(value['key']);return True
    if kind=='scroll':
        await page.mouse.move(value['x']*viewport['width'],value['y']*viewport['height'])
        await page.mouse.wheel(0,value['delta_y']);return True
    points=value['points']
    await page.mouse.move(points[0]['x']*viewport['width'],points[0]['y']*viewport['height'])
    await page.mouse.down()
    try:
        for point in points[1:]:await page.mouse.move(point['x']*viewport['width'],point['y']*viewport['height'],steps=3)
    finally:await page.mouse.up()
    return True
