#!/usr/bin/env python3
"""Send markdown message to DingTalk group. Supports reading from stdin for multiline content."""
import time, hmac, hashlib, base64, urllib.parse, json, urllib.request, sys

# 钉钉 Webhook 配置
SECRET='SEC21aaadf98aa3c9f10dc4e2858efe8e39e2e14fcc37ae77c4968e70e4ab1a2649'
ACCESS_TOKEN='1dd437474fd75ae7e845b050bf8ea3313ac9620457c1a2346b011c8448048533'

def send_dingtalk(title, text):
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{SECRET}'
    hmac_code = hmac.new(SECRET.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    webhook_url = f'https://oapi.dingtalk.com/robot/send?access_token={ACCESS_TOKEN}&timestamp={timestamp}&sign={sign}'
    data = json.dumps({
        'msgtype': 'markdown',
        'markdown': {'title': title, 'text': text}
    }).encode('utf-8')
    req = urllib.request.Request(webhook_url, data=data, headers={'Content-Type': 'application/json'})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode())

