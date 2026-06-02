#!/usr/bin/env python3
"""
MEC监控检测脚本 - 简化版
检测新日志，输出前20KB供LLM分析
"""
import json
import sys
import os
import time
import re
import subprocess
from datetime import datetime
import urllib.request
import urllib.error
from pathlib import Path

from config import FEISHU_APP_SECRET

# Feishu配置
CHAT_ID = 'oc_20cfbf30aae8b296ece5318b52cddd73'
APP_ID = 'cli_a965bcb58378dcd3'
REPORT_HEADER_KEYWORD = '全局刷新完成报告'

# 重试配置
MAX_RETRIES = 2
RETRY_DELAY = 1
MAX_LOG_SIZE = 200000  # 最大20KB

def get_feishu_token():
    """获取Feishu token"""
    app_secret = FEISHU_APP_SECRET
    
    try:
        url = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
        data = json.dumps({'app_id': APP_ID, 'app_secret': app_secret}).encode()
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())['tenant_access_token']
    except:
        return None

def fetch_latest_mec_message():
    """获取最新的MEC监控消息"""
    token = get_feishu_token()
    if not token:
        return None, "获取token失败"
    
    try:
        url = f'https://open.feishu.cn/open-apis/im/v1/messages?container_id_type=chat&container_id={CHAT_ID}&page_size=50&sort_type=ByCreateTimeDesc'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        resp = urllib.request.urlopen(req, timeout=10)
        items = json.loads(resp.read()).get('data', {}).get('items', [])
        
        for msg in items:
            content = msg.get('body', {}).get('content', '')
            if REPORT_HEADER_KEYWORD in content:
                try:
                    data = json.loads(content)
                    text = data.get('text', '')
                    if text:
                        return text, None
                    content_list = data.get('content', [])
                    if content_list:
                        extracted_text = []
                        for section in content_list:
                            if isinstance(section, list):
                                for item in section:
                                    if isinstance(item, dict) and item.get('tag') == 'text':
                                        extracted_text.append(item.get('text', ''))
                        if extracted_text:
                            return ''.join(extracted_text), None
                    return content, None
                except:
                    return content, None
        return None, "未找到MEC监控报告"
    except Exception as e:
        return None, f"API调用失败: {e}"

def load_last_check():
    try:
        with open(SELF_AGENT_DIR / 'last_check.json', 'r') as f:
            return json.load(f)
    except:
        return {"last_timestamp": ""}

def save_last_check(timestamp):
    with open(SELF_AGENT_DIR / 'last_check.json', 'w') as f:
        json.dump({"last_timestamp": timestamp}, f, indent=2)

def update_task_status(status):
    pass

def extract_timestamp(report_text):
    match = re.search(r'刷新时间: ([\d-]+ [\d:]+)', report_text)
    return match.group(1) if match else ""

def main():
    print(f"=== MEC日志检测 {datetime.now()} ===")

    print("1. 检查是否有新日志...")
    report_text, error = fetch_latest_mec_message()

    if error:
        print(f"   错误: {error}")
        update_task_status("error")
        return "[SILENT]"

    if not report_text:
        print("   未获取到报告")
        update_task_status("ok")
        return "[SILENT]"

    timestamp = extract_timestamp(report_text)
    print(f"   报告时间: {timestamp}")

    last_check = load_last_check()

    if last_check.get('last_timestamp') == timestamp:
        print(f"   无新日志")
        update_task_status("ok")
        return "[SILENT]"

    print(f"   ✓ 发现新日志！")
    # 注意：不在这里更新时间戳！
    # 时间戳由LLM分析完成后再更新（在prompt的步骤5之后）
    # 这样确保每条新日志都会被分析

    # 限制日志长度
    if len(report_text) > MAX_LOG_SIZE:
        print(f"   日志过长({len(report_text)}字节)，截取前{MAX_LOG_SIZE}字节")
        report_text = report_text[:MAX_LOG_SIZE] + "\n\n... [日志已截断，请重点关注P0/P1问题] ..."

    # 输出日志
    print("\n=== 新日志内容 ===")
    print(report_text)

    return None

def update_timestamp_only():
    """只更新时间戳，不输出日志内容（用于步骤4）"""
    print(f"=== 更新时间戳 {datetime.now()} ===")
    
    report_text, error = fetch_latest_mec_message()
    
    if error:
        print(f"❌ 获取日志失败: {error}")
        return 1
    
    if not report_text:
        print("❌ 未获取到报告")
        return 1
    
    timestamp = extract_timestamp(report_text)
    if not timestamp:
        print("❌ 无法提取时间戳")
        return 1
    
    print(f"报告时间: {timestamp}")
    save_last_check(timestamp)
    print(f"✅ 已更新时间戳: {timestamp}")
    
    return 0

if __name__ == '__main__':
    # 检查是否是 --update-timestamp 模式
    if len(sys.argv) > 1 and sys.argv[1] == '--update-timestamp':
        sys.exit(update_timestamp_only())
    
    # 正常模式：检测新日志
    result = main()
    if result:
        print(result)
