"""LAN remote for the existing Matter lamp. Python standard library only."""
import atexit
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from chip_session import ChipSession

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'controller-state'
WEB = Path(__file__).resolve().parent
PORT = 8765
LOCK = threading.Lock()
BLE_LOCK = threading.Lock()
last_garland_command = None
CACHE = {}
UPDATED = 0.0
FIELDS = ('OnOff', 'CurrentLevel', 'CurrentHue', 'CurrentSaturation', 'ColorMode',
          'ColorTemperatureMireds', 'CurrentX', 'CurrentY', 'ColorLoopActive', 'ColorLoopTime',
          'ColorLoopDirection', 'StartUpOnOff', 'OnTime')
PATTERN = re.compile(r'\[TOO\]\s+(' + '|'.join(FIELDS) + r'):\s*(TRUE|FALSE|null|\d+)\b')
CHIP = ROOT / 'bin' / 'chip-tool'


SESSION = ChipSession(CHIP, STATE)
atexit.register(SESSION.close)


def matter(*args, retry_busy=False):
    attempts = 2 if retry_busy else 1
    for attempt in range(attempts):
        try:
            reply = SESSION.run(args)
        except (ConnectionError, TimeoutError) as exc:
            if attempt + 1 < attempts:
                continue
            raise RuntimeError('Соединение прервалось или лампа не ответила вовремя.') from exc
        failure = rb'\[TOO\]\s+(?:Error:|Response Failure:|Run command failure:|InitArgs:|Unknown (?:cluster|command|attribute))'
        failed = re.search(failure, reply)
        codes = re.findall(rb'(?:CHIP|OS) Error (0x[0-9A-Fa-f]+)', reply)
        code = codes[-1] if codes else None
        if not failed:
            if args[1] == 'read-by-id':
                report = reply.decode(errors='replace')
                if PATTERN.search(report):
                    return report
            elif args[1] == 'write':
                # The SDK prints no success message for a single attribute write.
                # Both supported writes are idempotent and readable.
                field = {'on-time': 'OnTime', 'start-up-on-off': 'StartUpOnOff'}[args[2]]
                if b'(IM:WriteResponse)' in reply and not code:
                    check = SESSION.run((args[0], 'read', args[2], *args[4:]))
                    values = dict(PATTERN.findall(check.decode(errors='replace')))
                    if not re.search(failure, check) and values.get(field) == str(args[3]):
                        return
            elif re.search(rb'\[DMG\]\s+Received Command Response Status for Endpoint=1 Cluster=\S+ Command=\S+ Status=0x0\b', reply):
                return
        if code == b'0x000000DB':
            if attempt + 1 < attempts:
                time.sleep(1)
                continue
            raise RuntimeError('Лампа занята. Попробуйте ещё раз.')
        SESSION.close()
        if code == b'0x02000041':
            raise RuntimeError('Нет маршрута от пульта к лампе. Проверьте доступ Python к локальной сети в настройках macOS и подключение лампы к домашней сети.')
        if attempt + 1 == attempts:
            raise RuntimeError('Контроллер не получил подтверждение от лампы.')


def snapshot():
    values = dict(PATTERN.findall(matter('any', 'read-by-id', '6,8,768', '0xFFFFFFFF', 1, 1, retry_busy=True)))
    if set(values) != set(FIELDS):
        raise RuntimeError('Получено неполное состояние лампы.')
    return {k: {'TRUE': True, 'FALSE': False, 'null': None}.get(v, int(v) if v.isdigit() else None)
            for k, v in values.items()}


def number(data, key, low, high):
    n = data.get(key)
    if type(n) is not int or not low <= n <= high:
        raise ValueError('Значение вне допустимого диапазона.')
    return n


def command(d):
    if not isinstance(d, dict):
        raise ValueError('Ожидалась команда.')
    action = d.get('action')
    if action == 'power':
        if type(d.get('on')) is not bool:
            raise ValueError('Некорректное состояние.')
        return ['onoff', 'on' if d['on'] else 'off']
    if action == 'brightness':
        return ['levelcontrol','move-to-level-with-on-off',round(number(d,'value',1,100)*254/100),number(d,'fade',0,100),0,0]
    if action == 'color':
        return ['colorcontrol','move-to-hue-and-saturation',number(d,'hue',0,254),number(d,'sat',0,254),number(d,'fade',0,100),1,1]
    if action == 'white':
        mired = max(154,min(370,round(1000000/number(d,'kelvin',2700,6500))))
        return ['colorcontrol','move-to-color-temperature',mired,number(d,'fade',0,100),1,1]
    if action == 'loop':
        return ['colorcontrol','color-loop-set',7,number(d,'active',0,1)*2,number(d,'direction',0,1),number(d,'seconds',1,3600),0,1,1]
    if action == 'timer':
        minutes = number(d,'minutes',0,60)
        if minutes == 0:
            return ['onoff','write','on-time',0]
        return ['onoff','on-with-timed-off',0,minutes*600,0]
    if action == 'startup' and d.get('value') in ('0','1','2','null'):
        return ['onoff','write','start-up-on-off',d['value']]
    if action == 'identify':
        return ['identify','identify',3]
    raise ValueError('Неизвестная команда.')


def garland_frame(data):
    if not isinstance(data, dict):
        raise ValueError('Ожидалась команда.')
    action = data.get('action')
    if action == 'power' and type(data.get('on')) is bool:
        on = int(data['on'])
        payload = [4, 4, on, 0, on, 255, 0]
        label = 'включить' if on else 'выключить'
    elif action == 'brightness':
        level = number(data, 'value', 1, 100)
        payload = [4, 1, level, 1, 255, 255, 0]
        label = f'яркость {level} %'
    elif action == 'color' and isinstance(data.get('hex'), str) and re.fullmatch(r'#[a-fA-F0-9]{6}', data['hex']):
        rgb = bytes.fromhex(data['hex'][1:])
        peak = max(rgb)
        if not peak:
            raise ValueError('Чёрный цвет соответствует выключению.')
        payload = [7, 5, 3, *(round(c * 255 / peak) for c in rgb), 16]
        label = 'цвет ' + data['hex'].upper()
    else:
        raise ValueError('Некорректная команда гирлянды.')
    return bytes([126, *payload, 239]), label


def send_garland(data):
    global last_garland_command
    frame, label = garland_frame(data)
    with BLE_LOCK:
        try:
            result = subprocess.run(['/usr/bin/swift', str(WEB / 'garland.swift'),
                                     *[f'{b:02x}' for b in frame]], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Истекло время ожидания Bluetooth.') from exc
        if result.returncode:
            errors = {2: 'Включите Bluetooth на Маке.', 3: 'Разрешите доступ к Bluetooth в настройках macOS.',
                      4: 'Не удалось подключиться к гирлянде.', 5: 'Мак не нашёл гирлянду рядом.'}
            raise RuntimeError(errors.get(result.returncode, 'Не удалось запустить Bluetooth-контроллер. Проверьте Swift на Маке.'))
        last_garland_command = label
    return {'last': label}


class Remote(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # The bookmark contains a household access key.

    def reply(self, code, body, mime='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body,ensure_ascii=False).encode()
        self.send_response(code)
        for key, value in {'Content-Type':mime, 'Content-Length':str(len(body)), 'Cache-Control':'no-store',
                'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"} .items():
            self.send_header(key,value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route(self, write=False):
        global CACHE, UPDATED
        parts = urlsplit(self.path).path.split('/')
        host = self.headers.get('Host','').lower()
        if (host not in self.server.hosts or len(parts)!=4 or parts[1]!='p'
                or not hmac.compare_digest(parts[2].encode(),self.server.key)):
            return self.reply(404,{'error':'Откройте личный ярлык пульта.'})
        route = parts[3]
        if not write and route in ('','app.js','icon.png'):
            filename,mime = {'':('index.html','text/html; charset=utf-8'),'app.js':('app.js','text/javascript; charset=utf-8'),
                             'icon.png':('icon.png','image/png')}[route]
            return self.reply(200,(WEB/filename).read_bytes(),mime)
        if route not in ('garland', 'command' if write else 'state'):
            return self.reply(404,{})
        try:
            if write:
                if (self.headers.get('Origin')!='http://'+host or self.headers.get_content_type()!='application/json'):
                    return self.reply(403,{})
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 1024:
                    return self.reply(400,{})
                body=json.loads(self.rfile.read(length))
                if route == 'garland':
                    return self.reply(200, send_garland(body))
                args=command(body)
            elif route == 'garland':
                return self.reply(200, {'last': last_garland_command})
            with LOCK:
                if write:
                    UPDATED=0
                    matter(*args,1,1)
                    return self.reply(200,{'ok':True})
                if time.monotonic()-UPDATED>3 or not CACHE:
                    CACHE=snapshot()
                    UPDATED=time.monotonic()
                data=CACHE.copy()
            self.reply(200,data)
        except (ValueError,UnicodeError):
            self.reply(400,{'error':'Проверьте параметры команды.'})
        except (RuntimeError,OSError) as e:
            self.reply(503,{'error':str(e) if isinstance(e,RuntimeError) else 'Контроллер недоступен.'})

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route(True)


def wifi_ip():
    result=subprocess.run(['/usr/sbin/ipconfig','getifaddr','en0'],capture_output=True,text=True)
    if result.returncode: return None
    ip=ipaddress.IPv4Address(result.stdout.strip())
    return str(ip) if ip.is_private and not ip.is_loopback else None


def serve():
    os.umask(0o077)
    key=(ROOT/'remote-access'/'key').read_bytes().strip()
    if not re.fullmatch(rb'[A-Za-z0-9_-]{32,64}',key): raise ValueError('Invalid access key')
    ip=wifi_ip()
    if not ip: return
    name=subprocess.check_output(['/usr/sbin/scutil','--get','LocalHostName'],text=True).strip().lower()
    with ThreadingHTTPServer((ip,PORT),Remote) as server:
        server.key=key
        server.hosts={f'{ip}:{PORT}',f'{name}.local:{PORT}'}
        server.timeout=3
        next_check=time.monotonic()+20
        while True:
            server.handle_request()
            if time.monotonic()>=next_check:
                if wifi_ip()!=ip: return
                next_check=time.monotonic()+20


if __name__=='__main__':
    serve()
