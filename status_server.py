#!/usr/bin/env python3
import json
import mimetypes
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

STATE_FILE = Path("/opt/srt-bot/data/state.json")
AVATAR_DIR = Path("/opt/srt-bot/avatars")      # сюда бот складывает аватарки user_id.jpg
FAVICON_FILE = Path("/opt/srt-bot/static/favicon.ico")

HOST = "127.0.0.1"
PORT = 8080


def get_bubbles_from_state(state_data: dict, server_id: str) -> list[dict]:
    """
    Извлекает пузыри из состояния с указанием server_id.
    bubble = { user_id:int, kind:'in'|'out', server_id:str }
    """
    bubbles: list[dict] = []
    incoming_streams = state_data.get("incoming_streams", [])

    for s in incoming_streams:
        user_id = s.get("user_id")
        if s.get("status") == "running" and user_id:
            bubbles.append({
                "user_id": int(user_id),
                "kind": "in",
                "server_id": s.get("server_id", server_id)
            })

        for o in s.get("outgoing_streams", []):
            ouid = o.get("user_id") or user_id
            if o.get("status") == "running" and ouid:
                bubbles.append({
                    "user_id": int(ouid),
                    "kind": "out",
                    "server_id": o.get("server_id", s.get("server_id", server_id))
                })

    return bubbles


def get_state_from_msk() -> dict:
    """Получает состояние с МСК сервера через SSH"""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=5',
             f'root@{MSK_SERVER_IP}', f'cat {MSK_STATE_FILE}'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def get_bubbles() -> list[dict]:
    """
    Возвращает список активных 'пузырей' с обоих серверов.
    bubble = { user_id:int, kind:'in'|'out', server_id:'spb'|'msk' }
    """
    bubbles: list[dict] = []
    
    # СПБ сервер (локально)
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            bubbles.extend(get_bubbles_from_state(raw, "spb"))
        except Exception:
            pass
    
    # МСК сервер (через SSH)
    msk_state = get_state_from_msk()
    if msk_state:
        bubbles.extend(get_bubbles_from_state(msk_state, "msk"))
    
    return bubbles


FAQ_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>FAQ — ff264.org</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="/favicon.ico">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
    
    * { margin: 0; padding: 0; box-sizing: border-box; }
    
    :root {
      --green: #00ff41;
      --green-dim: #00aa2a;
      --red: #ff3333;
      --blue: #00bfff;
      --purple: #bf5fff;
      --orange: #ff9500;
      --bg: #0a0a0a;
      --line: #1a1a1a;
    }
    
    body {
      font-family: 'JetBrains Mono', monospace;
      background: var(--bg);
      color: var(--green);
      min-height: 100vh;
      line-height: 1.6;
    }
    
    .scanlines {
      position: fixed;
      inset: 0;
      background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.3) 2px, rgba(0,0,0,0.3) 4px);
      pointer-events: none;
      z-index: 1000;
    }
    
    .crt {
      position: fixed;
      inset: 0;
      background: radial-gradient(ellipse at center, transparent 0%, rgba(0,0,0,0.4) 100%);
      pointer-events: none;
      z-index: 999;
    }
    
    .terminal {
      max-width: 900px;
      margin: 0 auto;
      padding: 20px;
    }
    
    .header {
      border: 1px solid var(--green-dim);
      padding: 15px 20px;
      margin-bottom: 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    
    .logo {
      font-size: 1.5rem;
      font-weight: 700;
      text-decoration: none;
      color: var(--green);
      text-shadow: 0 0 10px var(--green);
    }
    
    .logo span { color: var(--green-dim); }
    
    .nav { display: flex; gap: 20px; }
    
    .nav a {
      color: var(--green-dim);
      text-decoration: none;
      padding: 5px 15px;
      border: 1px solid transparent;
      transition: all 0.2s;
    }
    
    .nav a:hover, .nav a.active {
      color: var(--green);
      border-color: var(--green-dim);
      text-shadow: 0 0 5px var(--green);
    }
    
    .section {
      border: 1px solid var(--green-dim);
      margin-bottom: 20px;
    }
    
    .section-header {
      background: var(--green-dim);
      color: var(--bg);
      padding: 8px 15px;
      font-weight: 500;
    }
    
    .section-content {
      padding: 20px;
    }
    
    .section-content p {
      margin-bottom: 15px;
    }
    
    .highlight {
      border-left: 3px solid var(--green);
      padding: 10px 15px;
      background: rgba(0,255,65,0.05);
      margin: 15px 0;
    }
    
    ul {
      list-style: none;
      margin: 15px 0;
    }
    
    ul li {
      padding: 8px 0;
      padding-left: 20px;
      position: relative;
    }
    
    ul li::before {
      content: '>';
      position: absolute;
      left: 0;
      color: var(--green-dim);
    }
    
    .servers-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 15px;
      margin-top: 15px;
    }
    
    .server-card {
      border: 1px solid var(--line);
      padding: 15px;
    }
    
    .server-card.msk { border-left: 3px solid var(--red); }
    .server-card.spb { border-left: 3px solid var(--orange); }
    
    .server-name {
      font-weight: 700;
      margin-bottom: 5px;
    }
    
    .server-domain {
      color: var(--blue);
      font-size: 0.9rem;
    }
    
    code {
      background: rgba(0,255,65,0.1);
      padding: 3px 8px;
      border: 1px solid var(--line);
      display: inline-block;
      margin: 5px 0;
    }
    
    .code-block {
      background: rgba(0,0,0,0.5);
      border: 1px solid var(--line);
      padding: 15px;
      margin: 15px 0;
      overflow-x: auto;
    }
    
    .faq-item {
      border-bottom: 1px solid var(--line);
      padding: 15px 0;
    }
    
    .faq-item:last-child { border-bottom: none; }
    
    .faq-q {
      color: var(--green);
      margin-bottom: 8px;
    }
    
    .faq-q::before {
      content: '? ';
      color: var(--green-dim);
    }
    
    .faq-a {
      color: var(--green-dim);
      padding-left: 20px;
    }
    
    .btn {
      display: inline-block;
      padding: 10px 25px;
      border: 1px solid var(--green-dim);
      color: var(--green);
      text-decoration: none;
      margin: 5px;
      transition: all 0.2s;
    }
    
    .btn:hover {
      background: var(--green-dim);
      color: var(--bg);
      text-shadow: none;
    }
    
    .footer {
      text-align: center;
      padding: 30px;
      color: var(--green-dim);
      font-size: 0.85rem;
    }
    
    @media (max-width: 600px) {
      .header { flex-direction: column; gap: 15px; }
    }
  </style>
</head>
<body>
  <div class="scanlines"></div>
  <div class="crt"></div>
  
  <div class="terminal">
    <div class="header">
      <a href="/" class="logo">ff264<span>.org</span></a>
      <nav class="nav">
        <a href="/">HOME</a>
        <a href="/faq" class="active">FAQ</a>
        <a href="https://t.me/ff264_bot">BOT</a>
      </nav>
    </div>
    
    <div class="section">
      <div class="section-header">// WHAT IS FF264?</div>
      <div class="section-content">
        <p><strong>ff264.org</strong> — сервис для создания и управления SRT-потоками через Telegram-бота.</p>
        <div class="highlight">
          <strong>SRT (Secure Reliable Transport)</strong> — протокол для передачи видео с низкой задержкой и защитой от потерь пакетов.
        </div>
      </div>
    </div>
    
    <div class="section">
      <div class="section-header">// FEATURES</div>
      <div class="section-content">
        <ul>
          <li><strong>Входящие потоки</strong> — принимайте SRT на выделенный порт</li>
          <li><strong>Множественные исходящие</strong> — раздавайте один поток нескольким получателям</li>
          <li><strong>Два сервера</strong> — Москва и Санкт-Петербург</li>
          <li><strong>Защита паролем</strong> — passphrase для каждого потока</li>
        </ul>
      </div>
    </div>
    
    <div class="section">
      <div class="section-header">// QUICK START</div>
      <div class="section-content">
        <p>1. Откройте Telegram-бота: <code>/start</code></p>
        <p>2. Выберите сервер (Москва / Петербург)</p>
        <p>3. Создайте входящий поток → получите SRT URL</p>
        <p>4. Добавьте исходящие потоки для раздачи</p>
        <div style="margin-top: 20px;">
          <a href="https://t.me/ff264_bot" class="btn">@ff264_bot</a>
          <a href="https://www.tbank.ru/cf/84HuS9nZ0Co" class="btn" target="_blank">Спасибо</a>
        </div>
      </div>
    </div>
    
    <div class="section">
      <div class="section-header">// SERVERS</div>
      <div class="section-content">
        <div class="servers-grid">
          <div class="server-card msk">
            <div class="server-name">MOSCOW</div>
            <div class="server-domain">msk.ff264.org</div>
          </div>
          <div class="server-card spb">
            <div class="server-name">ST. PETERSBURG</div>
            <div class="server-domain">ff264.org</div>
          </div>
        </div>
      </div>
    </div>
    
    <div class="section">
      <div class="section-header">// USAGE EXAMPLE</div>
      <div class="section-content">
        <p><strong>Отправка потока (OBS, FFmpeg):</strong></p>
        <div class="code-block">srt://msk.ff264.org:4000?passphrase=mypass</div>
        <p><strong>Получение потока (VLC, FFplay):</strong></p>
        <div class="code-block">srt://msk.ff264.org:6000?passphrase=mypass</div>
      </div>
    </div>
    
    <div class="section">
      <div class="section-header">// FAQ</div>
      <div class="section-content">
        <div class="faq-item">
          <div class="faq-q">Какая задержка у SRT?</div>
          <div class="faq-a">Типичная задержка 200-500ms в зависимости от качества соединения.</div>
        </div>
        <div class="faq-item">
          <div class="faq-q">Обязательно ли использовать пароль?</div>
          <div class="faq-a">Нет, passphrase опционален, но рекомендуется для защиты.</div>
        </div>
        <div class="faq-item">
          <div class="faq-q">Какой битрейт поддерживается?</div>
          <div class="faq-a">До 50 Mbps на поток, рекомендуется 5-15 Mbps.</div>
        </div>
      </div>
    </div>
    
    <div class="footer">
      ff264.org © 2026
    </div>
  </div>
</body>
</html>
"""

INSTALL_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Техническая документация - ff264.org</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
            background: #0d1117;
            color: #c9d1d9;
            min-height: 100vh;
            line-height: 1.6;
        }
        
        .container {
            max-width: 1000px;
            margin: 0 auto;
            padding: 40px 20px;
        }
        
        header {
            text-align: center;
            margin-bottom: 50px;
            padding-bottom: 30px;
            border-bottom: 1px solid #30363d;
        }
        
        h1 {
            font-size: 2.5rem;
            color: #58a6ff;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #8b949e;
            font-size: 1rem;
        }
        
        .section {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            margin-bottom: 25px;
            overflow: hidden;
        }
        
        .section-header {
            background: #21262d;
            padding: 15px 20px;
            border-bottom: 1px solid #30363d;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .section-header h2 {
            color: #f0f6fc;
            font-size: 1.1rem;
            font-weight: 600;
        }
        
        .section-header .icon {
            font-size: 1.2rem;
        }
        
        .section-content {
            padding: 20px;
        }
        
        .section-content p {
            margin-bottom: 15px;
            color: #8b949e;
        }
        
        pre {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
            margin: 15px 0;
        }
        
        code {
            color: #79c0ff;
            font-size: 0.9em;
        }
        
        pre code {
            color: #c9d1d9;
        }
        
        .code-comment {
            color: #8b949e;
        }
        
        .code-string {
            color: #a5d6ff;
        }
        
        .code-keyword {
            color: #ff7b72;
        }
        
        .code-function {
            color: #d2a8ff;
        }
        
        .architecture {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        
        .arch-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 20px;
        }
        
        .arch-box h3 {
            color: #58a6ff;
            margin-bottom: 15px;
            font-size: 1rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .arch-box ul {
            list-style: none;
        }
        
        .arch-box li {
            padding: 8px 0;
            border-bottom: 1px solid #21262d;
            color: #8b949e;
            font-size: 0.9rem;
        }
        
        .arch-box li:last-child {
            border-bottom: none;
        }
        
        .arch-box li strong {
            color: #c9d1d9;
        }
        
        .flow-diagram {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 30px;
            text-align: center;
            margin: 20px 0;
            font-size: 0.9rem;
        }
        
        .flow-diagram .flow-row {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 15px;
            margin: 10px 0;
            flex-wrap: wrap;
        }
        
        .flow-box {
            background: #21262d;
            border: 1px solid #30363d;
            padding: 12px 20px;
            border-radius: 6px;
            color: #c9d1d9;
        }
        
        .flow-box.primary {
            border-color: #58a6ff;
            color: #58a6ff;
        }
        
        .flow-box.success {
            border-color: #3fb950;
            color: #3fb950;
        }
        
        .flow-box.warning {
            border-color: #d29922;
            color: #d29922;
        }
        
        .flow-arrow {
            color: #8b949e;
            font-size: 1.2rem;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }
        
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #30363d;
        }
        
        th {
            background: #21262d;
            color: #f0f6fc;
            font-weight: 600;
        }
        
        td {
            color: #8b949e;
        }
        
        td code {
            background: #21262d;
            padding: 2px 6px;
            border-radius: 3px;
        }
        
        .nav-links {
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 40px;
            flex-wrap: wrap;
        }
        
        .nav-links a {
            color: #58a6ff;
            text-decoration: none;
            padding: 10px 20px;
            border: 1px solid #30363d;
            border-radius: 6px;
            transition: all 0.2s;
        }
        
        .nav-links a:hover {
            background: #21262d;
            border-color: #58a6ff;
        }
        
        footer {
            text-align: center;
            margin-top: 60px;
            padding-top: 30px;
            border-top: 1px solid #30363d;
            color: #8b949e;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-right: 5px;
        }
        
        .badge-python { background: #3572A5; color: #fff; }
        .badge-nginx { background: #009639; color: #fff; }
        .badge-ffmpeg { background: #007808; color: #fff; }
        .badge-telegram { background: #0088cc; color: #fff; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>// Техническая документация</h1>
            <p class="subtitle">Архитектура и реализация ff264.org</p>
            <div style="margin-top: 15px;">
                <span class="badge badge-python">Python 3.11</span>
                <span class="badge badge-nginx">Nginx</span>
                <span class="badge badge-ffmpeg">FFmpeg</span>
                <span class="badge badge-telegram">Aiogram 3</span>
            </div>
        </header>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">🏗️</span>
                <h2>Архитектура системы</h2>
            </div>
            <div class="section-content">
                <div class="flow-diagram">
                    <div class="flow-row">
                        <div class="flow-box">OBS / Encoder</div>
                        <span class="flow-arrow">→</span>
                        <div class="flow-box primary">SRT Input (ffmpeg)</div>
                        <span class="flow-arrow">→</span>
                        <div class="flow-box warning">UDP Multicast</div>
                        <span class="flow-arrow">→</span>
                        <div class="flow-box success">SRT Output (ffmpeg)</div>
                        <span class="flow-arrow">→</span>
                        <div class="flow-box">VLC / Decoder</div>
                    </div>
                </div>
                
                <div class="architecture">
                    <div class="arch-box">
                        <h3>📱 Telegram Bot</h3>
                        <ul>
                            <li><strong>Framework:</strong> Aiogram 3.x</li>
                            <li><strong>FSM:</strong> Finite State Machine для диалогов</li>
                            <li><strong>Storage:</strong> JSON файл (state.json)</li>
                            <li><strong>Service:</strong> systemd srt-bot.service</li>
                        </ul>
                    </div>
                    <div class="arch-box">
                        <h3>🎬 FFmpeg Streams</h3>
                        <ul>
                            <li><strong>Input:</strong> SRT listener mode</li>
                            <li><strong>Internal:</strong> UDP multicast 239.0.0.1</li>
                            <li><strong>Output:</strong> SRT listener mode</li>
                            <li><strong>Codec:</strong> Copy (без перекодирования)</li>
                        </ul>
                    </div>
                    <div class="arch-box">
                        <h3>🌐 Web Server</h3>
                        <ul>
                            <li><strong>Reverse Proxy:</strong> Nginx + SSL</li>
                            <li><strong>Status Page:</strong> Python HTTP server</li>
                            <li><strong>API:</strong> /api/bubbles (JSON)</li>
                            <li><strong>Cert:</strong> Let's Encrypt</li>
                        </ul>
                    </div>
                    <div class="arch-box">
                        <h3>🖥️ Серверы</h3>
                        <ul>
                            <li><strong>SPB:</strong> ff264.org (основной)</li>
                            <li><strong>MSK:</strong> msk.ff264.org</li>
                            <li><strong>SSH:</strong> Ключевая авторизация</li>
                            <li><strong>Sync:</strong> Централизованный бот</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">📁</span>
                <h2>Структура проекта</h2>
            </div>
            <div class="section-content">
<pre><code>/opt/srt-bot/
├── app/
│   ├── __init__.py
│   ├── main.py              <span class="code-comment"># Entry point</span>
│   ├── config.py            <span class="code-comment"># Settings & env vars</span>
│   ├── bot/
│   │   ├── handlers.py      <span class="code-comment"># Telegram handlers</span>
│   │   ├── keyboards.py     <span class="code-comment"># Inline & reply keyboards</span>
│   │   └── messages.py      <span class="code-comment"># Text constants</span>
│   └── core/
│       ├── models.py        <span class="code-comment"># IncomingStream, OutgoingStream</span>
│       ├── storage.py       <span class="code-comment"># JSON state management</span>
│       ├── ffmpeg_manager.py<span class="code-comment"># Stream start/stop</span>
│       ├── server_config.py <span class="code-comment"># Multi-server config</span>
│       └── server_manager.py<span class="code-comment"># Remote SSH execution</span>
├── data/
│   └── state.json           <span class="code-comment"># Current streams state</span>
├── logs/                    <span class="code-comment"># FFmpeg logs</span>
├── avatars/                 <span class="code-comment"># User avatars cache</span>
├── status_server.py         <span class="code-comment"># Web status page</span>
├── cleanup_ffmpeg.py        <span class="code-comment"># Orphan process cleaner</span>
└── .env                     <span class="code-comment"># Environment variables</span></code></pre>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">🔧</span>
                <h2>FFmpeg команды</h2>
            </div>
            <div class="section-content">
                <p><strong>Входящий поток (SRT → UDP):</strong></p>
<pre><code><span class="code-function">ffmpeg</span> -loglevel warning -nostats \
  -i <span class="code-string">"srt://0.0.0.0:5000?mode=listener&passphrase=xxx&latency=200"</span> \
  -c copy -f mpegts <span class="code-string">"udp://239.0.0.1:6000?ttl=1"</span></code></pre>
                
                <p><strong>Исходящий поток (UDP → SRT):</strong></p>
<pre><code><span class="code-function">ffmpeg</span> -loglevel warning -nostats \
  -i <span class="code-string">"udp://239.0.0.1:6000?reuse=1"</span> \
  -c copy -f mpegts <span class="code-string">"srt://0.0.0.0:7000?mode=listener&passphrase=xxx&latency=200"</span></code></pre>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">🔌</span>
                <h2>Порты и диапазоны</h2>
            </div>
            <div class="section-content">
                <table>
                    <tr>
                        <th>Сервер</th>
                        <th>Домен</th>
                        <th>Входящие порты</th>
                        <th>Исходящие порты</th>
                        <th>Internal UDP</th>
                    </tr>
                    <tr>
                        <td>Санкт-Петербург</td>
                        <td><code>ff264.org</code></td>
                        <td><code>5000-5020</code></td>
                        <td><code>7000-7100</code></td>
                        <td><code>6000+</code></td>
                    </tr>
                    <tr>
                        <td>Москва</td>
                        <td><code>msk.ff264.org</code></td>
                        <td><code>4000-4020</code></td>
                        <td><code>6000-6100</code></td>
                        <td><code>5000+</code></td>
                    </tr>
                </table>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">⚙️</span>
                <h2>Systemd сервисы</h2>
            </div>
            <div class="section-content">
<pre><code><span class="code-comment"># /etc/systemd/system/srt-bot.service</span>
[Unit]
Description=SRT Telegram Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/srt-bot
ExecStart=/opt/srt-bot/venv/bin/python -m app.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target</code></pre>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">🔐</span>
                <h2>Переменные окружения (.env)</h2>
            </div>
            <div class="section-content">
<pre><code>BOT_TOKEN=<span class="code-string">"your_telegram_bot_token"</span>
ADMIN_USER_ID=<span class="code-string">"your_telegram_id"</span>
SERVER_PUBLIC_IP=<span class="code-string">"ff264.org"</span>
MAX_INCOMING_STREAMS=<span class="code-string">"20"</span>

<span class="code-comment"># Multi-server config (JSON)</span>
SERVERS_CONFIG=<span class="code-string">'[
  {"id":"spb","name":"Санкт-Петербург","domain":"ff264.org",
   "ip":"83.222.17.46","is_local":true,
   "incoming_port_start":5000,"incoming_port_end":5020,
   "outgoing_port_start":7000,"outgoing_port_end":7100},
  {"id":"msk","name":"Москва","domain":"msk.ff264.org",
   "ip":"194.156.117.119","is_local":false,
   "ssh_key_path":"/root/.ssh/id_rsa",
   "incoming_port_start":4000,"incoming_port_end":4020,
   "outgoing_port_start":6000,"outgoing_port_end":6100}
]'</span></code></pre>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">🕐</span>
                <h2>Cron задачи</h2>
            </div>
            <div class="section-content">
<pre><code><span class="code-comment"># Очистка orphan ffmpeg процессов (каждые 5 минут)</span>
*/5 * * * * cd /opt/srt-bot && ./venv/bin/python cleanup_ffmpeg.py

<span class="code-comment"># Обновление аватаров пользователей (каждые 10 минут)</span>
*/10 * * * * cd /opt/srt-bot && ./venv/bin/python fetch_avatars.py

<span class="code-comment"># Напоминания о долгих туннелях (каждые 6 часов)</span>
0 */6 * * * cd /opt/srt-bot && ./venv/bin/python check_tunnel_reminders.py | ./venv/bin/python send_tunnel_reminders.py</code></pre>
            </div>
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="icon">📊</span>
                <h2>API Endpoints</h2>
            </div>
            <div class="section-content">
                <table>
                    <tr>
                        <th>Endpoint</th>
                        <th>Method</th>
                        <th>Description</th>
                    </tr>
                    <tr>
                        <td><code>/</code></td>
                        <td>GET</td>
                        <td>Status page с визуализацией потоков</td>
                    </tr>
                    <tr>
                        <td><code>/api/bubbles</code></td>
                        <td>GET</td>
                        <td>JSON список активных потоков</td>
                    </tr>
                    <tr>
                        <td><code>/faq</code></td>
                        <td>GET</td>
                        <td>Документация для пользователей</td>
                    </tr>
                    <tr>
                        <td><code>/install</code></td>
                        <td>GET</td>
                        <td>Техническая документация</td>
                    </tr>
                    <tr>
                        <td><code>/avatars/{id}.jpg</code></td>
                        <td>GET</td>
                        <td>Аватары пользователей (nginx)</td>
                    </tr>
                </table>
            </div>
        </div>
        
        <div class="nav-links">
            <a href="/">Главная</a>
            <a href="/faq">FAQ</a>
            <a href="https://t.me/ff264_bot">Telegram Bot</a>
        </div>
        
        <footer>
            <p>ff264.org © 2026 | Built with Python, FFmpeg & ❤️</p>
        </footer>
    </div>
</body>
</html>
"""


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>ff264.org — Terminal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="/favicon.ico">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
    
    * { margin: 0; padding: 0; box-sizing: border-box; }
    
    :root {
      --green: #00ff41;
      --green-dim: #00aa2a;
      --red: #ff3333;
      --blue: #00bfff;
      --purple: #bf5fff;
      --orange: #ff9500;
      --bg: #0a0a0a;
      --line: #1a1a1a;
    }
    
    body {
      font-family: 'JetBrains Mono', monospace;
      background: var(--bg);
      color: var(--green);
      min-height: 100vh;
      overflow-x: hidden;
    }
    
    .scanlines {
      position: fixed;
      inset: 0;
      background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0,0,0,0.3) 2px,
        rgba(0,0,0,0.3) 4px
      );
      pointer-events: none;
      z-index: 1000;
    }
    
    .crt {
      position: fixed;
      inset: 0;
      background: radial-gradient(ellipse at center, transparent 0%, rgba(0,0,0,0.4) 100%);
      pointer-events: none;
      z-index: 999;
    }
    
    .terminal {
      max-width: 1000px;
      margin: 0 auto;
      padding: 20px;
      position: relative;
    }
    
    .header {
      border: 1px solid var(--green-dim);
      padding: 15px 20px;
      margin-bottom: 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    
    .logo {
      font-size: 1.5rem;
      font-weight: 700;
      text-shadow: 0 0 10px var(--green);
    }
    
    .logo span { color: var(--green-dim); }
    
    .nav { display: flex; gap: 20px; }
    
    .nav a {
      color: var(--green-dim);
      text-decoration: none;
      padding: 5px 15px;
      border: 1px solid transparent;
      transition: all 0.2s;
    }
    
    .nav a:hover {
      color: var(--green);
      border-color: var(--green-dim);
      text-shadow: 0 0 5px var(--green);
    }
    
    .status-box {
      border: 1px solid var(--green-dim);
      margin-bottom: 20px;
    }
    
    .status-header {
      background: var(--green-dim);
      color: var(--bg);
      padding: 8px 15px;
      font-weight: 500;
      display: flex;
      justify-content: space-between;
    }
    
    .status-content {
      padding: 20px;
    }
    
    .stats-row {
      display: flex;
      gap: 40px;
      margin-bottom: 20px;
      font-size: 0.9rem;
    }
    
    .stat-item {
      display: flex;
      gap: 10px;
    }
    
    .stat-label { color: var(--green-dim); }
    .stat-value { color: var(--green); text-shadow: 0 0 5px var(--green); }
    
    .streams-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 15px;
    }
    
    .stream-card {
      border: 1px solid var(--line);
      padding: 15px;
      background: rgba(0,255,65,0.02);
      transition: all 0.2s;
    }
    
    .stream-card:hover {
      border-color: var(--green-dim);
      background: rgba(0,255,65,0.05);
    }
    
    .stream-card.msk-in { border-left: 3px solid var(--red); }
    .stream-card.msk-out { border-left: 3px solid var(--blue); }
    .stream-card.spb-in { border-left: 3px solid var(--orange); }
    .stream-card.spb-out { border-left: 3px solid var(--purple); }
    
    .stream-header {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }
    
    .stream-avatar {
      width: 32px;
      height: 32px;
      border-radius: 4px;
      background: var(--line);
    }
    
    .stream-type {
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 2px;
      font-weight: 500;
    }
    
    .stream-type.in { background: rgba(0,255,65,0.2); color: var(--green); }
    .stream-type.out { background: rgba(0,191,255,0.2); color: var(--blue); }
    
    .stream-server {
      font-size: 0.8rem;
      color: var(--green-dim);
    }
    
    .stream-server.msk { color: var(--red); }
    .stream-server.spb { color: var(--orange); }
    
    .blink {
      animation: blink 1s step-end infinite;
    }
    
    @keyframes blink {
      50% { opacity: 0; }
    }
    
    .empty-state {
      text-align: center;
      padding: 60px 20px;
      color: var(--green-dim);
    }
    
    .empty-state pre {
      font-size: 0.8rem;
      margin-top: 20px;
      color: var(--line);
    }
    
    .cursor::after {
      content: '█';
      animation: blink 1s step-end infinite;
    }
    
    .footer {
      text-align: center;
      padding: 30px;
      color: var(--green-dim);
      font-size: 0.8rem;
    }
    
    @media (max-width: 600px) {
      .header { flex-direction: column; gap: 15px; }
      .stats-row { flex-direction: column; gap: 10px; }
    }
  </style>
</head>
<body>
  <div class="scanlines"></div>
  <div class="crt"></div>
  
  <div class="terminal">
    <div class="header">
      <div class="logo">ff264<span>.org</span></div>
      <nav class="nav">
        
        <a href="/faq">FAQ</a>
        <a href="https://t.me/ff264_bot">BOT</a>
      </nav>
    </div>
    
    <div class="status-box">
      <div class="status-header">
        <span>SYSTEM STATUS</span>
        <span id="time"></span>
      </div>
      <div class="status-content">
        <div class="stats-row">
          <div class="stat-item">
            <span class="stat-label">STREAMS:</span>
            <span class="stat-value" id="total">0</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">INPUT:</span>
            <span class="stat-value" id="input">0</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">OUTPUT:</span>
            <span class="stat-value" id="output">0</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">STATUS:</span>
            <span class="stat-value">ONLINE<span class="blink">_</span></span>
          </div>
        </div>
        
        <div class="streams-grid" id="grid"></div>
        
        <div class="empty-state" id="empty">
          <div>> NO ACTIVE STREAMS<span class="cursor"></span></div>
          <pre>
   _____ _____ ____  ____  _  _   
  |  ___|  ___|___ \/ ___|| || |  
  | |_  | |_    __) / /___ | || |_ 
  |  _| |  _|  / __/\  __ \|__   _|
  |_|   |_|   |_____|\_____|  |_|  
          </pre>
        </div>
      </div>
    </div>
    
    <div class="footer">
      [ UPTIME: <span id="uptime">00:00:00</span> ] — ff264.org © 2026
    </div>
  </div>

  <script>
    const grid = document.getElementById('grid');
    const empty = document.getElementById('empty');
    let startTime = Date.now();
    
    function updateTime() {
      document.getElementById('time').textContent = new Date().toLocaleTimeString('ru-RU');
      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      const h = String(Math.floor(elapsed / 3600)).padStart(2, '0');
      const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
      const s = String(elapsed % 60).padStart(2, '0');
      document.getElementById('uptime').textContent = `${h}:${m}:${s}`;
    }
    setInterval(updateTime, 1000);
    updateTime();
    
    async function fetchStreams() {
      try {
        const res = await fetch('/api/bubbles');
        const data = await res.json();
        render(data);
      } catch(e) {
        console.error(e);
      }
    }
    
    function render(data) {
      document.getElementById('total').textContent = data.length;
      document.getElementById('input').textContent = data.filter(s => s.kind === 'in').length;
      document.getElementById('output').textContent = data.filter(s => s.kind === 'out').length;
      
      if (data.length === 0) {
        empty.style.display = 'block';
        grid.innerHTML = '';
        return;
      }
      
      empty.style.display = 'none';
      grid.innerHTML = data.map(s => {
        const sid = s.server_id || 'spb';
        const kind = s.kind || 'in';
        return `
          <div class="stream-card ${sid}-${kind}">
            <div class="stream-header">
              <img class="stream-avatar" src="/avatars/${s.user_id}.jpg" 
                   onerror="this.style.background='#222'">
              <span class="stream-type ${kind}">${kind.toUpperCase()}</span>
            </div>
            <div class="stream-server ${sid}">${sid.toUpperCase()}</div>
          </div>
        `;
      }).join('');
    }
    
    fetchStreams();
    setInterval(fetchStreams, 3000);
  </script>
</body>
</html>
"""


class StatusHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, ctype: str, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        # Нужен для favicon-check и curl -I
        self.do_GET(head_only=True)

    def do_GET(self, head_only: bool = False):
        path = urlparse(self.path).path

        # API bubbles
        if path == "/api/bubbles":
            data = json.dumps(get_bubbles(), ensure_ascii=False).encode("utf-8")
            if head_only:
                self._send(200, "application/json; charset=utf-8", b"")
            else:
                self._send(200, "application/json; charset=utf-8", data)
            return

        # favicon
        if path == "/favicon.ico":
            if FAVICON_FILE.exists():
                blob = FAVICON_FILE.read_bytes()
                if head_only:
                    self._send(200, "image/x-icon", b"")
                else:
                    self._send(200, "image/x-icon", blob)
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        if path == "/faq":
            body = FAQ_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        if path == "/install":
            body = INSTALL_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        if False:  # removed /2
            body = V2_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return



        # avatars
        if path.startswith("/avatars/"):
            name = path.split("/", 2)[2]
            file_path = (AVATAR_DIR / name).resolve()
            if not str(file_path).startswith(str(AVATAR_DIR.resolve())):
                self._send(403, "text/plain; charset=utf-8", b"forbidden")
                return

            if file_path.exists() and file_path.is_file():
                ctype = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
                blob = file_path.read_bytes()
                if head_only:
                    self._send(200, ctype, b"")
                else:
                    self._send(200, ctype, blob)
            else:
                # fallback: пустая картинка, чтобы не ломать фон
                self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        # main page
        if path in ("/", "/index.html"):
            data = HTML_PAGE.encode("utf-8")
            if head_only:
                self._send(200, "text/html; charset=utf-8", b"")
            else:
                self._send(200, "text/html; charset=utf-8", data)
            return

        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def log_message(self, *args):
        return


def run_server(host: str = HOST, port: int = PORT):
    os.makedirs(AVATAR_DIR, exist_ok=True)
    httpd = HTTPServer((host, port), StatusHandler)
    print(f"Status server listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
