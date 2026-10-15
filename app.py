import os
import re
import sys
import json
import uuid
import asyncio
import threading
import urllib.request
import urllib.parse
from flask import Flask, render_template, request, jsonify, send_file
from yt_dlp import YoutubeDL

app = Flask(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_config.json")
DEFAULT_API_ID = "39329089"
DEFAULT_API_HASH = "d95a657cda16c755da9daa54f09a5c31"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

tasks = {}

def load_tg_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"api_id": DEFAULT_API_ID, "api_hash": DEFAULT_API_HASH}

def clean_url(raw):
    raw = raw.strip()
    if "facebook.com/l.php" in raw:
        parsed = urllib.parse.urlparse(raw)
        q = urllib.parse.parse_qs(parsed.query)
        if 'u' in q:
            raw = urllib.parse.unquote(q['u'][0])
    if raw.startswith("t.me/"):
        raw = "https://" + raw
    if "t.me/" in raw and "?" in raw:
        raw = raw.split("?")[0]
    return raw

def resolve_pinterest(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        v = re.findall(r'https?://[^\s"\'<>]+\.mp4', html)
        if v:
            return {"type": "video", "url": v[0].replace('\\u002F', '/'), "ext": "mp4"}
        m = re.findall(r'https?://[^\s"\'<>]+\.m3u8', html)
        if m:
            return {"type": "stream", "url": m[0].replace('\\u002F', '/'), "ext": "mp4"}
        img = re.findall(r'https://i\.pinimg\.com/originals/[^\s"\'<>]+\.(?:jpg|png|webp)', html)
        if img:
            return {"type": "image", "url": img[0].replace('\\u002F', '/'), "ext": "jpg"}
    except Exception:
        pass
    return None

def download_tg_channel_all(clean_link, task_id, is_mp3=False, max_items=None):
    cfg = load_tg_config()
    api_id = cfg.get("api_id", DEFAULT_API_ID)
    api_hash = cfg.get("api_hash", DEFAULT_API_HASH)

    m = re.search(r't\.me/([^/]+)', clean_link)
    peer = m.group(1) if m else None

    from telethon.sync import TelegramClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_user_session")
    client = TelegramClient(session_file, int(api_id), api_hash, loop=loop)
    client.connect()

    if not client.is_user_authorized():
        tasks[task_id]['status'] = 'failed'
        tasks[task_id]['error'] = 'Telegram មិនទាន់ Login នៅឡើយ!'
        client.disconnect()
        return

    limit = None if (not max_items or max_items == 'all') else int(max_items)
    messages = client.get_messages(peer, limit=limit)
    media_msgs = [m for m in messages if m.media]

    total = len(media_msgs)
    tasks[task_id]['msg'] = f"រកឃើញ {total} ឯកសារក្នុង Channel..."
    
    downloaded_files = []
    for idx, msg in enumerate(media_msgs):
        tasks[task_id]['msg'] = f"ទាញយកទី: {idx+1}/{total}"
        tasks[task_id]['percent'] = ((idx + 1) / total) * 100
        downloaded = client.download_media(msg, file=DOWNLOAD_DIR)
        if is_mp3 and downloaded and not downloaded.endswith('.mp3'):
            base, _ = os.path.splitext(downloaded)
            try:
                os.rename(downloaded, f"{base}.mp3")
                downloaded = f"{base}.mp3"
            except Exception: pass
        if downloaded:
            downloaded_files.append(os.path.basename(downloaded))

    client.disconnect()
    tasks[task_id]['files'] = downloaded_files
    tasks[task_id]['status'] = 'completed'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/download_file/<task_id>')
def download_file_api(task_id):
    task = tasks.get(task_id)
    if not task:
        return "File not found", 404
    
    file_path = task.get('final_file')
    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    
    files = task.get('files', [])
    if files:
        target = os.path.join(DOWNLOAD_DIR, files[0])
        if os.path.exists(target):
            return send_file(target, as_attachment=True)
            
    return "File not ready or expired", 404

@app.route('/api/analyze', methods=['POST'])
def analyze_api():
    url = clean_url(request.json.get('url', ''))

    if "t.me" in url:
        return jsonify({
            "status": "ok", 
            "title": "Telegram Channel / Post", 
            "duration": "Telegram Media", 
            "thumbnail": ""
        })
    
    ydl_opts = {
        'extract_flat': True,
        'skip_download': True,
        'quiet': True,
        'user_agent': HEADERS['User-Agent']
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Video Title')
            channel_url = info.get('channel_url') or info.get('uploader_url')
            channel_name = info.get('channel') or info.get('uploader')
            d = info.get('duration', 0)
            dur = f"{d//60:02d}:{d%60:02d}" if d else "Ready"
            return jsonify({
                "status": "ok", 
                "title": title, 
                "duration": dur, 
                "thumbnail": info.get('thumbnail', ''),
                "channel_url": channel_url,
                "channel_name": channel_name
            })
    except Exception:
        return jsonify({
            "status": "ok", 
            "title": "Social Media Video", 
            "duration": "Ready", 
            "thumbnail": ""
        })

@app.route('/api/download', methods=['POST'])
def download_api():
    data = request.json
    raw_url = data.get('url', '')
    url = clean_url(raw_url)
    title = data.get('title', 'Media Item')
    mode = data.get('mode', 'single')
    fmt = data.get('format', 'mp4')
    max_items = data.get('max_items', '10')

    is_mp3 = (fmt == 'mp3')
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        'status': 'downloading',
        'percent': 5,
        'speed': 'Connecting...',
        'msg': 'Starting download...',
        'title': title,
        'final_file': None,
        'files': []
    }

    def worker():
        if "t.me" in url and (mode == 'profile' or not re.search(r't\.me/.+/\d+', url)):
            try:
                download_tg_channel_all(url, task_id, is_mp3=is_mp3, max_items=max_items)
            except Exception as e:
                tasks[task_id]['status'] = 'failed'
                tasks[task_id]['error'] = str(e)
            return

        if "pinterest.com" in url or "pin.it" in url:
            pin = resolve_pinterest(url)
            if pin:
                ext = "mp3" if is_mp3 else pin['ext']
                out = os.path.join(DOWNLOAD_DIR, f"Pinterest_{task_id}.{ext}")
                try:
                    urllib.request.urlretrieve(pin['url'], out)
                    tasks[task_id]['final_file'] = out
                    tasks[task_id]['status'] = 'completed'
                    return
                except Exception:
                    pass

        target_download_url = url
        if mode == 'profile':
            tasks[task_id]['msg'] = "កំពុងស្វែងរក Channel / Profile..."
            try:
                with YoutubeDL({'extract_flat': True, 'skip_download': True, 'quiet': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    chan_url = info.get('channel_url') or info.get('uploader_url')
                    if chan_url:
                        if "youtube.com" in chan_url and not chan_url.endswith('/videos'):
                            chan_url = chan_url.rstrip('/') + '/videos'
                        target_download_url = chan_url
                        uploader_name = info.get('channel') or info.get('uploader') or "Profile"
                        tasks[task_id]['msg'] = f"រកឃើញ Profile: {uploader_name}"
            except Exception as e:
                print("Could not auto-resolve channel:", e)

        def hook(d):
            if d['status'] == 'downloading':
                p_str = d.get('_percent_str', '0%').replace('%','').strip()
                try:
                    tasks[task_id]['percent'] = float(p_str)
                    tasks[task_id]['speed'] = d.get('_speed_str', '')
                    curr_item = d.get('playlist_index', '')
                    total_item = d.get('playlist_count', '')
                    if curr_item and total_item:
                        tasks[task_id]['msg'] = f"ទាញយកវីដេអូទី: {curr_item}/{total_item} ({p_str}%)"
                    else:
                        tasks[task_id]['msg'] = f"Downloading: {p_str}%"
                except Exception:
                    pass
            elif d['status'] == 'finished':
                tasks[task_id]['percent'] = 100
                f_name = d.get('filename')
                if f_name:
                    tasks[task_id]['final_file'] = f_name
                    tasks[task_id]['files'].append(os.path.basename(f_name))

        is_batch = (mode in ['playlist', 'profile'])
        out_template = os.path.join(DOWNLOAD_DIR, '%(id)s_%(title).50s.%(ext)s')

        ydl_opts = {
            'outtmpl': out_template,
            'progress_hooks': [hook],
            'quiet': True,
            'ignoreerrors': True,
            'user_agent': HEADERS['User-Agent']
        }

        if is_mp3:
            ydl_opts['format'] = 'bestaudio/best'
        else:
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best'

        if is_batch:
            ydl_opts['yes_playlist'] = True
            if max_items and max_items != 'all':
                ydl_opts['playlist_items'] = f"1-{max_items}"
        else:
            ydl_opts['noplaylist'] = True

        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([target_download_url])
            tasks[task_id]['status'] = 'completed'
        except Exception as e:
            try:
                ydl_opts['format'] = 'best'
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([target_download_url])
                tasks[task_id]['status'] = 'completed'
            except Exception as e2:
                tasks[task_id]['status'] = 'failed'
                tasks[task_id]['error'] = str(e2)

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"status": "started", "task_id": task_id, "title": title})

@app.route('/api/progress/<task_id>')
def progress_api(task_id):
    return jsonify(tasks.get(task_id, {"status": "not_found"}))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
