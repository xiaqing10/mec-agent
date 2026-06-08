import json
from pathlib import Path
from aiohttp import web

SELF_AGENT_DIR = Path(__file__).parent
STATIC_DIR = SELF_AGENT_DIR / 'static' / 'vendor'


async def handle_static(request):
    filename = request.match_info.get("filename", "")
    filepath = STATIC_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        return web.json_response({"error": "File not found"}, status=404)
    return web.FileResponse(filepath)


async def handle_webui(request):
    from config import API_KEY, FEEDBACK_DELAY_SECONDS, AVAILABLE_MODELS
    html = WEBUI_HTML.replace('__API_KEY__', json.dumps(API_KEY))
    html = html.replace('__FEEDBACK_DELAY__', str(FEEDBACK_DELAY_SECONDS * 1000))
    html = html.replace('__AVAILABLE_MODELS__', json.dumps(AVAILABLE_MODELS, ensure_ascii=False))
    return web.Response(text=html, content_type='text/html')


WEBUI_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>智慧交通垂域智能体</title>
<link rel="stylesheet" href="/static/github.min.css">
<script src="/static/highlight.min.js"></script>
<script src="/static/markdown-it.min.js"></script>
<style>
:root {
  --sidebar-w: 260px;
  --header-h: 56px;
  --primary: #4a9eff;
  --primary-dim: #2a6fd4;
  --primary-glow: rgba(74, 158, 255, 0.3);
  --bg-base: #0d1117;
  --bg-surface: #161b22;
  --bg-elevated: #1c2333;
  --bg-glass: rgba(22, 27, 34, 0.75);
  --border-color: rgba(74, 158, 255, 0.15);
  --border-glow: rgba(74, 158, 255, 0.25);
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --text-muted: #6e7681;
  --success: #3fb950;
  --warning: #d29922;
  --danger: #f85149;
  --info: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg-base);
  height: 100vh;
  display: flex;
  flex-direction: column;
  color: var(--text-primary);
  overflow: hidden;
}
/* Header */
.header {
  height: var(--header-h);
  background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1a2332 100%);
  border-bottom: 1px solid var(--border-glow);
  color: var(--text-primary);
  display: flex;
  align-items: center;
  padding: 0 24px;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 0.5px;
  flex-shrink: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
  position: relative;
}
.header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--primary-glow), transparent);
}
.header .header-badge {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.header .header-badge::before {
  content: '🚦';
  font-size: 20px;
}
.header .subtitle { font-size: 11px; font-weight: 400; opacity: 0.5; margin-left: 12px; letter-spacing: 1px; text-transform: uppercase; }
.header-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.header-right .user-info { font-size: 13px; color: var(--text-secondary); }
.header-right button {
  background: var(--bg-elevated); border: 1px solid var(--border-color);
  color: var(--text-secondary); padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 12px;
  transition: all 0.2s ease;
}
.header-right button:hover { background: var(--border-color); color: var(--text-primary); border-color: var(--border-glow); box-shadow: 0 0 8px var(--primary-glow); }
/* Container */
.container { display: flex; flex: 1; overflow: hidden; }
/* Sidebar */
.sidebar {
  width: var(--sidebar-w); background: var(--bg-surface); border-right: 1px solid var(--border-color);
  display: flex; flex-direction: column; flex-shrink: 0;
}
.sidebar-header {
  padding: 14px 16px; border-bottom: 1px solid var(--border-color);
  font-size: 13px; font-weight: 500; color: var(--text-secondary); display: flex; justify-content: space-between; align-items: center;
}
.sidebar-header button {
  background: linear-gradient(135deg, var(--primary-dim), var(--primary));
  color: #fff; border: none; padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 11px;
  transition: all 0.2s ease;
}
.sidebar-header button:hover { box-shadow: 0 0 12px var(--primary-glow); }
.session-list { flex: 1; overflow-y: auto; padding: 4px 0; }
.session-item {
  padding: 10px 16px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,0.03);
  font-size: 13px; color: var(--text-secondary); transition: all 0.2s; position: relative;
}
.session-item:hover { background: rgba(74, 158, 255, 0.06); color: var(--text-primary); }
.session-item.active { background: rgba(74, 158, 255, 0.1); color: var(--primary); font-weight: 500; border-left: 2px solid var(--primary); }
.session-item .time { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.session-item .delete-btn {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 14px; display: none; padding: 2px 6px; border-radius: 4px;
}
.session-item:hover .delete-btn { display: block; }
.session-item .delete-btn:hover { color: var(--danger); background: rgba(248,81,73,0.1); }
/* Main */
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--bg-base); }
/* Chat area */
.chat-area { flex: 1; overflow-y: auto; padding: 20px 24px; }
.msg { margin-bottom: 20px; max-width: 85%; animation: msgFadeIn 0.3s ease; }
@keyframes msgFadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.msg.user { margin-left: auto; }
.msg.bot { margin-right: auto; }
.msg .bubble {
  padding: 12px 18px; border-radius: 14px; line-height: 1.6; font-size: 14px; word-wrap: break-word;
}
.msg.user .bubble {
  background: linear-gradient(135deg, var(--primary-dim), var(--primary));
  color: #fff; border-bottom-right-radius: 4px; box-shadow: 0 2px 12px rgba(74, 158, 255, 0.2);
}
.msg.bot .bubble {
  background: var(--bg-glass);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  border-bottom-left-radius: 4px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.2);
  backdrop-filter: blur(8px);
}
.msg.bot .bubble pre {
  background: #0d1117;
  border: 1px solid rgba(74,158,255,0.1);
  border-radius: 8px;
  padding: 12px;
  overflow-x: auto;
  font-size: 13px;
  margin: 10px 0;
}
.msg.bot .bubble code { font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; color: var(--info); }
.msg.bot .bubble table {
  border-collapse: collapse; margin: 10px 0; width: 100%; font-size: 13px;
  border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden;
}
.msg.bot .bubble th, .msg.bot .bubble td {
  border: 1px solid var(--border-color); padding: 8px 12px; text-align: left;
}
.msg.bot .bubble th { background: rgba(74,158,255,0.08); font-weight: 600; color: var(--primary); }
.msg.bot .bubble tr:nth-child(even) { background: rgba(255,255,255,0.02); }
.msg.bot .bubble tr:hover { background: rgba(74,158,255,0.06); }
.msg.bot .bubble a { color: var(--info); }
.msg.bot .bubble strong { color: var(--text-primary); }
.tool-tag { font-size: 12px; margin: 4px 0; padding: 3px 10px; border-radius: 6px; display: inline-block; }
.tool-tag.running { background: rgba(210,153,34,0.12); color: var(--warning); border: 1px solid rgba(210,153,34,0.2); }
.tool-tag.done { background: rgba(63,185,80,0.12); color: var(--success); border: 1px solid rgba(63,185,80,0.2); }
/* Input */
.input-area {
  border-top: 1px solid var(--border-color); padding: 14px 24px; background: var(--bg-surface);
  display: flex; gap: 10px; align-items: flex-end;
}
.input-area textarea {
  flex: 1; background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 10px; padding: 10px 16px; font-size: 14px;
  resize: none; outline: none; min-height: 44px; max-height: 120px; line-height: 1.5; font-family: inherit; color: var(--text-primary);
  transition: all 0.2s ease;
}
.input-area textarea::placeholder { color: var(--text-muted); }
.input-area textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.input-area button {
  background: linear-gradient(135deg, var(--primary-dim), var(--primary));
  color: #fff; border: none; border-radius: 10px; padding: 10px 22px;
  font-size: 14px; cursor: pointer; white-space: nowrap; height: 44px;
  transition: all 0.2s ease;
}
.input-area button:hover { box-shadow: 0 0 16px var(--primary-glow); }
.input-area button:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; }
/* Login overlay */
.login-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 1000; backdrop-filter: blur(4px);
}
.login-box {
  background: var(--bg-elevated); border: 1px solid var(--border-glow); border-radius: 16px; padding: 36px; width: 360px; box-shadow: 0 8px 40px rgba(0,0,0,0.4);
}
.login-box h2 { margin-bottom: 24px; text-align: center; color: var(--text-primary); font-size: 20px; letter-spacing: 0.5px; }
.login-box input {
  width: 100%; padding: 11px 16px; background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 8px; font-size: 14px; margin-bottom: 14px; outline: none; color: var(--text-primary); transition: all 0.2s;
}
.login-box input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.login-box input::placeholder { color: var(--text-muted); }
.login-box button {
  width: 100%; padding: 11px; background: linear-gradient(135deg, var(--primary-dim), var(--primary)); color: #fff; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.login-box button:hover { box-shadow: 0 0 16px var(--primary-glow); }
.login-box .error { color: var(--danger); font-size: 13px; margin-bottom: 8px; text-align: center; }
/* Feedback bar */
.feedback-bar {
  background: var(--bg-elevated); border-top: 1px solid var(--border-color); padding: 10px 20px; display: none; align-items: center; gap: 12px; font-size: 13px; color: var(--text-secondary); flex-shrink: 0;
}
.feedback-bar.show { display: flex; }
.feedback-bar .fb-btn {
  background: none; border: 1px solid var(--border-color); border-radius: 16px; padding: 4px 14px; cursor: pointer; font-size: 12px; color: var(--text-secondary); transition: all 0.2s;
}
.feedback-bar .fb-btn:hover { border-color: var(--primary); color: var(--primary); background: rgba(74,158,255,0.06); }
.feedback-bar .fb-btn.selected { background: var(--primary); color: #fff; border-color: var(--primary); }
.feedback-bar .fb-text { flex: 1; }
.feedback-bar textarea {
  flex: 1; background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 6px; padding: 6px 10px; font-size: 12px; resize: none; height: 32px; outline: none; color: var(--text-primary); transition: all 0.2s;
}
.feedback-bar textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-glow); }
.feedback-bar .fb-submit {
  background: linear-gradient(135deg, var(--primary-dim), var(--primary)); color: #fff; border: none; border-radius: 6px; padding: 6px 16px; font-size: 12px; cursor: pointer; transition: all 0.2s;
}
.feedback-bar .fb-submit:hover { box-shadow: 0 0 10px var(--primary-glow); }
/* Scrollbar */
.chat-area::-webkit-scrollbar, .session-list::-webkit-scrollbar { width: 6px; }
.chat-area::-webkit-scrollbar-track, .session-list::-webkit-scrollbar-track { background: transparent; }
.chat-area::-webkit-scrollbar-thumb, .session-list::-webkit-scrollbar-thumb { background: rgba(74,158,255,0.2); border-radius: 3px; }
.chat-area::-webkit-scrollbar-thumb:hover, .session-list::-webkit-scrollbar-thumb:hover { background: rgba(74,158,255,0.4); }
/* Copy button */
.copy-btn {
  display: block; margin-top: 10px; background: var(--bg-elevated); border: 1px solid var(--border-color); border-radius: 6px;
  padding: 5px 14px; font-size: 12px; cursor: pointer; color: var(--text-secondary); transition: all 0.2s;
}
.copy-btn:hover { background: var(--border-color); color: var(--text-primary); border-color: var(--border-glow); }
/* Guide panel */
.guide-panel {
  position: fixed; top: var(--header-h); right: 0; width: 380px; height: calc(100vh - var(--header-h));
  background: var(--bg-glass); border-left: 1px solid var(--border-color); box-shadow: -4px 0 24px rgba(0,0,0,0.3);
  z-index: 200; overflow-y: auto; padding: 24px; display: none;
  font-size: 14px; line-height: 1.7; color: var(--text-secondary); backdrop-filter: blur(16px);
}
.guide-panel.show { display: block; }
.guide-panel h3 { margin: 18px 0 8px; color: var(--primary); font-size: 15px; }
.guide-panel ul { padding-left: 20px; }
.guide-panel li { margin: 5px 0; color: var(--text-secondary); }
.guide-panel p { color: var(--text-muted); }
/* Feedback history modal */
.fb-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 500; display: flex; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
.fb-modal-content { background: var(--bg-elevated); border: 1px solid var(--border-color); border-radius: 16px; width: 720px; max-width: 90vw; max-height: 80vh; display: flex; flex-direction: column; box-shadow: 0 8px 40px rgba(0,0,0,0.4); }
.fb-modal-header { padding: 18px 24px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; font-size: 16px; font-weight: 600; color: var(--text-primary); }
.fb-modal-header button { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); transition: color 0.2s; }
.fb-modal-header button:hover { color: var(--text-primary); }
.fb-modal-body { overflow-y: auto; padding: 18px 24px; flex: 1; }
.fb-stats { display: flex; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
.fb-stat-card { flex: 1; min-width: 80px; text-align: center; padding: 14px; border-radius: 10px; background: var(--bg-surface); border: 1px solid var(--border-color); }
.fb-stat-card .num { font-size: 26px; font-weight: 700; }
.fb-stat-card .label { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.fb-record { padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px; }
.fb-record:last-child { border-bottom: none; }
.fb-record .fb-intent { font-weight: 500; color: var(--text-primary); }
.fb-record .fb-meta { font-size: 11px; color: var(--text-muted); margin-top: 3px; display: flex; gap: 10px; flex-wrap: wrap; }
.fb-record .fb-rating { font-size: 12px; }
.fb-tabs { display: flex; gap: 0; margin-bottom: 12px; border-bottom: 2px solid var(--border-color); }
.fb-tabs button { padding: 8px 16px; border: none; background: none; cursor: pointer; font-size: 13px; color: var(--text-muted); border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }
.fb-tabs button:hover { color: var(--text-secondary); }
.fb-tabs button.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 500; }
.fb-record .fb-meta .edit-fb-btn { background: none; border: none; cursor: pointer; font-size: 12px; color: var(--primary); padding: 0 4px; }
.diag-progress-bar {
  font-size: 13px; color: var(--text-secondary); margin: 6px 0; padding: 8px 14px;
  background: rgba(74,158,255,0.06); border-radius: 8px; border-left: 3px solid var(--primary);
}
.diag-summary-card {
  margin: 10px 0; border: 1px solid var(--border-color); border-radius: 10px;
  overflow: hidden; font-size: 13px; background: var(--bg-glass); backdrop-filter: blur(4px);
}
.diag-summary-card .summary-header {
  background: rgba(74,158,255,0.06); padding: 10px 16px; border-bottom: 1px solid var(--border-color);
  display: flex; justify-content: space-between; align-items: center;
}
.diag-summary-card .summary-body { padding: 8px 16px; }
.diag-summary-card .dim-row {
  display: flex; align-items: flex-start; padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
}
.diag-summary-card .dim-name { flex: 0 0 90px; font-weight: 600; }
.diag-summary-card .dim-detail { flex: 1; color: var(--text-secondary); }
.root-cause-box {
  margin-top: 12px; padding: 10px 16px; background: rgba(210,153,34,0.08);
  border-radius: 8px; border-left: 3px solid var(--warning);
}
.cursor { animation: blink 1s step-end infinite; }
@keyframes blink { 50% { opacity: 0; } }
</style>
</head>
<body>
<div class="header">
  <div class="header-badge">智慧交通垂域智能体</div>
  <span class="subtitle">v3.4 · LangGraph</span>
  <div class="header-right">
    <span class="user-info" id="userInfo"></span>
    <select id="modelSelect" onchange="onModelChange()" style="font-size:12px;padding:4px 8px;border-radius:4px;border:1px solid var(--border-color);background:var(--bg-surface);color:var(--text-primary);cursor:pointer;"></select>
    <button onclick="showGuide()">📖 指南</button>
    <button onclick="showFeedbackHistory()">📊 反馈</button>
    <button onclick="showMemoryPanel()">🧠 记忆</button>
    <button onclick="logout()">退出</button>
  </div>
</div>
<div class="container">
  <div class="sidebar">
    <div class="sidebar-header">
      <span>会话历史</span>
      <button onclick="newSession()">＋ 新对话</button>
    </div>
    <div class="session-list" id="sessionList"></div>
  </div>
  <div class="main">
    <div class="chat-area" id="chatArea"></div>
    <div class="feedback-bar" id="feedbackBar">
      <span id="fbPrompt">这个回答有帮助吗？</span>
      <button class="fb-btn" data-rating="satisfied" onclick="rate('satisfied')">👍 有帮助</button>
      <button class="fb-btn" data-rating="partial" onclick="rate('partial')">🤔 部分解决</button>
      <button class="fb-btn" data-rating="unsatisfied" onclick="rate('unsatisfied')">👎 没帮助</button>
      <textarea id="fbTextarea" placeholder="补充说明（可选）"></textarea>
      <button class="fb-submit" onclick="submitFeedback()">提交</button>
    </div>
    <div class="input-area">
      <textarea id="msgInput" rows="1" placeholder="输入消息，例如：查看德会项目状态" onkeydown="onInputKeydown(event)"></textarea>
      <button id="sendBtn" onclick="sendMessage()">发送</button>
    </div>
  </div>
</div>
<div class="login-overlay" id="loginOverlay">
  <div class="login-box">
    <h2>智慧交通垂域智能体 登录</h2>
    <div class="error" id="loginError"></div>
    <input type="text" id="loginUser" placeholder="用户名" autocomplete="username">
    <input type="password" id="loginPass" placeholder="密码" autocomplete="current-password">
    <button onclick="doLogin()">登 录</button>
  </div>
</div>
<div class="fb-modal" id="fbModal" style="display:none">
  <div class="fb-modal-content">
    <div class="fb-modal-header">
      <span>📊 反馈记录</span>
      <button onclick="closeFeedbackModal()">×</button>
    </div>
    <div class="fb-modal-body" id="fbModalBody">
      <div class="fb-tabs">
        <button class="active" onclick="switchFbTab('my', this)">我的反馈</button>
        <button onclick="switchFbTab('all', this)" id="fbTabAll">全部反馈</button>
        <button onclick="switchFbTab('pinned', this)" id="fbTabPinned">📌 待优化</button>
        <button onclick="switchFbTab('stats', this)" id="fbTabStats">统计</button>
        <button onclick="switchFbTab('needs', this)" id="fbTabNeeds" style="display:none">📋 用户需求</button>
      </div>
      <div id="fbContent"></div>
    </div>
  </div>
</div>
<div class="fb-modal" id="editFbModal" style="display:none">
  <div class="fb-modal-content" style="width:400px;">
    <div class="fb-modal-header">
      <span>✏️ 编辑反馈</span>
      <button onclick="closeEditFbModal()">×</button>
    </div>
    <div class="fb-modal-body">
      <div style="margin-bottom:12px;">
        <label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px;">评价</label>
        <select id="editFbRating" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:14px;">
          <option value="">请选择</option>
          <option value="satisfied">👍 有帮助</option>
          <option value="partial">🤔 部分解决</option>
          <option value="unsatisfied">👎 没帮助</option>
        </select>
      </div>
      <div style="margin-bottom:12px;">
        <label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px;">补充说明</label>
        <textarea id="editFbText" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:14px;resize:vertical;min-height:80px;" placeholder="可选"></textarea>
      </div>
      <button onclick="submitEditFeedback()" style="width:100%;padding:10px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;">保存修改</button>
    </div>
  </div>
</div>
<div class="fb-modal" id="memModal" style="display:none">
  <div class="fb-modal-content">
    <div class="fb-modal-header">
      <span>🧠 记忆管理</span>
      <button onclick="closeMemoryModal()">×</button>
    </div>
    <div class="fb-modal-body" id="memModalBody">
      <div class="fb-tabs" id="memTabs">
        <button class="active" onclick="switchMemTab('preference', this)">偏好</button>
        <button onclick="switchMemTab('habit', this)">习惯</button>
        <button onclick="switchMemTab('fact', this)">事实</button>
      </div>
      <div id="memContent"></div>
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid #e5e7eb;">
        <button onclick="showAddMemoryForm()" style="padding:8px 16px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:13px;cursor:pointer;">＋ 添加记忆</button>
      </div>
      <div id="memForm" style="display:none;margin-top:12px;padding:12px;background:#f8f9fa;border-radius:8px;">
        <div style="margin-bottom:8px;">
          <label style="font-size:12px;color:#666;">类型</label>
          <select id="memType" style="width:100%;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;">
            <option value="preference">偏好</option>
            <option value="habit">习惯</option>
            <option value="fact">事实</option>
          </select>
        </div>
        <div style="margin-bottom:8px;">
          <label style="font-size:12px;color:#666;">键</label>
          <select id="memKey" onchange="onMemKeyChange()" style="width:100%;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;">
            <option value="reply_style">回复风格（简洁/详细）</option>
            <option value="focus_project">关注的项目</option>
            <option value="preferred_format">偏好的格式（表格/列表）</option>
            <option value="common_device">常用设备</option>
            <option value="diagnose_focus">诊断关注点（硬盘/内存/传感器）</option>
            <option value="work_pattern">工作模式（先诊断再修复）</option>
            <option value="role">角色/职责</option>
            <option value="background">背景信息</option>
            <option value="__custom__">自定义...</option>
          </select>
          <input id="memCustomKey" style="display:none;width:100%;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;margin-top:4px;" placeholder="输入自定义键名">
        </div>
        <div style="margin-bottom:8px;">
          <label style="font-size:12px;color:#666;">值</label>
          <textarea id="memValue" rows="3" style="width:100%;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;resize:vertical;" placeholder="例如：优先使用德会项目"></textarea>
        </div>
        <div style="display:flex;gap:8px;">
          <button onclick="saveMemory()" style="padding:6px 16px;background:#2ecc71;color:#fff;border:none;border-radius:4px;font-size:13px;cursor:pointer;">保存</button>
          <button onclick="cancelMemoryForm()" style="padding:6px 16px;background:#95a5a6;color:#fff;border:none;border-radius:4px;font-size:13px;cursor:pointer;">取消</button>
          <input type="hidden" id="memEditId" value="">
        </div>
      </div>
    </div>
  </div>
</div>
<div class="fb-modal" id="repairModal" style="display:none">
  <div class="fb-modal-content" style="width:500px;">
    <div class="fb-modal-header">
      <span>🔧 修复确认</span>
      <button onclick="closeRepairModal()">×</button>
    </div>
    <div class="fb-modal-body" id="repairBody">
    </div>
  </div>
</div>
<div class="guide-panel" id="guidePanel">
  <h3>🤖 智慧交通垂域智能体 使用指南</h3>
  <p style="color:#888;font-size:13px;margin-bottom:12px;">点击右上角「📖 指南」切换显示</p>
  <h3>支持的对话类型</h3>
  <ul>
    <li><b>项目状态查询</b> — "查看德会项目状态"</li>
    <li><b>异常概览</b> — "查看所有异常设备"</li>
    <li><b>设备诊断</b> — "诊断设备 10.145.4.1"</li>
    <li><b>设备信息</b> — "查看设备信息 10.145.4.1"</li>
    <li><b>日志分析</b> — "分析日志"</li>
    <li><b>SSH查询</b> — 直接输入 cat/tail/ls 等命令</li>
    <li><b>飞书报告</b> — "获取最新飞书报告"</li>
    <li><b>钉钉推送</b> — "推送到钉钉"</li>
  </ul>
  <h3>🚦 交通数据分析（南京仙新路）</h3>
  <ul>
    <li><b>流量查询</b> — "今天仙新路流量怎么样"、"查K9+820流量"</li>
    <li><b>流量趋势</b> — "分析5月31号的流量趋势"</li>
    <li><b>事件查询</b> — "最近有哪些事件"、"查变道事件"</li>
    <li><b>事件统计</b> — "统计今天的事件分布"</li>
    <li><b>设备健康</b> — "查设备运行状态"</li>
    <li><b>综合报告</b> — "交通总结报告"、"分析异常"、"预测流量"</li>
  </ul>
  <h3>可用工具一览（共23个）</h3>
  <p style="font-size:13px;color:#888;margin-bottom:8px;">以下工具按触发场景分类，AI 自动判断调用：</p>
  <h3>🔍 MEC诊断类</h3>
  <ul>
    <li><b>mec_diagnose_device</b> — 6维度SSH诊断设备：物理机、容器、进程、ROS、数据源、传感器。输入IP或设备名即触发</li>
    <li><b>mec_llm_diagnose_device</b> — LLM深度分析：先采集全部数据，再由AI做根因分析、影响评估和修复建议。基本诊断不明确时自动触发</li>
    <li><b>mec_diagnose_project</b> — 批量诊断项目下所有异常设备。输入项目名（如"德会"）即触发</li>
  </ul>
  <h3>📊 MEC查询类</h3>
  <ul>
    <li><b>query_mec_abnormal</b> — 查询所有项目异常设备统计概览。无参数，问"有多少异常设备"即触发</li>
    <li><b>query_mec_device_from_db</b> — 从MySQL查设备状态（无需SSH）。输入IP或设备名，用于快速查看概况或离线设备历史</li>
    <li><b>query_mec_project_from_db</b> — 从MySQL查项目汇总统计：设备数、在线率、传感器健康率、异常列表。输入项目名触发</li>
    <li><b>mec_device_info</b> — 查设备详细信息：硬盘、内存、CPU、网络、运行时间。支持多维度组合查询</li>
  </ul>
  <h3>📋 日志与报告（飞书）</h3>
  <ul>
    <li><b>feishu_analyze_logs</b> — 分析飞书监控报告，P0-P3分级告警，与历史对比（持续/新增/恢复/恶化/好转）。输入项目名可选过滤</li>
    <li><b>feishu_llm_analyze_logs</b> — LLM深度分析日志，给出整体概况、突出问题、趋势变化和关键建议</li>
    <li><b>feishu_fetch_report</b> — 获取飞书最新原始监控报告原文。无参数，不分析</li>
  </ul>
  <h3>⚙️ MEC操作类</h3>
  <ul>
    <li><b>mec_ssh_exec</b> — 在设备上执行只读SSH命令（cat/tail/ls/ps/df等）。支持容器内执行和ROS环境。自动过滤危险命令</li>
    <li><b>push_to_dingtalk</b> — 推送消息到钉钉群。HMAC-SHA256签名认证。需指定标题和内容</li>
    <li><b>mec_repair_device</b> — 安全修复操作（重启容器/进程/服务、清理缓存/日志/临时文件），需用户前端确认</li>
  </ul>
  <h3>📋 事件图片类</h3>
  <ul>
    <li><b>query_mec_event_records</b> — 从MySQL查询设备/项目的事件记录列表，支持多条件过滤</li>
    <li><b>query_mec_project_event_stats</b> — 查询项目事件统计汇总（按类型/设备分类）</li>
    <li><b>query_mec_event_image</b> — 根据事件ID从远程设备抓取事件图片</li>
  </ul>
  <h3>🚦 交通数据分析类（MongoDB雷达数据）</h3>
  <ul>
    <li><b>query_server_traffic_flow</b> — 查询指定路段/时间段的断面流量（车流量/平均速度/时间占有率/道路状态）</li>
    <li><b>query_server_events</b> — 查询雷达事件记录（事件类型、车牌、车速、车道、方向等）</li>
    <li><b>query_server_event_stats</b> — 按事件类型统计分布（违章/拥堵/异常分类汇总）</li>
    <li><b>query_server_device_metrics</b> — 查询MEC设备运行健康指标（CPU/内存/磁盘/温度/告警）</li>
    <li><b>query_server_traffic_pattern</b> — 综合交通流分析：时间序列趋势、高峰识别、拥堵分析</li>
    <li><b>query_server_analysis_report</b> — LLM二次分析报告（总结/异常识别/流量预测/事件-流量关联）</li>
  </ul>
  <h3>ℹ️ 辅助</h3>
  <ul>
    <li><b>help_info</b> — 获取使用帮助。问"你能做什么"即触发</li>
    <li><b>memory</b> — 管理用户记忆（偏好/习惯/事实），Agent自动维护</li>
  </ul>
  <h3>触发机制</h3>
  <ul>
    <li>AI根据用户意图自动选择合适的工具，无需手动指定</li>
    <li>诊断数据源优先级：数据库 > 飞书报告（除非明确要求飞书）</li>
    <li>上下文中继承上次对话的设备IP和项目名，后续可省略</li>
    <li>交通分析数据源：MongoDB radarData（雷达实时数据，南京仙新路）</li>
  </ul>
  <h3>提示</h3>
  <ul>
    <li>首次使用需等待 20-40 秒初始化</li>
    <li>支持流式输出，实时查看回复</li>
    <li>会话历史自动保存在浏览器本地</li>
    <li>每次对话后可对回答进行评价，帮助优化</li>
  </ul>
</div>
<script>
var API_KEY = __API_KEY__;
var FEEDBACK_DELAY = __FEEDBACK_DELAY__;
var AVAILABLE_MODELS = __AVAILABLE_MODELS__;
var md = window.markdownit({ html: true, linkify: true, typographer: true, breaks: true, tables: true });
var sessions = [];
var currentSessionId = null;
var loggedIn = false;

function renderMD(text) {
  var html = md.render(text);
  // 用 highlight.js 高亮代码块
  var tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.querySelectorAll('pre code').forEach(function(block) {
    if (window.hljs) hljs.highlightElement(block);
  });
  return tmp.innerHTML;
}

function scrollToBottom() {
  var area = document.getElementById('chatArea');
  area.scrollTop = area.scrollHeight;
}

function addMsgToDOM(type, content, extra, isStream) {
  var area = document.getElementById('chatArea');
  var div = document.createElement('div');
  div.className = 'msg ' + type;
  if (isStream) div.id = 'streaming-msg';
  var bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = renderMD(content);
  div.appendChild(bubble);
  area.appendChild(div);
  scrollToBottom();
  return bubble;
}

function removeTyping() {
  var el = document.getElementById('streaming-msg');
  if (el) {
    el.id = '';
    el.classList.remove('streaming');
  }
}

function saveSessions() {
  try { localStorage.setItem('mec_sessions', JSON.stringify(sessions)); } catch(e) {}
}

function loadSessions() {
  try {
    var data = localStorage.getItem('mec_sessions');
    if (data) sessions = JSON.parse(data);
  } catch(e) { sessions = []; }
}

function getCurrentSession() {
  return sessions.find(function(s) { return s.id === currentSessionId; });
}

function renderSessionList() {
  var list = document.getElementById('sessionList');
  list.innerHTML = '';
  sessions.slice().reverse().forEach(function(s) {
    var div = document.createElement('div');
    div.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
    var preview = s.messages.length > 0 ? s.messages[s.messages.length-1].content.slice(0, 30) : '新对话';
    div.innerHTML = '<div>' + preview + '</div><div class="time">' + (s.time || '') + '</div>';
    div.onclick = function() { switchSession(s.id); };
    var del = document.createElement('button');
    del.className = 'delete-btn';
    del.textContent = '×';
    del.onclick = function(e) { e.stopPropagation(); deleteSession(s.id); };
    div.appendChild(del);
    list.appendChild(div);
  });
}

function switchSession(id) {
  currentSessionId = id;
  renderSessionList();
  renderMessages();
  document.getElementById('feedbackBar').classList.remove('show');
}

function renderMessages() {
  var area = document.getElementById('chatArea');
  area.innerHTML = '';
  var session = getCurrentSession();
  if (!session) return;
  session.messages.forEach(function(m) {
    addMsgToDOM(m.type, m.content, m.extra || {});
  });
  scrollToBottom();
}

function deleteSession(id) {
  sessions = sessions.filter(function(s) { return s.id !== id; });
  saveSessions();
  if (currentSessionId === id) {
    if (sessions.length > 0) currentSessionId = sessions[sessions.length-1].id;
    else newSession();
  }
  renderSessionList();
  renderMessages();
}

function newSession() {
  var id = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2,6);
  sessions.push({ id: id, messages: [], time: new Date().toLocaleString() });
  currentSessionId = id;
  saveSessions();
  renderSessionList();
  var area = document.getElementById('chatArea');
  area.innerHTML = WELCOME_HTML;
  document.getElementById('msgInput').focus();
}

var WELCOME_HTML = '<div class="msg bot"><div class="bubble">' +
  renderMD('## 🚦 智慧交通垂域智能体 已就绪\n\n' +
    '> 系统已加载 **15 个诊断工具**，覆盖设备诊断、日志分析、数据查询与远程操作。\n\n' +
    '### 📡 能力矩阵\n' +
    '| 能力 | 示例指令 |\n' +
    '|------|---------|\n' +
    '| 🔍 **设备诊断** | `诊断设备 10.145.4.1` — 6 维度 SSH 深度扫描 |\n' +
    '| 📊 **项目总览** | `查看德会项目状态` — 批量诊断项目下所有异常设备 |\n' +
    '| ⚠️ **异常监控** | `查看所有异常设备` — 异常统计与分级概览 |\n' +
    '| 📋 **日志分析** | `分析日志` — P0-P3 分级告警与趋势变化 |\n' +
    '| 🖥️ **设备信息** | `查询设备 10.145.4.1` — CPU/内存/硬盘/网络指标 |\n' +
    '| 🔬 **深度分析** | 诊断后自动触发 — LLM 根因分析与修复建议 |\n' +
    '| 🔗 **钉钉推送** | `推送到钉钉` — 结果实时通知 |\n' +
    '| 🛠️ **远程操作** | 支持 SSH 命令、容器重启、缓存清理等 |\n\n' +
    '> 💡 点击右上角 `📖 指南` 查看完整工具说明，或直接输入问题开始诊断。') +
  '</div></div>';

function showWelcome() {
  var area = document.getElementById('chatArea');
  if (area.children.length === 0) {
    area.innerHTML = WELCOME_HTML;
  }
}

function onInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

async function sendMessage() {
  var input = document.getElementById('msgInput');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  input.style.height = 'auto';
  var btn = document.getElementById('sendBtn');

  var stopKeywords = ["/stop", "停止", "取消", "终止", "停下"];
  var isStop = stopKeywords.some(function(kw) { return msg.indexOf(kw) !== -1; });
  if (isStop && window._streamController) {
    window._stoppedByUser = true;
    window._streamController.abort();
    window._streamController = null;
  }

  var session = getCurrentSession();
  if (!session) { newSession(); session = getCurrentSession(); }
  session.messages.push({ type: 'user', content: msg });
  saveSessions();
  addMsgToDOM('user', msg, {});

  var contentDiv = addMsgToDOM('bot', '', {}, true);
  var fullText = '';
  var toolDiv = null;
  var toolCount = 0;
  window._streamingFullText = '';
  var controller = new AbortController();
  var timeoutId = setTimeout(function() { controller.abort(); }, 120000);

try {
      var selectedModel = document.getElementById('modelSelect').value;
      var response = await fetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
        body: JSON.stringify({ message: msg, session_id: currentSessionId, model: selectedModel }),
        signal: controller.signal
      });
      clearTimeout(timeoutId);

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      while (true) {
        var result = await reader.read();
        if (result.done) break;
        buffer += decoder.decode(result.value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.startsWith('event: ')) {
            var eventType = line.slice(7).trim();
            var dataLine = lines[i + 1];
            if (dataLine && dataLine.startsWith('data: ')) {
              try {
                var data = JSON.parse(dataLine.slice(6));
                handleEvent(eventType, data);
              } catch(e) {}
            }
          }
        }
      }
    } catch (e) {
      clearTimeout(timeoutId);
      if (isStop) {
        contentDiv.innerHTML = '✅ 已取消';
        var msgDiv = contentDiv.parentNode;
        if (msgDiv) msgDiv.classList.remove('streaming');
        btn.disabled = false;
        window._streamingFullText = '';
        window._streamController = null;
      } else if (window._stoppedByUser) {
        window._stoppedByUser = false;
        window._streamingFullText = '';
        window._streamController = null;
      } else {
        removeTyping();
        addMsgToDOM('error', '网络错误: ' + e.message, {}, true);
        btn.disabled = false;
        window._streamingFullText = '';
      }
    }

  function handleEvent(type, data) {
    try {
      if (type === 'info') {
        var msg = data.message || '';
        fullText += (fullText ? '\n' : '') + '💬 ' + msg;
        contentDiv.innerHTML = renderMD(fullText) + '<span class="cursor">▌</span>';
        scrollToBottom();
        return;
      }
      if (type === 'token') {
        removeTyping();
        fullText += data.content || '';
        window._streamingFullText = fullText;
        contentDiv.innerHTML = renderMD(fullText) + '<span class="cursor">▌</span>';
        scrollToBottom();
      } else if (type === 'tool_start') {
        toolCount++;
        var tag = document.createElement('div');
        tag.className = 'tool-tag running';
        tag.id = 'tool-' + toolCount;
        tag.textContent = '⚙️ ' + (data.name || '工具') + ' 运行中...';
        contentDiv.parentNode.insertBefore(tag, contentDiv);
      } else if (type === 'diag_progress') {
        var ico = {'ok':'✅','error':'❌','warning':'⚠️','skip':'⏭️','progress':'⏳'}[data.status] || '❓';
        if (data.status === 'progress') {
          // 进度步骤 - 追加到进度区域
          var pTag = document.getElementById('diag-progress');
          if (!pTag) {
            pTag = document.createElement('div');
            pTag.id = 'diag-progress';
            pTag.className = 'diag-progress-bar';
            contentDiv.parentNode.insertBefore(pTag, contentDiv);
          }
          pTag.innerHTML = pTag.innerHTML + '<div class="dim-row">' + ico + ' <b>' + data.name + '</b>: ' + data.detail + '</div>';
        } else {
          // 最终结果 - 增量更新 summary 面板
          var panel = document.getElementById('diag-summary-panel');
          if (!panel) {
            panel = document.createElement('div');
            panel.id = 'diag-summary-panel';
            panel.className = 'diag-summary-card';
            contentDiv.parentNode.insertBefore(panel, contentDiv);
          }
          panel.style.display = 'block';
          var color = {'ok':'#10b981','warning':'#f59e0b','error':'#ef4444','skip':'#9ca3af'}[data.status] || '#888';
          // 清除之前的进度条
          var pBar = document.getElementById('diag-progress');
          if (pBar) { pBar.remove(); }
          // 找到或创建该维度的行
          var row = document.getElementById('dim-' + data.name);
          if (!row) {
            row = document.createElement('div');
            row.id = 'dim-' + data.name;
            row.className = 'dim-row';
            panel.appendChild(row);
          }
          row.innerHTML = '<span class="dim-name" style="color:' + color + ';">' + ico + ' ' + data.name + '</span>'
                       + '<span class="dim-detail">' + data.detail + '</span>';
          scrollToBottom();
        }
      } else if (type === 'diag_summary') {
        var dims = data.dimensions || [];
        var ip = data.ip || '';
        var overall = data.overall || '';
        var cause = data.root_cause || '';
        var time = data.diagnosis_time || '';
        var chatArea = document.getElementById('chatArea');
        var panel = document.getElementById('diag-summary-panel');
        if (!panel) {
          panel = document.createElement('div');
          panel.id = 'diag-summary-panel';
          panel.className = 'diag-summary-card';
          contentDiv.parentNode.insertBefore(panel, contentDiv);
        }
        panel.style.display = 'block';
        var statusColors = {'ok':'#10b981','warning':'#f59e0b','error':'#ef4444','skip':'#9ca3af'};
        var statusIcons = {'ok':'✅','warning':'⚠️','error':'❌','skip':'⏭️'};
        var html = '<div class="summary-header">';
        var ovIco = {'normal':'✅','warning':'⚠️','error':'❌'}[overall] || '❓';
        var ovLabel = {'normal':'正常','warning':'注意','error':'异常'}[overall] || overall;
        html += '<b>' + ovIco + ' 诊断结果: ' + ip + ' (' + ovLabel + ')</b>';
        html += '<span style="color:var(--text-muted);font-size:11px;">' + time + '</span>';
        html += '</div>';
        html += '<div class="summary-body">';
        for (var i = 0; i < dims.length; i++) {
          var d = dims[i];
          if (d.name === '数据库记录' || d.name === '登录建议' || d.name === '网络建议') continue;
          var c = statusColors[d.status] || '#888';
          var ic = statusIcons[d.status] || '❓';
          html += '<div class="dim-row">';
          html += '<span class="dim-name" style="color:' + c + ';">' + ic + ' ' + d.name + '</span>';
          html += '<span class="dim-detail">' + (d.detail || '') + '</span>';
          html += '</div>';
          if (d.log_errors_detail && d.log_errors_detail.length > 0) {
            html += '<div style="margin-left:90px;padding:4px 8px;background:rgba(248,81,73,0.08);border-left:2px solid var(--danger);border-radius:0 4px 4px 0;font-size:12px;color:var(--text-secondary);margin-bottom:4px;">';
            for (var j = 0; j < d.log_errors_detail.length; j++) {
              html += '<div>' + d.log_errors_detail[j] + '</div>';
            }
            html += '</div>';
          }
          if (d.topic_rates && d.topic_rates.length > 0) {
            var hasZero = false;
            for (var kk = 0; kk < d.topic_rates.length; kk++) {
              if (d.topic_rates[kk].is_zero) { hasZero = true; break; }
            }
            var bg = hasZero ? 'rgba(248,81,73,0.08)' : 'rgba(63,185,80,0.06)';
            var bd = hasZero ? 'var(--danger)' : 'var(--success)';
            html += '<div style="margin-left:90px;padding:4px 8px;background:' + bg + ';border-left:2px solid ' + bd + ';border-radius:0 4px 4px 0;font-size:12px;color:var(--text-secondary);margin-bottom:4px;">';
            for (var k = 0; k < d.topic_rates.length; k++) {
              var tItem = d.topic_rates[k];
              if (typeof tItem === 'string') {
                html += '<div>' + tItem + '</div>';
              } else {
                html += '<div style="color:' + (tItem.is_zero ? 'var(--danger)' : 'var(--text-secondary)') + ';">' + tItem.topic + '</div>';
              }
            }
            html += '</div>';
          }
        }
        if (cause) {
          html += '<div class="root-cause-box">';
          html += '<b>🔍 根因分析:</b> ' + cause;
          html += '</div>';
        }
        html += '</div>';
        panel.innerHTML = html;
        scrollToBottom();
      } else if (type === 'tool_end') {
        var tag = document.getElementById('tool-' + toolCount);
        if (tag) {
          tag.className = 'tool-tag done';
          tag.textContent = '✅ ' + (data.name || '工具') + ' 完成';
        }
      } else if (type === 'tool_result') {
        var resultText = data.output || '';
        if (data.name === 'mec_repair_device' && resultText.indexOf('"status": "pending_confirmation"') !== -1) {
          try {
            var repairData = JSON.parse(resultText);
            if (repairData.status === 'pending_confirmation') {
              showRepairConfirm(repairData);
            }
          } catch(e) {}
        }
        if (resultText.length > 6000) resultText = resultText.slice(0, 6000) + '\n\n...(截断)';
        var details = document.createElement('details');
        details.style.margin = '4px 0';
        var summary = document.createElement('summary');
        summary.style.cssText = 'font-size:12px;color:var(--text-muted);cursor:pointer;';
        summary.textContent = '📋 ' + (data.name || '工具') + ' 返回结果';
        details.appendChild(summary);
        var pre = document.createElement('div');
        pre.style.cssText = 'font-size:12px;background:var(--bg-base);border:1px solid var(--border-color);border-radius:4px;padding:8px;margin-top:4px;overflow-x:auto;';
        pre.innerHTML = renderMD(resultText);
        details.appendChild(pre);
        contentDiv.parentNode.insertBefore(details, contentDiv.nextSibling);
        scrollToBottom();
      } else if (type === 'error') {
        var errMsg = (data.message || '');
        if (errMsg.includes('AccountQuotaExceeded') || errMsg.includes('429')) {
          errMsg = 'LLM API 配额超限，请稍后再试（每日 00:48 重置）。已执行的工具结果见上方。';
        } else if (errMsg.includes('timed out') || errMsg.includes('timeout')) {
          errMsg = '模型响应超时，请稍后重试。';
        }
        fullText += '\n\n<font color="red">[错误] ' + errMsg + '</font>';
        contentDiv.innerHTML = renderMD(fullText);
        btn.disabled = false;
      } else if (type === 'done') {
        var status = data.status;
        if (status === 'stopped') {
          contentDiv.innerHTML = '✅ 已取消';
        } else {
          contentDiv.innerHTML = renderMD(fullText);
          void contentDiv.offsetHeight;
          try {
            contentDiv.querySelectorAll('pre code').forEach(function(block) {
              if (window.hljs) hljs.highlightElement(block);
            });
          } catch(e) {}
          var copyBtn = document.createElement('button');
          copyBtn.className = 'copy-btn';
          copyBtn.textContent = '复制';
          copyBtn.onclick = function() {
            navigator.clipboard.writeText(fullText).then(function() {
              copyBtn.textContent = '✅ 已复制';
              setTimeout(function() { copyBtn.textContent = '复制'; }, 2000);
            });
          };
          contentDiv.appendChild(copyBtn);
          var session = getCurrentSession();
          if (session) {
            session.messages.push({ type: 'bot', content: fullText, extra: {} });
            saveSessions();
          }
        }
        var msgDiv = contentDiv.parentNode;
        if (msgDiv) msgDiv.classList.remove('streaming');
        scrollToBottom();
        btn.disabled = false;
        window._streamingFullText = '';
        window._streamController = null;
      } else if (type === 'feedback_request') {
        setTimeout(function() {
          var fbBar = document.getElementById('feedbackBar');
          var prompt = document.getElementById('fbPrompt');
          var summary = data.summary || '';
          if (summary) {
            prompt.textContent = '本次请求「' + summary + '」对你有帮助吗？';
          } else {
            prompt.textContent = '这次回答对你有帮助吗？';
          }
          if (fbBar) fbBar.classList.add('show');
        }, FEEDBACK_DELAY);
      }
    } catch(e) { console.error('handleEvent error', e); }
  }

  window._streamController = controller;
}

var currentRating = null;
function rate(rating) {
  currentRating = rating;
  document.querySelectorAll('.fb-btn').forEach(function(b) {
    b.classList.toggle('selected', b.dataset.rating === rating);
  });
}
function submitFeedback() {
  if (!currentRating) return;
  var text = document.getElementById('fbTextarea').value;
  fetch('/api/v1/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify({ session_id: currentSessionId, rating: currentRating, feedback_text: text })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      document.getElementById('feedbackBar').classList.remove('show');
      currentRating = null;
      document.querySelectorAll('.fb-btn').forEach(function(b) { b.classList.remove('selected'); });
      document.getElementById('fbTextarea').value = '';
    }
  });
}

function doLogin() {
  var user = document.getElementById('loginUser').value.trim();
  var pass = document.getElementById('loginPass').value.trim();
  fetch('/api/v1/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: pass })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      loggedIn = true;
      document.getElementById('loginOverlay').style.display = 'none';
      document.getElementById('userInfo').textContent = '👤 ' + user;
      if (user === 'admin') document.getElementById('fbTabNeeds').style.display = '';
      initApp();
    } else {
      document.getElementById('loginError').textContent = d.error || '登录失败';
    }
  });
}

function logout() {
  fetch('/api/v1/logout').then(function() {
    loggedIn = false;
    document.getElementById('loginOverlay').style.display = 'flex';
    document.getElementById('loginError').textContent = '';
    document.getElementById('userInfo').textContent = '';
    document.getElementById('chatArea').innerHTML = '';
    document.getElementById('sessionList').innerHTML = '';
    sessions = [];
    currentSessionId = null;
  });
}

function checkLogin() {
  fetch('/api/v1/me').then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      loggedIn = true;
      document.getElementById('loginOverlay').style.display = 'none';
      document.getElementById('userInfo').textContent = '👤 ' + d.data.username;
      if (d.data.username === 'admin') document.getElementById('fbTabNeeds').style.display = '';
      initApp();
    } else {
      document.getElementById('loginOverlay').style.display = 'flex';
    }
  }).catch(function() {
    document.getElementById('loginOverlay').style.display = 'flex';
  });
}

function initApp() {
  loadSessions();
  if (sessions.length === 0) {
    newSession();
  } else {
    currentSessionId = sessions[sessions.length-1].id;
    renderSessionList();
    renderMessages();
  }
  showWelcome();
  document.getElementById('msgInput').focus();
  document.getElementById('msgInput').addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
  });
  _initModelSelect();
}

function _initModelSelect() {
  var sel = document.getElementById('modelSelect');
  if (!sel) return;
  var keys = Object.keys(AVAILABLE_MODELS);
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var opt = document.createElement('option');
    opt.value = k;
    opt.textContent = AVAILABLE_MODELS[k].label;
    sel.appendChild(opt);
  }
}

function onModelChange() {
  var sel = document.getElementById('modelSelect');
  var label = AVAILABLE_MODELS[sel.value] ? AVAILABLE_MODELS[sel.value].label : sel.value;
  // Show brief indicator in the page
  var info = document.getElementById('userInfo');
  if (info) {
    info.textContent = info.textContent.replace(/ \[.*?\]$/, '') + ' [' + label + ']';
  }
}

function showGuide() {
  var panel = document.getElementById('guidePanel');
  panel.classList.toggle('show');
}

function showFeedbackHistory() {
  document.getElementById('fbModal').style.display = 'flex';
  loadFeedbackMy();
}

function closeFeedbackModal() {
  document.getElementById('fbModal').style.display = 'none';
}

function switchFbTab(tab, btn) {
  document.querySelectorAll('.fb-tabs button').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  if (tab === 'my') loadFeedbackMy();
  else if (tab === 'all') loadFeedbackAll();
  else if (tab === 'pinned') loadPinnedFeedback();
  else if (tab === 'stats') loadFeedbackStats();
  else if (tab === 'needs') loadAdminConversations();
}

function loadFeedbackMy() {
  var el = document.getElementById('fbContent');
  el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">加载中...</div>';
  fetch('/api/v1/feedback/my', { headers: { 'X-API-Key': API_KEY } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.success) renderFeedbackList(el, d.data, '我的反馈');
      else el.innerHTML = '<div style="color:#e74c3c;">' + (d.error || '加载失败') + '</div>';
    }).catch(function(e) { el.innerHTML = '<div style="color:#e74c3c;">请求失败: ' + e.message + '</div>'; });
}

function loadFeedbackAll() {
  var el = document.getElementById('fbContent');
  el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">加载中...</div>';
  fetch('/api/v1/feedback/list', { headers: { 'X-API-Key': API_KEY } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.success) renderFeedbackList(el, d.data, '全部反馈');
      else el.innerHTML = '<div style="color:#e74c3c;">' + (d.error || '无权限') + '</div>';
    }).catch(function(e) { el.innerHTML = '<div style="color:#e74c3c;">请求失败: ' + e.message + '</div>'; });
}

function loadFeedbackStats() {
  var el = document.getElementById('fbContent');
  el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">加载中...</div>';
  fetch('/api/v1/feedback/stats', { headers: { 'X-API-Key': API_KEY } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.success) {
        var s = d.data;
        var ratingMap = {'satisfied': '👍 有帮助', 'partial': '🤔 部分解决', 'unsatisfied': '👎 没帮助', 'pending': '⏳ 待评价'};
        var colorMap = {'satisfied': '#2ecc71', 'partial': '#f39c12', 'unsatisfied': '#e74c3c', 'pending': '#95a5a6'};
        var html = '<div class="fb-stats">';
        ['total','satisfied','partial','unsatisfied','pending'].forEach(function(k) {
          var label = k === 'total' ? '总计' : ratingMap[k] || k;
          html += '<div class="fb-stat-card"><div class="num" style="color:' + (k==='total'?'#333':colorMap[k]) + '">' + s[k] + '</div><div class="label">' + label + '</div></div>';
        });
        html += '</div>';
        el.innerHTML = html;
      } else {
        el.innerHTML = '<div style="color:#e74c3c;">' + (d.error || '加载失败') + '</div>';
      }
    }).catch(function(e) { el.innerHTML = '<div style="color:#e74c3c;">请求失败: ' + e.message + '</div>'; });
}

function renderFeedbackList(el, records, title) {
  if (!records || records.length === 0) {
    el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">暂无反馈记录</div>';
    return;
  }
  var ratingMap = {'satisfied': '👍 有帮助', 'partial': '🤔 部分解决', 'unsatisfied': '👎 没帮助'};
  var colorMap = {'satisfied': '#2ecc71', 'partial': '#f39c12', 'unsatisfied': '#e74c3c'};
  var isMy = title === '我的反馈';
  var isAll = title === '全部反馈';
  var isPinned = title === '📌 待优化';
  var html = '';
  var shown = 0;
  records.forEach(function(r) {
    if (isAll && (!r.rating || r.rating === 'satisfied')) return;
    shown++;
    var rating = r.rating ? ratingMap[r.rating] || r.rating : '⏳ 待评价';
    var color = r.rating ? colorMap[r.rating] || '#999' : '#95a5a6';
    var actions = '';
    try {
      var acts = typeof r.actions === 'string' ? JSON.parse(r.actions) : r.actions;
      if (acts && acts.length) actions = '🛠 ' + acts.map(function(a) { return a.name; }).join(', ');
    } catch(e) {}
    var pinBtn = '';
    if (isAll) {
      if (r.pinned) {
        pinBtn = ' <button onclick="unpinFeedback(' + r.id + ')" style="background:none;border:none;cursor:pointer;font-size:13px;color:#e74c3c;">📌 取消置顶</button>';
      } else {
        pinBtn = ' <button onclick="pinFeedback(' + r.id + ')" style="background:none;border:none;cursor:pointer;font-size:13px;color:#999;">📌 置顶</button>';
      }
    } else if (isPinned) {
      pinBtn = ' <button onclick="unpinFeedback(' + r.id + ')" style="background:none;border:none;cursor:pointer;font-size:13px;color:#e74c3c;">📌 移出</button>';
    }
    html += '<div class="fb-record">' +
      '<div class="fb-intent">' + (r.intent || '(无意图)') + '</div>' +
      '<div class="fb-meta">' +
        '<span class="fb-rating" style="color:' + color + '">' + rating + '</span>' +
        (r.user_id ? '<span>👤 ' + r.user_id + '</span>' : '') +
        (r.auto_correctness != null ? '<span>🤖 自评 ' + r.auto_correctness + '/10</span>' : '') +
        '<span>' + (r.created_at || '') + '</span>' +
        (isMy ? ' <button class="edit-fb-btn" onclick="editFeedback(' + r.id + ')">✏️ 编辑</button>' : '') +
        pinBtn +
      '</div>' +
      (actions ? '<div style="font-size:11px;color:#888;margin-top:2px;">' + actions + '</div>' : '') +
      (r.feedback_text ? '<div style="font-size:12px;color:#555;margin-top:2px;background:#f8f9fa;padding:4px 8px;border-radius:4px;">💬 ' + r.feedback_text + '</div>' : '') +
    '</div>';
  });
  if (shown === 0 && isAll) {
    el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">所有反馈都已被标记为有帮助</div>';
    return;
  }
  el.innerHTML = html;
}

var editingFbId = null;
function editFeedback(id) {
  editingFbId = id;
  document.getElementById('editFbModal').style.display = 'flex';
  document.getElementById('editFbRating').value = '';
  document.getElementById('editFbText').value = '';
}

function closeEditFbModal() {
  document.getElementById('editFbModal').style.display = 'none';
  editingFbId = null;
}

function submitEditFeedback() {
  if (!editingFbId) return;
  var rating = document.getElementById('editFbRating').value;
  var text = document.getElementById('editFbText').value;
  if (!rating) { alert('请选择评价'); return; }
  fetch('/api/v1/feedback/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify({ id: editingFbId, rating: rating, feedback_text: text })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      closeEditFbModal();
      loadFeedbackMy();
    } else {
      alert(d.error || '更新失败');
    }
  }).catch(function(e) { alert('请求失败: ' + e.message); });
}

function loadPinnedFeedback() {
  var el = document.getElementById('fbContent');
  el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">加载中...</div>';
  fetch('/api/v1/feedback/pinned', { headers: { 'X-API-Key': API_KEY } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.success) renderFeedbackList(el, d.data, '📌 待优化');
      else el.innerHTML = '<div style="color:#e74c3c;">' + (d.error || '加载失败') + '</div>';
    }).catch(function(e) { el.innerHTML = '<div style="color:#e74c3c;">请求失败: ' + e.message + '</div>'; });
}

function loadAdminConversations() {
  var el = document.getElementById('fbContent');
  el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">加载中...</div>';
  fetch('/api/v1/admin/conversations?hours=72', { headers: { 'X-API-Key': API_KEY } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.success) { el.innerHTML = '<div style="color:var(--danger);">' + (d.error || '加载失败') + '</div>'; return; }
      var users = d.data || [];
      if (users.length === 0) { el.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">暂无对话数据</div>'; return; }
      var html = '';
      users.forEach(function(u) {
        var colors = {'satisfied':'var(--success)','partial':'var(--warning)','unsatisfied':'var(--danger)'};
        var labels = {'satisfied':'有帮助','partial':'部分解决','unsatisfied':'没帮助'};
        html += '<div style="margin-bottom:20px;border:1px solid var(--border-color);border-radius:12px;overflow:hidden;background:var(--bg-glass);">' +
          '<div style="padding:14px 18px;border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;align-items:center;background:rgba(74,158,255,0.04);">' +
          '<div><span style="font-weight:600;font-size:15px;color:var(--text-primary);">👤 ' + u.user_id + '</span>' +
          '<span style="margin-left:12px;font-size:12px;color:var(--text-muted);">最近活跃: ' + u.last_active.slice(0,16).replace('T',' ') + '</span></div>' +
          '<div style="display:flex;gap:10px;font-size:12px;">' +
          '<span style="color:var(--text-secondary);">共 ' + u.total + ' 次</span>' +
          (u.satisfied > 0 ? '<span style="color:var(--success);">👍 ' + u.satisfied + '</span>' : '') +
          (u.partial > 0 ? '<span style="color:var(--warning);">🤔 ' + u.partial + '</span>' : '') +
          (u.unsatisfied > 0 ? '<span style="color:var(--danger);">👎 ' + u.unsatisfied + '</span>' : '') +
          '</div></div>';
        u.conversations.forEach(function(c) {
          var rc = c.rating ? (colors[c.rating] || 'var(--text-muted)') : 'var(--text-muted)';
          var rl = c.rating ? (labels[c.rating] || c.rating) : '未评价';
          html += '<div style="padding:10px 18px;border-bottom:1px solid rgba(255,255,255,0.04);font-size:13px;">' +
            '<div style="display:flex;justify-content:space-between;align-items:flex-start;">' +
            '<div style="flex:1;"><span style="color:var(--text-primary);font-weight:500;">' + (c.intent || '(无意图)') + '</span>' +
            (c.actions && c.actions.length > 0 ? '<span style="margin-left:8px;font-size:11px;color:var(--text-muted);">🛠 ' + c.actions.join(', ') + '</span>' : '') +
            '</div>' +
            '<div style="display:flex;align-items:center;gap:6px;flex-shrink:0;margin-left:12px;">' +
            '<span style="font-size:11px;color:var(--text-muted);">' + c.created_at.slice(5,16).replace('T',' ') + '</span>' +
            '<span style="font-size:11px;color:' + rc + ';">' + rl + '</span>' +
            '</div></div>' +
            (c.feedback_text ? '<div style="margin-top:4px;font-size:12px;color:var(--text-secondary);background:rgba(255,255,255,0.03);padding:4px 10px;border-radius:6px;">💬 ' + c.feedback_text + '</div>' : '') +
            '</div>';
        });
        html += '</div>';
      });
      el.innerHTML = html;
    }).catch(function(e) { el.innerHTML = '<div style="color:var(--danger);">请求失败: ' + e.message + '</div>'; });
}

function pinFeedback(id) {
  fetch('/api/v1/feedback/pin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify({ id: id })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) loadFeedbackAll();
    else alert(d.error || '操作失败');
  }).catch(function(e) { alert('请求失败: ' + e.message); });
}

function unpinFeedback(id) {
  fetch('/api/v1/feedback/unpin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify({ id: id })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      var activeTab = document.querySelector('.fb-tabs button.active');
      if (activeTab && activeTab.textContent.indexOf('待优化') !== -1) loadPinnedFeedback();
      else loadFeedbackAll();
    } else alert(d.error || '操作失败');
  }).catch(function(e) { alert('请求失败: ' + e.message); });
}

var currentMemTab = 'preference';
var editingMemId = null;

function showMemoryPanel() {
  document.getElementById('memModal').style.display = 'flex';
  switchMemTab('preference', document.querySelector('#memTabs button'));
}

function closeMemoryModal() {
  document.getElementById('memModal').style.display = 'none';
  cancelMemoryForm();
}

function switchMemTab(tab, btn) {
  currentMemTab = tab;
  document.querySelectorAll('#memTabs button').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  loadMemories(tab);
}

function loadMemories(tab) {
  var el = document.getElementById('memContent');
  el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">加载中...</div>';
  fetch('/api/v1/memory/list?fact_type=' + encodeURIComponent(tab || currentMemTab), { headers: { 'X-API-Key': API_KEY } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.success) {
        var items = d.data || [];
        if (items.length === 0) {
          el.innerHTML = '<div style="text-align:center;padding:20px;color:#999;">暂无记忆</div>';
          return;
        }
        var html = '';
        items.forEach(function(m) {
          html += '<div class="fb-record" style="display:flex;justify-content:space-between;align-items:flex-start;">' +
            '<div style="flex:1;">' +
              '<div style="font-weight:500;">' + (m.key || '') + '</div>' +
              '<div style="font-size:12px;color:#555;margin-top:2px;">' + (m.value || '') + '</div>' +
              '<div style="font-size:11px;color:#999;margin-top:2px;">置信度: ' + (m.confidence != null ? (m.confidence * 100).toFixed(0) + '%' : '-') + ' · ' + (m.updated_at || '') + '</div>' +
            '</div>' +
            '<div style="display:flex;gap:4px;flex-shrink:0;margin-left:8px;">' +
              '<button onclick="editMemory(' + m.id + ',\'' + (m.fact_type || '') + '\',\'' + (m.key || '').replace(/'/g,"\\\\'") + '\',\'' + (m.value || '').replace(/'/g,"\\\\'") + '\')" style="background:none;border:none;cursor:pointer;font-size:13px;color:var(--primary);">✏️</button>' +
              '<button onclick="deleteMemory(' + m.id + ')" style="background:none;border:none;cursor:pointer;font-size:13px;color:#e74c3c;">🗑</button>' +
            '</div>' +
          '</div>';
        });
        el.innerHTML = html;
      } else {
        el.innerHTML = '<div style="color:#e74c3c;">' + (d.error || '加载失败') + '</div>';
      }
    }).catch(function(e) { el.innerHTML = '<div style="color:#e74c3c;">请求失败: ' + e.message + '</div>'; });
}

function showAddMemoryForm() {
  document.getElementById('memForm').style.display = 'block';
  document.getElementById('memType').value = currentMemTab;
  document.getElementById('memKey').value = 'reply_style';
  document.getElementById('memCustomKey').style.display = 'none';
  document.getElementById('memCustomKey').value = '';
  document.getElementById('memValue').value = '';
  document.getElementById('memEditId').value = '';
}

function cancelMemoryForm() {
  document.getElementById('memForm').style.display = 'none';
  editingMemId = null;
}

function onMemKeyChange() {
  var sel = document.getElementById('memKey');
  var custom = document.getElementById('memCustomKey');
  if (sel.value === '__custom__') {
    custom.style.display = 'block';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = '';
  }
}

var MEM_KEY_OPTIONS = ['reply_style','focus_project','preferred_format','common_device','diagnose_focus','work_pattern','role','background'];

function editMemory(id, type, key, value) {
  document.getElementById('memForm').style.display = 'block';
  document.getElementById('memType').value = type;
  if (MEM_KEY_OPTIONS.indexOf(key) !== -1) {
    document.getElementById('memKey').value = key;
    document.getElementById('memCustomKey').style.display = 'none';
    document.getElementById('memCustomKey').value = '';
  } else {
    document.getElementById('memKey').value = '__custom__';
    document.getElementById('memCustomKey').style.display = 'block';
    document.getElementById('memCustomKey').value = key;
  }
  document.getElementById('memValue').value = value;
  document.getElementById('memEditId').value = id;
}

function saveMemory() {
  var editId = document.getElementById('memEditId').value;
  var factType = document.getElementById('memType').value;
  var keySel = document.getElementById('memKey').value;
  var key = keySel === '__custom__' ? document.getElementById('memCustomKey').value.trim() : keySel;
  var value = document.getElementById('memValue').value.trim();
  if (!key || !value) { alert('键和值不能为空'); return; }
  var url = editId ? '/api/v1/memory/update' : '/api/v1/memory/create';
  var body = editId ? { id: parseInt(editId), fact_type: factType, key: key, value: value } : { fact_type: factType, key: key, value: value };
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify(body)
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      cancelMemoryForm();
      loadMemories(currentMemTab);
    } else {
      alert(d.error || '保存失败');
    }
  }).catch(function(e) { alert('请求失败: ' + e.message); });
}

function deleteMemory(id) {
  if (!confirm('确定要删除这条记忆吗？')) return;
  fetch('/api/v1/memory/' + id, {
    method: 'DELETE',
    headers: { 'X-API-Key': API_KEY }
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success) {
      loadMemories(currentMemTab);
    } else {
      alert(d.error || '删除失败');
    }
  }).catch(function(e) { alert('请求失败: ' + e.message); });
}

var pendingRepairs = [];
var currentRepairIndex = 0;

function showRepairConfirm(data) {
  pendingRepairs = [{
    ip: data.device_ip,
    action: data.action,
    target: data.target || '',
    action_desc: data.action_desc,
    command: data.command
  }];
  currentRepairIndex = 0;
  renderRepairModal();
  document.getElementById('repairModal').style.display = 'flex';
}

function closeRepairModal() {
  document.getElementById('repairModal').style.display = 'none';
  pendingRepairs = [];
  currentRepairIndex = 0;
}

function renderRepairModal() {
  if (currentRepairIndex >= pendingRepairs.length) {
    closeRepairModal();
    return;
  }
  var r = pendingRepairs[currentRepairIndex];
  var html = '<div style="margin-bottom:12px;">' +
    '<div style="font-weight:600;font-size:15px;margin-bottom:4px;">' + r.action_desc + '</div>' +
    '<div style="font-size:13px;color:#555;margin-bottom:8px;">' +
      '<div>设备: <b>' + r.ip + '</b></div>' +
      (r.target ? '<div>目标: <b>' + r.target + '</b></div>' : '') +
      '<div style="margin-top:4px;font-family:monospace;font-size:12px;background:#2d2d2d;color:#f8f8f2;padding:6px 10px;border-radius:4px;">$ ' + r.command + '</div>' +
    '</div>' +
  '</div>' +
  '<div style="display:flex;gap:8px;">' +
    '<button onclick="confirmRepair()" style="flex:1;padding:10px;background:#2ecc71;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;">✅ 确认执行</button>' +
    '<button onclick="skipRepair()" style="flex:1;padding:10px;background:#e74c3c;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;">✗ 跳过</button>' +
  '</div>';
  document.getElementById('repairBody').innerHTML = html;
}

function confirmRepair() {
  if (currentRepairIndex >= pendingRepairs.length) return;
  var r = pendingRepairs[currentRepairIndex];
  var el = document.getElementById('repairBody');
  el.innerHTML = '<div style="text-align:center;padding:20px;">⏳ 正在执行...</div>';
  fetch('/api/v1/repair/execute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    body: JSON.stringify({ ip: r.ip, action: r.action, target: r.target })
  }).then(function(resp) { return resp.json(); }).then(function(d) {
    if (d.success) {
      el.innerHTML = '<div style="text-align:center;padding:20px;">' +
        '<div style="font-size:48px;margin-bottom:8px;">✅</div>' +
        '<div style="font-weight:600;font-size:15px;color:#2ecc71;">执行成功</div>' +
        '<div style="font-size:12px;color:#666;margin-top:4px;font-family:monospace;">' + (d.output || '') + '</div>' +
        '<button onclick="closeRepairModal()" style="margin-top:16px;padding:8px 24px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;">关闭</button>' +
      '</div>';
    } else {
      el.innerHTML = '<div style="text-align:center;padding:20px;">' +
        '<div style="font-size:48px;margin-bottom:8px;">❌</div>' +
        '<div style="font-weight:600;font-size:15px;color:#e74c3c;">执行失败</div>' +
        '<div style="font-size:12px;color:#666;margin-top:4px;">' + (d.error || '未知错误') + '</div>' +
        (d.output ? '<div style="font-size:11px;color:#999;margin-top:4px;font-family:monospace;">' + d.output + '</div>' : '') +
        '<button onclick="closeRepairModal()" style="margin-top:16px;padding:8px 24px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;">关闭</button>' +
      '</div>';
    }
  }).catch(function(e) {
    el.innerHTML = '<div style="text-align:center;padding:20px;color:#e74c3c;">请求失败: ' + e.message + '<br><button onclick="closeRepairModal()" style="margin-top:12px;padding:8px 24px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;">关闭</button></div>';
  });
}

function skipRepair() {
  currentRepairIndex++;
  renderRepairModal();
}

checkLogin();
</script>
</body>
</html>'''