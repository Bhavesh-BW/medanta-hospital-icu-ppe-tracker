"""ICU PPE Tracker — Continuous multi-camera PPE compliance & violation monitoring.

Ingests 3 live ICU RTSP camera streams, runs YOLO11-pose for person detection & face
visibility estimation + retrained best.pt PPE detection (mask, surgical_cap, gloves, surgical_gown).
Prevents false-positive mask violations when a person is facing away (back to camera).
Serves both the REST/MJPEG streaming API and the built React Dashboard UI from dist/.
"""

import collections
import hmac
import json
import os
import pathlib
import re
import sqlite3
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'rtsp_transport;tcp')
import cv2
import numpy as np
import torch
from ultralytics import YOLO

ROOT = pathlib.Path(__file__).resolve().parent
MODELS = ROOT / 'models'
DIST_DIR = ROOT / 'dist'
KEY_FILE = ROOT / '.service-key'
KEY = os.environ.get('VISION_SERVICE_KEY', KEY_FILE.read_text().strip() if KEY_FILE.exists() else '')
DB_PATH = os.environ.get('PPE_TRACKER_DB', str(ROOT / 'ppe_tracker.db'))
RTSP_BASE = os.environ.get('RTSP_BASE', 'rtsp://124.123.89.162:31554/live')
DEVICE = os.environ.get('DEVICE', 'cuda:0' if torch.cuda.is_available() else 'cpu')

MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
}

# Colors (BGR)
COLOR_COMPLIANT = (60, 200, 60)      # Green
COLOR_VIOLATION = (50, 50, 220)       # Red
COLOR_ITEM_MASK = (255, 200, 0)       # Cyan/Blue
COLOR_ITEM_CAP = (0, 220, 255)        # Yellow
COLOR_ITEM_GLOVE = (0, 140, 255)      # Orange
COLOR_ITEM_GOWN = (220, 100, 200)     # Magenta

ITEM_COLORS = {
    'mask': COLOR_ITEM_MASK,
    'surgical_cap': COLOR_ITEM_CAP,
    'gloves': COLOR_ITEM_GLOVE,
    'surgical_gown': COLOR_ITEM_GOWN,
}

DEFAULT_CAMERAS = [
    {'stream': 'icu_8_backside', 'name': 'ICU 8 Backside', 'role': 'inside', 'url': f'{RTSP_BASE}/icu_8_backside'},
    {'stream': 'icu_8_in', 'name': 'ICU 8 Inside', 'role': 'inside', 'url': f'{RTSP_BASE}/icu_8_in'},
    {'stream': 'icu_cori', 'name': 'ICU 7 8 Corridor', 'role': 'corridor', 'url': f'{RTSP_BASE}/icu_cori'},
]


def format_ist_time(epoch_ms, fmt='%I:%M:%S %p'):
    dt = time.gmtime(epoch_ms / 1000.0 + 5.5 * 3600)
    return time.strftime(fmt, dt)


def format_ist_short(epoch_ms):
    dt = time.gmtime(epoch_ms / 1000.0 + 5.5 * 3600)
    h12 = dt.tm_hour % 12 or 12
    ampm = "AM" if dt.tm_hour < 12 else "PM"
    return f"{h12:02d}:{dt.tm_min:02d} {ampm}"


def _get_time_window(time_range):
    now = time.time()
    now_sec = int(now)
    ist_offset_sec = int(5.5 * 3600)
    seconds_today_in_ist = (now_sec + ist_offset_sec) % 86400
    start_today = (now_sec - seconds_today_in_ist) * 1000
    end_today = int(now * 1000)

    if time_range == 'today':
        return start_today, end_today
    elif time_range == 'yesterday':
        start_yesterday = start_today - (86400 * 1000)
        end_yesterday = start_today - 1
        return start_yesterday, end_yesterday
    elif time_range == '3days':
        return start_today - (3 * 86400 * 1000), end_today
    elif time_range == '7days':
        return start_today - (7 * 86400 * 1000), end_today
    elif time_range == 'all':
        return 0, end_today
    else:
        return start_today, end_today


class Store:
    def __init__(self, path):
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS devices(stream TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, url TEXT);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, stream TEXT NOT NULL, camera_name TEXT NOT NULL, status TEXT NOT NULL, missing TEXT, detected TEXT, confidence REAL, ts INTEGER NOT NULL, photo BLOB, person_photo BLOB);
                CREATE INDEX IF NOT EXISTS ev_ts ON events(ts);
                CREATE INDEX IF NOT EXISTS ev_status ON events(status, ts);
                CREATE INDEX IF NOT EXISTS ev_stream ON events(stream, ts);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            cols = [c[1] for c in self.db.execute("PRAGMA table_info(devices)").fetchall()]
            if 'url' not in cols:
                self.db.execute("ALTER TABLE devices ADD COLUMN url TEXT")
            self.db.commit()

            count = self.db.execute('SELECT COUNT(*) FROM devices').fetchone()[0]
            if count == 0:
                now = int(time.time() * 1000)
                for cam in DEFAULT_CAMERAS:
                    self.db.execute('INSERT OR IGNORE INTO devices VALUES(?,?,?,1,?,?)',
                                    (cam['stream'], cam['name'], cam['role'], now, cam.get('url', '')))
                self.db.commit()

    def setting(self, key, fallback=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else fallback

    def set_setting(self, key, value):
        with self.lock:
            self.db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            (key, str(value)))
            self.db.commit()

    def rules(self):
        try:
            rules = json.loads(self.setting('rules', '{}'))
        except Exception:
            rules = {}
        return {
            'required_ppe': rules.get('required_ppe', ['mask', 'surgical_cap']),
            'person_conf': float(rules.get('person_conf', 0.35)),
            'ppe_conf': float(rules.get('ppe_conf', 0.20)),
            'cooldown_s': int(rules.get('cooldown_s', 6)),
        }

    def save_rules(self, body):
        rules = self.rules()
        if 'required_ppe' in body and isinstance(body['required_ppe'], list):
            valid = {'mask', 'surgical_cap', 'gloves', 'surgical_gown'}
            rules['required_ppe'] = [x.strip().lower() for x in body['required_ppe'] if x.strip().lower() in valid]
        if 'person_conf' in body:
            rules['person_conf'] = max(0.1, min(0.9, float(body['person_conf'])))
        if 'ppe_conf' in body:
            rules['ppe_conf'] = max(0.1, min(0.9, float(body['ppe_conf'])))
        if 'cooldown_s' in body:
            rules['cooldown_s'] = max(1, min(60, int(body['cooldown_s'])))
        self.set_setting('rules', json.dumps(rules))
        return self.rules()

    def devices(self):
        with self.lock:
            rows = self.db.execute('SELECT stream, name, role, active, created_at, url FROM devices WHERE active=1 ORDER BY created_at').fetchall()
        return [{
            'id': r[0],
            'stream': r[0],
            'name': r[1],
            'role': r[2],
            'active': bool(r[3]),
            'created_at': r[4],
            'url': r[5] or (r[0] if (r[0].startswith('rtsp://') or r[0].startswith('http://')) else f'{RTSP_BASE}/{r[0]}')
        } for r in rows]

    def save_device(self, stream, name, url=None, role='inside'):
        name = (name or '').strip()
        url = (url or '').strip()
        stream = (stream or '').strip()

        # If url was pasted into stream field
        if (stream.startswith('rtsp://') or stream.startswith('http://')) and not url:
            url = stream
            clean_id = url.rstrip('/').split('/')[-1]
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '_', clean_id.lower())
            if not clean_id or len(clean_id) < 2:
                clean_id = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
            stream = clean_id

        # If stream is empty, generate clean ID from name
        if not stream:
            clean_id = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
            stream = clean_id if clean_id else f"cam_{int(time.time())}"

        # If url is still empty, fallback to stream or RTSP_BASE
        if not url:
            url = stream if (stream.startswith('rtsp://') or stream.startswith('http://')) else f'{RTSP_BASE}/{stream}'

        with self.lock:
            self.db.execute('''
                INSERT INTO devices (stream, name, role, active, created_at, url)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(stream) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    role = excluded.role,
                    active = 1
            ''', (stream, name, role, int(time.time() * 1000), url))
            self.db.commit()
        return stream

    def add_device(self, stream, name, role='inside', url=None):
        return self.save_device(stream, name, url=url, role=role)

    def remove_device(self, stream):
        with self.lock:
            self.db.execute('UPDATE devices SET active=0 WHERE stream=?', (stream,))
            self.db.commit()

    def save_event(self, stream, camera_name, status, missing, detected, confidence, ts, photo=None, person_photo=None):
        eid = uuid.uuid4().hex
        with self.lock:
            self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (eid, stream, camera_name, status, json.dumps(missing), json.dumps(detected),
                             float(confidence), ts, photo, person_photo))
            self.db.execute('DELETE FROM events WHERE ts<?', (ts - 30 * 86400000,))
            self.db.commit()
        return eid

    def events(self, since=0, until=None, status=None, stream=None, limit=200):
        query = 'SELECT id, stream, camera_name, status, missing, detected, confidence, ts, (photo IS NOT NULL), (person_photo IS NOT NULL) FROM events WHERE ts>=?'
        params = [since]
        if until is not None:
            query += ' AND ts<=?'
            params.append(until)
        if status:
            query += ' AND status=?'
            params.append(status.lower())
        if stream:
            query += ' AND stream=?'
            params.append(stream)
        query += ' ORDER BY ts DESC LIMIT ?'
        params.append(limit)
        with self.lock:
            rows = self.db.execute(query, params).fetchall()
        return [{
            'id': r[0],
            'stream': r[1],
            'camera_name': r[2],
            'status': r[3],
            'missing': json.loads(r[4] or '[]'),
            'detected': json.loads(r[5] or '[]'),
            'confidence': round(r[6], 2),
            'ts': r[7],
            'time': format_ist_time(r[7]),
            'date': format_ist_time(r[7], '%d %b %Y'),
            'photo_url': f'/events/{r[0]}/photo' if r[8] else None,
            'person_url': f'/events/{r[0]}/person' if r[9] else None,
        } for r in rows]

    def event_photo(self, eid, kind='photo'):
        col = 'person_photo' if kind == 'person' else 'photo'
        with self.lock:
            row = self.db.execute(f'SELECT {col} FROM events WHERE id=?', (eid,)).fetchone()
        return row[0] if row and row[0] else None


class Engine:
    """Multi-stream continuous PPE inference & MJPEG live stream generator."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Condition()
        self.workers = {}
        self.stats = {'checks': 0, 'compliant': 0, 'violations': 0, 'device': DEVICE}

        pose_path = MODELS / 'yolo11n-pose.pt'
        if pose_path.exists():
            print(f'Loading YOLO11-pose model from {pose_path} on {DEVICE} ...', flush=True)
            self.person_model = YOLO(str(pose_path))
            self.has_pose = True
        else:
            print(f'Loading standard person model on {DEVICE} ...', flush=True)
            self.person_model = YOLO(str(MODELS / 'yolo11n.pt'))
            self.has_pose = False

        print(f'Loading retrained PPE model from {MODELS / "best.pt"} on {DEVICE} ...', flush=True)
        self.ppe_model = YOLO(str(MODELS / 'best.pt'))
        print(f'PPE models ready! Names: {self.ppe_model.names}', flush=True)

        self.last_event_time = {}
        threading.Thread(target=self.infer_loop, daemon=True).start()

    def sync(self):
        wanted = {d['stream']: d for d in self.store.devices() if d['active']}
        for stream, d in wanted.items():
            cam_url = d.get('url') or (stream if (stream.startswith('rtsp://') or stream.startswith('http://')) else f'{RTSP_BASE}/{stream}')
            if stream not in self.workers:
                with self.lock:
                    self.workers[stream] = {
                        'name': d['name'],
                        'role': d['role'],
                        'url': cam_url,
                        'reconnect': False,
                        'latest': None,
                        'jpeg': None,
                        'sequence': 0,
                        'last_out': 0,
                        'meta': {'fps': 0, 'scan_ms': 0, 'persons': 0, 'compliant': 0, 'violations': 0, 'error': None}
                    }
                threading.Thread(target=self.capture, args=(stream,), daemon=True).start()
            else:
                with self.lock:
                    w = self.workers[stream]
                    w['name'] = d['name']
                    w['role'] = d['role']
                    if w.get('url') != cam_url:
                        w['url'] = cam_url
                        w['reconnect'] = True
        with self.lock:
            for s in list(self.workers):
                if s not in wanted:
                    self.workers.pop(s, None)

    def capture(self, stream):
        while True:
            with self.lock:
                if stream not in self.workers:
                    return
                worker = self.workers[stream]
                rtsp_url = worker.get('url') or (stream if (stream.startswith('rtsp://') or stream.startswith('http://')) else f'{RTSP_BASE}/{stream}')
                worker['reconnect'] = False

            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 6000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 6000])
            if not cap.isOpened():
                with self.lock:
                    if stream in self.workers:
                        self.workers[stream]['meta']['error'] = 'Connecting to camera...'
                cap.release()
                time.sleep(3)
                continue

            with self.lock:
                if stream in self.workers:
                    self.workers[stream]['meta']['error'] = None

            while True:
                with self.lock:
                    if stream not in self.workers:
                        cap.release()
                        return
                    if self.workers[stream].get('reconnect'):
                        break

                ok, frame = cap.read()
                if not ok:
                    break
                with self.lock:
                    if stream not in self.workers:
                        cap.release()
                        return
                    self.workers[stream]['latest'] = (frame, time.monotonic())
            cap.release()
            time.sleep(1)

    def infer_loop(self):
        last_captured = {}
        while True:
            with self.lock:
                items = [(s, w['latest'], w) for s, w in self.workers.items()]
            items = [(s, latest, w) for s, latest, w in items if latest and last_captured.get(s) != latest[1]]
            if not items:
                time.sleep(0.02)
                continue

            rules = self.store.rules()
            required_ppe = rules['required_ppe']
            person_conf = rules['person_conf']
            ppe_conf = rules['ppe_conf']
            cooldown_s = rules['cooldown_s']

            for stream, (original, captured), worker in items:
                last_captured[stream] = captured
                t0 = time.monotonic()
                now_ms = int(time.time() * 1000)

                h, w = original.shape[:2]
                disp_w = 960
                disp_h = int(h * (disp_w / w))
                frame = cv2.resize(original, (disp_w, disp_h))

                try:
                    # Stage 1: Person detection & face orientation estimation
                    p_res = self.person_model(frame, conf=person_conf, imgsz=640, device=DEVICE, verbose=False)[0]
                    persons = []
                    if p_res.boxes is not None and len(p_res.boxes) > 0:
                        for idx, b in enumerate(p_res.boxes):
                            cls_id = int(b.cls[0].cpu().numpy()) if b.cls is not None else 0
                            if cls_id != 0:
                                continue
                            xyxy = b.xyxy[0].cpu().numpy().astype(int)
                            conf = float(b.conf[0].cpu().numpy())

                            face_visible = True
                            if self.has_pose and getattr(p_res, 'keypoints', None) is not None and idx < len(p_res.keypoints):
                                kp_conf = p_res.keypoints.conf[idx].cpu().numpy()
                                nose_conf = float(kp_conf[0])
                                l_eye_conf = float(kp_conf[1])
                                r_eye_conf = float(kp_conf[2])
                                # Face is visible if nose OR either eye is detected
                                face_visible = (nose_conf >= 0.28) or (l_eye_conf >= 0.28 or r_eye_conf >= 0.28)

                            persons.append({'bbox': xyxy, 'conf': conf, 'face_visible': face_visible})

                    # Stage 2: Detect PPE items (best.pt)
                    ppe_res = self.ppe_model(frame, conf=ppe_conf, imgsz=640, device=DEVICE, verbose=False)[0]
                    items_found = []
                    if ppe_res.boxes is not None and len(ppe_res.boxes) > 0:
                        for b in ppe_res.boxes:
                            xyxy = b.xyxy[0].cpu().numpy().astype(int)
                            cls_id = int(b.cls[0].cpu().numpy())
                            conf = float(b.conf[0].cpu().numpy())
                            cls_name = self.ppe_model.names.get(cls_id, 'item')
                            items_found.append({'bbox': xyxy, 'name': cls_name, 'conf': conf})

                    # Stage 3: Match PPE items to persons & check compliance
                    person_count = len(persons)
                    compliant_count = 0
                    violation_count = 0

                    for p in persons:
                        px1, py1, px2, py2 = p['bbox']
                        pw = px2 - px1
                        ph = py2 - py1
                        pad_x = int(pw * 0.15)
                        pad_y = int(ph * 0.15)

                        matched_items = []
                        for it in items_found:
                            ix1, iy1, ix2, iy2 = it['bbox']
                            cx = (ix1 + ix2) // 2
                            cy = (iy1 + iy2) // 2
                            if (px1 - pad_x <= cx <= px2 + pad_x) and (py1 - pad_y <= cy <= py2 + pad_y):
                                matched_items.append(it)

                        detected_names = list({it['name'] for it in matched_items})

                        missing = []
                        for req in required_ppe:
                            if req == 'mask' and not p['face_visible']:
                                continue
                            if req not in detected_names:
                                missing.append(req)

                        is_compliant = len(missing) == 0

                        if is_compliant:
                            compliant_count += 1
                            box_color = COLOR_COMPLIANT
                            badge = 'COMPLIANT [Cap, Face Away]' if not p['face_visible'] else 'COMPLIANT'
                        else:
                            violation_count += 1
                            box_color = COLOR_VIOLATION
                            badge = f"VIOLATION: NO {', '.join(missing).upper()}"

                        # Draw person bounding box
                        cv2.rectangle(frame, (px1, py1), (px2, py2), box_color, 2)

                        # Top badge
                        label = f"{badge} ({round(p['conf'] * 100)}%)"
                        t_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 2)[0]
                        cv2.rectangle(frame, (px1, max(0, py1 - 24)), (px1 + t_size[0] + 8, py1), box_color, -1)
                        cv2.putText(frame, label, (px1 + 4, max(16, py1 - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 2, cv2.LINE_AA)

                        # Draw detected item boxes inside person
                        for it in matched_items:
                            ix1, iy1, ix2, iy2 = it['bbox']
                            it_color = ITEM_COLORS.get(it['name'], (255, 255, 255))
                            cv2.rectangle(frame, (ix1, iy1), (ix2, iy2), it_color, 2)
                            cv2.putText(frame, it['name'], (ix1, max(14, iy1 - 4)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, it_color, 1, cv2.LINE_AA)

                        # Save event with spatial-temporal cooldown
                        grid_key = (stream, px1 // 80, py1 // 80)
                        last_ts = self.last_event_time.get(grid_key, 0)
                        if (time.time() - last_ts) > cooldown_s:
                            self.last_event_time[grid_key] = time.time()
                            crop_margin = 15
                            cy1 = max(0, py1 - crop_margin)
                            cy2 = min(frame.shape[0], py2 + crop_margin)
                            cx1 = max(0, px1 - crop_margin)
                            cx2 = min(frame.shape[1], px2 + crop_margin)
                            p_crop = frame[cy1:cy2, cx1:cx2]

                            ok1, p_jpg = cv2.imencode('.jpg', p_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                            ok2, f_jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])

                            self.store.save_event(
                                stream=stream,
                                camera_name=worker['name'],
                                status='compliant' if is_compliant else 'violation',
                                missing=missing,
                                detected=detected_names,
                                confidence=p['conf'],
                                ts=now_ms,
                                photo=f_jpg.tobytes() if ok2 else None,
                                person_photo=p_jpg.tobytes() if ok1 else None
                            )
                            self.stats['checks'] += 1
                            if is_compliant:
                                self.stats['compliant'] += 1
                            else:
                                self.stats['violations'] += 1

                    scan_ms = (time.monotonic() - t0) * 1000
                    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok:
                        with self.lock:
                            if stream in self.workers:
                                worker['jpeg'] = jpeg.tobytes()
                                worker['sequence'] += 1
                                now_mon = time.monotonic()
                                fps = 1 / max(now_mon - worker['last_out'], 0.001) if worker['last_out'] else 0
                                worker['last_out'] = now_mon
                                worker['meta']['fps'] = round(worker['meta']['fps'] * 0.7 + fps * 0.3, 1)
                                worker['meta']['scan_ms'] = round(scan_ms, 1)
                                worker['meta']['persons'] = person_count
                                worker['meta']['compliant'] = compliant_count
                                worker['meta']['violations'] = violation_count
                                self.lock.notify_all()
                except Exception as err:
                    with self.lock:
                        if stream in self.workers:
                            worker['meta']['error'] = f'Inference error: {str(err)[:100]}'
                    time.sleep(0.1)

    def state(self):
        with self.lock:
            workers = {s: {'jpeg': w['jpeg'] is not None, **w['meta']} for s, w in self.workers.items()}
        return {
            'running': True,
            'device': DEVICE,
            'workers': workers,
            'devices': self.store.devices(),
            'rules': self.store.rules(),
            **self.stats
        }


class Handler(BaseHTTPRequestHandler):
    store = None
    engine = None

    def log_message(self, *args):
        pass

    def send_json(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        return not KEY or hmac.compare_digest(self.headers.get('X-Local-Vision-Key', ''), KEY)

    def body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 1024 * 1024:
            raise ValueError('Request too large')
        return json.loads(self.rfile.read(size) or b'{}')

    def do_GET(self):
        p = urlparse(self.path)
        path_str = p.path
        if path_str.startswith('/api'):
            path_str = path_str[4:] or '/'
        q = parse_qs(p.query)

        try:
            # 1. API Endpoints
            if path_str == '/health':
                return self.send_json(200, {'ok': True})
            if path_str == '/state':
                return self.send_json(200, self.engine.state())
            if path_str == '/devices':
                return self.send_json(200, {'devices': self.store.devices()})
            if path_str == '/rules':
                return self.send_json(200, self.store.rules())
            if path_str == '/events':
                tr = q.get('time_range', [None])[0]
                status = q.get('status', [None])[0]
                stream = q.get('stream', [None])[0]
                limit = min(1000, max(1, int(q.get('limit', ['100'])[0])))
                if tr:
                    since, until = _get_time_window(tr)
                else:
                    hours = min(720, max(1, float(q.get('hours', ['24'])[0])))
                    since = int(time.time() * 1000 - hours * 3600000)
                    until = None
                events = self.store.events(since=since, until=until, status=status, stream=stream, limit=limit)
                return self.send_json(200, {'events': events, 'count': len(events), 'time_range': tr or '24h'})
            if path_str == '/analytics':
                tr = q.get('time_range', [None])[0]
                stream = q.get('stream', [None])[0]
                if tr:
                    since, until = _get_time_window(tr)
                else:
                    hours = min(720, max(1, float(q.get('hours', ['24'])[0])))
                    since = int(time.time() * 1000 - hours * 3600000)
                    until = None
                events = self.store.events(since=since, until=until, stream=stream, limit=10000)
                total = len(events)
                compliant = sum(1 for e in events if e['status'] == 'compliant')
                violations = sum(1 for e in events if e['status'] == 'violation')
                missing_counter = collections.Counter()
                per_camera = collections.Counter()
                for e in events:
                    per_camera[e['camera_name']] += 1
                    for m in e['missing']:
                        missing_counter[m] += 1
                return self.send_json(200, {
                    'total_checks': total,
                    'compliant_count': compliant,
                    'violation_count': violations,
                    'compliance_rate': round((compliant / max(1, total)) * 100, 1) if total > 0 else 0.0,
                    'missing_breakdown': dict(missing_counter),
                    'per_camera': dict(per_camera),
                    'time_range': tr or '24h',
                })
            match = re.match(r'^/events/([a-f0-9]+)/(photo|person)$', path_str)
            if match:
                blob = self.store.event_photo(match.group(1), match.group(2))
                if not blob:
                    return self.send_json(404, {'error': 'No image'})
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(blob)))
                self.send_header('Cache-Control', 'private, max-age=3600')
                self.end_headers()
                return self.wfile.write(blob)
            if path_str.startswith('/devices/') and path_str.endswith('/stream.mjpg'):
                stream = path_str.split('/stream.mjpg')[0].replace('/devices/', '').strip('/')
                with self.engine.lock:
                    worker = self.engine.workers.get(stream)
                if not worker:
                    return self.send_json(404, {'error': f'Stream {stream} not registered'})
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.end_headers()
                seq = -1
                try:
                    while True:
                        with self.engine.lock:
                            curr = self.engine.workers.get(stream)
                            if not curr:
                                return
                            if curr['jpeg'] is None:
                                # Standby placeholder frame
                                placeholder = np.zeros((480, 854, 3), dtype=np.uint8)
                                cv2.putText(placeholder, f"{curr.get('name', stream)} - Connecting...", (50, 240),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 160, 255), 2)
                                _, frame_bytes = cv2.imencode('.jpg', placeholder, [cv2.IMWRITE_JPEG_QUALITY, 80])
                                frame_bytes = frame_bytes.tobytes()
                                self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame_bytes)).encode() + b'\r\n\r\n' + frame_bytes + b'\r\n')
                                self.wfile.flush()
                                time.sleep(1.0)
                                continue

                            if curr['sequence'] == seq:
                                self.engine.lock.wait_for(
                                    lambda: self.engine.workers.get(stream, {}).get('sequence', seq) != seq,
                                    timeout=0.5
                                )
                                continue
                            seq, frame_bytes = curr['sequence'], curr['jpeg']
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame_bytes)).encode() + b'\r\n\r\n' + frame_bytes + b'\r\n')
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

            # 2. Static UI Files (Single-Page React App from dist/)
            if DIST_DIR.exists():
                rel = p.path.lstrip('/')
                target = (DIST_DIR / rel).resolve()
                if not target.is_relative_to(DIST_DIR.resolve()) or not target.exists() or target.is_dir():
                    target = DIST_DIR / 'index.html'
                if target.is_file():
                    ext = target.suffix.lower()
                    mime = MIME_TYPES.get(ext, 'application/octet-stream')
                    content = target.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(len(content)))
                    if ext in ('.js', '.css', '.woff2'):
                        self.send_header('Cache-Control', 'public, max-age=86400')
                    else:
                        self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    return self.wfile.write(content)

            return self.send_json(404, {'error': 'Not found'})
        except Exception as err:
            return self.send_json(400, {'error': str(err)[:200]})

    def do_POST(self):
        path_str = self.path
        if path_str.startswith('/api'):
            path_str = path_str[4:] or '/'
        try:
            data = self.body()
            if path_str in ('/devices', '/devices/save', '/devices/update'):
                stream_id = data.get('stream') or data.get('id')
                name = data.get('name', '')
                url = data.get('url', '')
                role = data.get('role', 'inside')
                saved_id = self.store.save_device(stream_id, name, url=url, role=role)
                self.engine.sync()
                return self.send_json(200, {'ok': True, 'stream': saved_id, 'devices': self.store.devices()})
            if path_str == '/devices/delete':
                stream_id = data.get('stream') or data.get('id')
                self.store.remove_device(stream_id)
                self.engine.sync()
                return self.send_json(200, {'devices': self.store.devices()})
            if path_str == '/rules':
                return self.send_json(200, self.store.save_rules(data))
            return self.send_json(404, {'error': 'Not found'})
        except Exception as err:
            return self.send_json(400, {'error': str(err)[:200]})


def main():
    store = Store(DB_PATH)
    engine = Engine(store)
    Handler.store = store
    Handler.engine = engine
    engine.sync()

    bind = os.environ.get('PPE_BIND', '0.0.0.0')
    port = int(os.environ.get('PPE_PORT', '8186'))
    print(f'ICU PPE Tracker server ready on http://{bind}:{port}', flush=True)
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
