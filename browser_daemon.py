#!/usr/bin/env python3
"""
브라우저 데몬 — 상주 Chromium + CDP WebSocket 통신
URL 요청 시 상주 Chromium에 CDP로 페이지를 로드하고 텍스트를 반환합니다.

의존성: 없음 (표준 라이브러리만 사용)
"""

import collections
import hashlib
import json
import os
import queue
import re
import signal
import socket
import struct
import subprocess
import sys
import time
import threading
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

CHROMIUM_PORT = 9222
DAEMON_PORT = 9223
CHROMIUM_CMD = (
    "chromium-browser --headless=new --disable-gpu --no-sandbox "
    "--disable-dev-shm-usage "
    "--blink-settings=imagesEnabled=false "
    "--disable-background-networking "
    "--disable-default-apps "
    "--disable-extensions "
    "--disable-plugins "
    "--disable-blink-features=AutomationControlled "
    "--user-agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' "
    "--mute-audio "
    "--no-first-run "
    f"--remote-debugging-port={CHROMIUM_PORT} "
    "--remote-debugging-address=127.0.0.1 "
    "about:blank"
)

chromium_proc = None
_screencast_stop = False  # 스크린캐스트 중지 시그널
_screencast_session_id = 0  # 세션 ID (연속 호출 BrokenPipeError 방지)
_screencast_buffer = {"frame": "", "ts": 0, "seq": 0, "source": "url", "url_idx": 0}  # 공유 프레임 버퍼
_screencast_queue = collections.deque(maxlen=64)  # 프레임 큐 (버퍼 폴링 모드용, 유실 방지)
_screencast_lock = threading.Lock()  # 큐 동기화

# ── 탭 풀 (4개 상주) ──
TAB_POOL_SIZE = 4
_tab_pool = queue.Queue(maxsize=TAB_POOL_SIZE)  # {"id": str, "ws_url": str}
_tab_pool_ready = False


def _force_close_tab(tab_id):
    """탭 강제 닫기 (GET/PUT 모두 시도)"""
    if not tab_id:
        return
    close_url = f"http://127.0.0.1:{CHROMIUM_PORT}/json/close/{tab_id}"
    for method in ('GET', 'PUT'):
        try:
            req = urllib.request.Request(close_url, method=method)
            urllib.request.urlopen(req, timeout=2)
            return
        except Exception:
            pass


def init_tab_pool():
    """Chromium 시작 후 4개 탭을 사전 생성하여 풀에 넣기"""
    global _tab_pool_ready
    # 기존 탭 정리
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROMIUM_PORT}/json", timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
        for tab in tabs:
            if tab.get("url", "") != "about:blank":
                _force_close_tab(tab.get("id"))
    except Exception:
        pass

    created = 0
    for i in range(TAB_POOL_SIZE):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{CHROMIUM_PORT}/json/new?about:blank",
                method='PUT'
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                tab_info = json.loads(resp.read().decode())
            tab_id = tab_info.get("id", "")
            ws_url = tab_info.get("webSocketDebuggerUrl", "")
            if tab_id and ws_url:
                _tab_pool.put({"id": tab_id, "ws_url": ws_url})
                created += 1
        except Exception as e:
            print(f"[browser_daemon] ⚠ 탭 {i} 생성 실패: {e}", flush=True)
    _tab_pool_ready = created > 0
    print(f"[browser_daemon] 🏊 탭 풀 초기화: {created}/{TAB_POOL_SIZE}개", flush=True)


def _acquire_tab(timeout=10):
    """풀에서 탭 하나 획득 후, /json에서 최신 ws_url 갱신"""
    try:
        tab_info = _tab_pool.get(timeout=timeout)
    except queue.Empty:
        return None
    # ws_url 갱신 (이전 WebSocket 닫히면 URL이 바뀐 수 있음)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROMIUM_PORT}/json", timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
        for t in tabs:
            if t.get("id") == tab_info["id"]:
                new_ws = t.get("webSocketDebuggerUrl", "")
                if new_ws:
                    tab_info["ws_url"] = new_ws
                break
    except Exception:
        pass
    return tab_info


def _release_tab(tab_info):
    """탭을 풀에 반납 (단순 반납, WebSocket 건드리지 않음)"""
    if not tab_info:
        return
    try:
        _tab_pool.put_nowait(tab_info)
    except queue.Full:
        pass


def _recreate_tab_for_pool(old_tab_info=None):
    """탭이 죽었을 때 새로 만들어서 풀에 반납"""
    if old_tab_info:
        _force_close_tab(old_tab_info.get("id"))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{CHROMIUM_PORT}/json/new?about:blank",
            method='PUT'
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            tab_info = json.loads(resp.read().decode())
        new_tab = {"id": tab_info.get("id", ""), "ws_url": tab_info.get("webSocketDebuggerUrl", "")}
        if new_tab["id"] and new_tab["ws_url"]:
            try:
                _tab_pool.put_nowait(new_tab)
            except queue.Full:
                pass
            return
    except Exception:
        pass


# ═══════════════════════════════════════
# 미니멀 WebSocket 클라이언트 (표준 라이브러리)
# ═══════════════════════════════════════

class MiniWebSocket:
    """CDP 통신용 최소 WebSocket 클라이언트"""

    def __init__(self, url: str, timeout: int = 10):
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"

        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)

        # WebSocket 핸드셰이크
        key = hashlib.sha1(os.urandom(16)).hexdigest()[:16]
        import base64
        ws_key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self.sock.sendall(handshake.encode())

        # 핸드셰이크 응답 읽기
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 핸드셰이크 실패")
            resp += chunk

    def send(self, data: str):
        """텍스트 프레임 전송"""
        payload = data.encode("utf-8")
        frame = bytearray()
        frame.append(0x81)  # FIN + TEXT

        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)  # MASK bit set
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", length))

        # 마스킹 키
        mask_key = os.urandom(4)
        frame.extend(mask_key)
        masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        frame.extend(masked)

        self.sock.sendall(frame)

    def recv(self) -> str:
        """텍스트 프레임 수신"""
        header = self._recv_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]

        if masked:
            mask_key = self._recv_exact(4)

        payload = self._recv_exact(length)

        if masked:
            payload = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == 0x01:  # TEXT
            return payload.decode("utf-8", errors="replace")
        elif opcode == 0x08:  # CLOSE
            return ""
        return payload.decode("utf-8", errors="replace")

    def _recv_exact(self, n: int) -> bytes:
        """정확히 n바이트 수신"""
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("연결 끊김")
            buf += chunk
        return buf

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ═══════════════════════════════════════
# Chromium 관리
# ═══════════════════════════════════════

def start_chromium():
    global chromium_proc
    print(f"[browser_daemon] Chromium 시작 중 (CDP port={CHROMIUM_PORT})...", flush=True)
    chromium_proc = subprocess.Popen(
        CHROMIUM_CMD, shell=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for i in range(20):
        try:
            with socket.create_connection(("127.0.0.1", CHROMIUM_PORT), timeout=1):
                print(f"[browser_daemon] Chromium CDP 준비 완료 ({i+1}초)", flush=True)
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(1)
    print("[browser_daemon] ⚠ Chromium 시작 시간 초과", flush=True)
    return False


def fetch_page_cdp(url: str, max_timeout: int = 10, idle_timeout: float = 2.0) -> tuple:
    """새 탭 생성 → CDP로 페이지 로드 + DOM 텍스트 추출 + 스크린샷 → 탭 닫기
    Returns: (html: str, screenshot_base64: str)

    종료 조건 (먼저 충족된 것이 우선):
      1. Page.loadEventFired → 모든 리소스 로딩 완료 (이상적)
      2. domContentLoaded 후 1.5초 추가 대기 → DOM 파싱 완료 (빠른 탈출)
      3. 네트워크 idle (활성 요청 0개가 1초 유지) → 실질적 로딩 완료
      4. max_timeout 초과 → 강제 종료 (안전장치)
      5. 페이지 이벤트 없이 idle_timeout 경과 → 응답 없는 페이지
    """
    tab_id = None
    ws = None
    final_screenshot = ""
    try:
        # 빈 탭 생성 (URL 없이 — 이벤트 등록 후 navigate)
        new_tab_url = f"http://127.0.0.1:{CHROMIUM_PORT}/json/new"
        req = urllib.request.Request(new_tab_url, method='PUT')
        with urllib.request.urlopen(req, timeout=5) as resp:
            tab_info = json.loads(resp.read().decode())
        tab_id = tab_info.get("id", "")
        ws_url = tab_info.get("webSocketDebuggerUrl", "")
        if not ws_url:
            return "", ""

        ws = MiniWebSocket(ws_url, timeout=idle_timeout)

        # 뷰포트 + 이벤트 활성화 (navigate 전에 먼저!)
        def _cdp_send_recv(ws, msg_id, method, params=None):
            """CDP 명령 전송 + ID 매칭 응답 대기 (이벤트 무시)"""
            payload = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            ws.send(json.dumps(payload))
            for _ in range(20):
                resp = ws.recv()
                if not resp:
                    return None
                data = json.loads(resp)
                if data.get("id") == msg_id:
                    return data
            return None

        _cdp_send_recv(ws, 0, "Emulation.setDeviceMetricsOverride",
                       {"width": 960, "height": 540, "deviceScaleFactor": 1, "mobile": False})
        _cdp_send_recv(ws, 1, "Page.enable")

        # SLiRP 연결 포화 방지: JS/분석/광고 차단 (CSS/폰트는 렌더링에 필요하므로 허용)
        # Chromium 1페이지 로드 시 JS가 60+개 외부 연결 생성 → SLiRP 슬롯 포화 → SSH 차단
        _cdp_send_recv(ws, 2, "Network.enable")
        _cdp_send_recv(ws, 21, "Network.setBlockedURLs", {"urls": [
            "*.js",
            "*google-analytics*", "*googletagmanager*", "*doubleclick*",
            "*facebook.net*", "*facebook.com/tr*",
            "*twitter.com/i/*", "*linkedin.com/li/*",
            "*ads*", "*tracking*", "*beacon*", "*pixel*",
        ]})

        # 이벤트 등록 완료 후 navigate
        _cdp_send_recv(ws, 3, "Page.navigate", {"url": url})

        global _screencast_buffer
        with _screencast_lock:
            _screencast_buffer["url_idx"] = _screencast_buffer.get("url_idx", 0) + 1
            my_url_idx = _screencast_buffer["url_idx"]
        deadline = time.time() + max_timeout
        last_page_event = time.time()  # 페이지 이벤트 전용 타이머 (스크린샷 제외)
        last_capture = time.time()
        capture_id = 900

        # 네트워크 idle 추적
        active_requests = 0          # 진행 중 네트워크 요청 수
        net_idle_since = None        # 활성 요청이 0이 된 시각
        NET_IDLE_THRESHOLD = 1.0     # 0개 요청이 이 시간 유지되면 idle 판정

        # DOM 완료 추적
        dom_content_loaded = False   # Page.domContentLoaded 수신 여부
        dom_loaded_at = None         # domContentLoaded 수신 시각
        DOM_GRACE_PERIOD = 1.5       # domContentLoaded 후 추가 대기 시간

        screenshot_sent = 0
        screenshot_recv = 0
        exit_reason = "deadline"
        print(f"[browser_daemon] 🔍 fetch loop starting: url_idx={my_url_idx} timeout={max_timeout}s", flush=True)
        while time.time() < deadline:
            now = time.time()

            # domContentLoaded 후 grace period 경과 → 탈출
            if dom_content_loaded and (now - dom_loaded_at) >= DOM_GRACE_PERIOD:
                exit_reason = "dom_grace"
                print(f"[browser_daemon] ✅ DOM grace period done url_idx={my_url_idx} ({DOM_GRACE_PERIOD}s after domContentLoaded)", flush=True)
                break

            # 네트워크 idle 감지 (활성 요청 0개 → NET_IDLE_THRESHOLD초 유지)
            if active_requests <= 0 and net_idle_since and dom_content_loaded:
                if (now - net_idle_since) >= NET_IDLE_THRESHOLD:
                    exit_reason = "net_idle"
                    print(f"[browser_daemon] ✅ network idle url_idx={my_url_idx} (0 requests for {NET_IDLE_THRESHOLD}s)", flush=True)
                    break

            if now - last_capture >= 0.5:
                capture_id += 1
                try:
                    ws.send(json.dumps({
                        "id": capture_id, "method": "Page.captureScreenshot",
                        "params": {"format": "jpeg", "quality": 25}
                    }))
                    screenshot_sent += 1
                except Exception as e:
                    print(f"[browser_daemon] ⚠ screenshot send error: {e}", flush=True)
                last_capture = time.time()

            remaining = min(idle_timeout, deadline - time.time())
            if remaining <= 0:
                break
            ws.sock.settimeout(min(remaining, 0.15))
            try:
                msg = ws.recv()
                if not msg:
                    break
                data = json.loads(msg)
                msg_id = data.get("id", -1)
                msg_method = data.get("method", "")
                msg_error = data.get("error", "")

                # 스크린샷 응답 — 별도 처리 (페이지 이벤트 타이머에 영향 안 줌)
                if msg_id >= 900:
                    screenshot_recv += 1
                    if msg_error:
                        print(f"[browser_daemon] ⚠ screenshot error id={msg_id}: {msg_error}", flush=True)
                        continue
                    frame = data.get("result", {}).get("data", "")
                    if frame and len(frame) > 2000:
                        _screencast_buffer["frame"] = frame
                        _screencast_buffer["ts"] = time.time()
                        _screencast_buffer["seq"] += 1
                        _screencast_buffer["source"] = "url"
                        with _screencast_lock:
                            _screencast_queue.append({
                                "frame": frame,
                                "source": "url",
                                "url_idx": my_url_idx
                            })
                            qlen = len(_screencast_queue)
                        print(f"[browser_daemon] 📸 frame queued: url_idx={my_url_idx} size={len(frame)}B qlen={qlen}", flush=True)
                    else:
                        print(f"[browser_daemon] ⚠ screenshot too small: id={msg_id} len={len(frame) if frame else 0}", flush=True)
                    continue

                # 페이지 이벤트 → idle 타이머 리셋
                last_page_event = time.time()

                if msg_method == "Page.loadEventFired":
                    exit_reason = "load"
                    print(f"[browser_daemon] ✅ Page.loadEventFired url_idx={my_url_idx} sent={screenshot_sent} recv={screenshot_recv}", flush=True)
                    time.sleep(0.3)
                    break

                if msg_method == "Page.domContentEventFired":
                    dom_content_loaded = True
                    dom_loaded_at = time.time()
                    print(f"[browser_daemon] 📄 domContentLoaded url_idx={my_url_idx} (grace={DOM_GRACE_PERIOD}s)", flush=True)

                # 네트워크 요청 추적
                if msg_method == "Network.requestWillBeSent":
                    active_requests += 1
                    net_idle_since = None  # 새 요청 → idle 리셋
                elif msg_method in ("Network.loadingFinished", "Network.loadingFailed"):
                    active_requests = max(0, active_requests - 1)
                    if active_requests <= 0 and not net_idle_since:
                        net_idle_since = time.time()

            except socket.timeout:
                idle = time.time() - last_page_event
                if idle >= idle_timeout:
                    exit_reason = "page_idle"
                    print(f"[browser_daemon] ⏱ page event idle url_idx={my_url_idx} ({idle:.1f}s) sent={screenshot_sent} recv={screenshot_recv}", flush=True)
                    break
            except Exception as e:
                exit_reason = "error"
                print(f"[browser_daemon] ⚠ loop error: {e}", flush=True)
                break
        print(f"[browser_daemon] 🔍 fetch loop done: url_idx={my_url_idx} reason={exit_reason} sent={screenshot_sent} recv={screenshot_recv} net_reqs={active_requests}", flush=True)

        # ── DOM 텍스트 추출 (먼저 — 안정적으로 작동) ──
        html = ""
        try:
            ws.sock.settimeout(5)
            ws.send(json.dumps({"id": 10, "method": "Runtime.evaluate", "params": {"expression": "document.documentElement.outerHTML"}}))
            for _ in range(20):
                rmsg = ws.recv()
                if not rmsg:
                    break
                rd = json.loads(rmsg)
                if rd.get("id") == 10:
                    html = rd.get("result", {}).get("result", {}).get("value", "")
                    break
        except Exception as e:
            print(f"[browser_daemon] ⚠ DOM 추출 오류: {e}", flush=True)

        # ── 최종 스크린샷 (DOM 추출 후 — WebSocket 아직 살아있음) ──
        try:
            ws.sock.settimeout(5)
            ws.send(json.dumps({"id": 999, "method": "Page.captureScreenshot", "params": {"format": "jpeg", "quality": 25}}))
            print(f"[browser_daemon] 📤 screenshot sent id=999, waiting...", flush=True)
            found_999 = False
            for attempt in range(50):
                fmsg = ws.recv()
                if not fmsg:
                    print(f"[browser_daemon] ⚠ screenshot recv #{attempt}: empty", flush=True)
                    break
                fd = json.loads(fmsg)
                msg_id = fd.get("id", "none")
                method = fd.get("method", "")
                if msg_id == 999:
                    ff = fd.get("result", {}).get("data", "")
                    if ff and len(ff) > 500:
                        final_screenshot = ff
                        with _screencast_lock:
                            _screencast_queue.append({"frame": ff, "source": "url", "url_idx": my_url_idx})
                        print(f"[browser_daemon] 📸 screenshot: url_idx={my_url_idx} size={len(ff)}B (attempt={attempt})", flush=True)
                    else:
                        print(f"[browser_daemon] ⚠ screenshot too small: {len(ff) if ff else 0}B", flush=True)
                    found_999 = True
                    break
                # 이벤트 무시
                if attempt < 3:
                    print(f"[browser_daemon] 🔍 screenshot recv #{attempt}: id={msg_id} method={method} keys={list(fd.keys())[:5]}", flush=True)
            if not found_999:
                print(f"[browser_daemon] ❌ screenshot id=999 not found after {attempt+1} messages", flush=True)
        except Exception as e:
            print(f"[browser_daemon] ⚠ screenshot 오류: {e}", flush=True)

        return html, final_screenshot

    except Exception as e:
        print(f"[browser_daemon] CDP 오류: {e}", flush=True)
        return "", ""
    finally:
        if ws:
            ws.close()
        # 사용 완료 탭 닫기 (최소 1개 유지 — Chromium 종료 방지)
        if tab_id:
            try:
                tabs_resp = urllib.request.urlopen(f"http://127.0.0.1:{CHROMIUM_PORT}/json", timeout=2)
                tabs = json.loads(tabs_resp.read().decode())
                if len(tabs) > 1:
                    close_req = urllib.request.Request(f"http://127.0.0.1:{CHROMIUM_PORT}/json/close/{tab_id}", method='PUT')
                    urllib.request.urlopen(close_req, timeout=2)
            except Exception:
                pass


# ═══════════════════════════════════════
# HTML → 텍스트 변환
# ═══════════════════════════════════════

def clean_html(html: str) -> str:
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


def extract_content(html: str, url: str, max_length: int = 8000) -> str:
    if not html or len(html) < 50:
        return ""
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    title = clean_html(title_match.group(1)).strip() if title_match else ""
    main_content = ""
    for tag in ["article", "main"]:
        match = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.DOTALL | re.IGNORECASE)
        if match and len(match.group(1)) > 200:
            main_content = match.group(1)
            break
    text = clean_html(main_content) if main_content else clean_html(html)
    if len(text) > max_length:
        text = text[:max_length] + "\n... (잘림)"
    header = f"🌐 {title}\n🔗 {url}\n{'─' * 40}\n" if title else f"🔗 {url}\n{'─' * 40}\n"
    return header + text if text else ""


# ═══════════════════════════════════════
# HTTP 서버 (멀티스레드 — 동시 페이지 요청 처리)
# ═══════════════════════════════════════

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class BrowserHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[browser_daemon] {args[0]}", flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return

        if parsed.path == "/fetch":
            params = parse_qs(parsed.query)
            url = params.get("url", [""])[0]
            if not url:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "url parameter required"}).encode())
                return

            print(f"[browser_daemon] 🌐 페이지 로드: {url}", flush=True)
            start_time = time.time()

            html = ""
            screenshot = ""

            # 1차: 상주 Chromium CDP (JS 렌더링 지원) — 기본
            html, screenshot = fetch_page_cdp(url)

            # 2차: CDP 실패 시 wget 폴백
            if not html or len(clean_html(html)) < 100:
                try:
                    cmd = f"wget -qO- --timeout=3 --header='User-Agent: Mozilla/5.0' '{url}' 2>/dev/null"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=7)
                    if result.returncode == 0 and len(result.stdout) > len(html or ""):
                        html = result.stdout
                except Exception:
                    pass

            elapsed = time.time() - start_time

            if html:
                content = extract_content(html, url)
                print(f"[browser_daemon] ✅ {len(content)}자 추출 ({elapsed:.1f}초) screenshot={len(screenshot)}B", flush=True)
            else:
                content = f"❌ 페이지를 로드할 수 없습니다: {url}"
                print(f"[browser_daemon] ❌ 로드 실패 ({elapsed:.1f}초)", flush=True)

            resp_data = {"content": content, "elapsed": elapsed}
            if screenshot:
                resp_data["screenshot"] = screenshot

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        if parsed.path == "/screencast":
            self._handle_screencast(parsed)
            return

        if parsed.path == "/screencast/stop":
            global _screencast_stop
            _screencast_stop = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "stopping"}).encode())
            return

        self.send_response(404)
        self.end_headers()

    def _handle_screencast(self, parsed):
        """SSE 스트리밍 — 전용 탭을 열어 검색 페이지를 캡처"""
        global _screencast_stop, _screencast_session_id
        _screencast_stop = False
        _screencast_session_id += 1
        my_session = _screencast_session_id

        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0]
        url = params.get("url", [""])[0]

        # 검색 쿼리가 있으면 DuckDuckGo 검색 페이지로
        if query:
            nav_url = f"https://duckduckgo.com/?q={urllib.request.quote(query)}"
        elif url:
            nav_url = url
        else:
            nav_url = ""

        # SSE 헤더
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        if not nav_url:
            # URL 없으면 큐 폴링 모드 (Tavily 검색 시 사용 — 프레임 유실 방지)
            print("[browser_daemon] 📹 스크린캐스트: 큐 폴링 모드", flush=True)
            # 큐 초기화
            with _screencast_lock:
                _screencast_queue.clear()
            deadline = time.time() + 90
            frame_count = 0
            poll_log_counter = 0
            while time.time() < deadline and not _screencast_stop and _screencast_session_id == my_session:
                try:
                    # 큐에서 모든 대기 프레임 드레인
                    frames_to_send = []
                    with _screencast_lock:
                        while _screencast_queue:
                            frames_to_send.append(_screencast_queue.popleft())
                    if frames_to_send:
                        print(f"[browser_daemon] 📤 polling: sending {len(frames_to_send)} frames", flush=True)
                    for fdata in frames_to_send:
                        frame_count += 1
                        sse_data = json.dumps(fdata)
                        self.wfile.write(f"data: {sse_data}\n\n".encode())
                        self.wfile.flush()
                    poll_log_counter += 1
                    if poll_log_counter % 40 == 0:  # ~6초마다 로그
                        with _screencast_lock:
                            qlen = len(_screencast_queue)
                        print(f"[browser_daemon] 🔄 polling alive: {frame_count} frames sent, qlen={qlen}, stop={_screencast_stop}", flush=True)
                    time.sleep(0.15)  # ~6fps 폴링
                except (BrokenPipeError, ConnectionResetError):
                    print(f"[browser_daemon] ❌ SSE pipe broken after {frame_count} frames", flush=True)
                    break
            print(f"[browser_daemon] 📹 큐 폴링 종료 ({frame_count}프레임)", flush=True)
            try:
                self.wfile.write(b"data: {\"done\": true}\n\n")
                self.wfile.flush()
            except Exception:
                pass
            return

        # 전용 탭으로 캡처 (하이브리드: 전용탭 + 버퍼 프레임)
        tab_id = None
        ws = None
        print(f"[browser_daemon] 📹 스크린캐스트 시작: {nav_url[:80]}", flush=True)

        try:
            # 새 탭 생성 (PUT 필수)
            encoded = urllib.request.quote(nav_url, safe='/:?=&%+')
            new_tab_url = f"http://127.0.0.1:{CHROMIUM_PORT}/json/new?{encoded}"
            req = urllib.request.Request(new_tab_url, method='PUT')
            with urllib.request.urlopen(req, timeout=5) as resp:
                tab_info = json.loads(resp.read().decode())
            tab_id = tab_info.get("id", "")
            ws_url = tab_info.get("webSocketDebuggerUrl", "")
            if not ws_url:
                self.wfile.write(b"data: {\"done\": true}\n\n")
                self.wfile.flush()
                return

            ws = MiniWebSocket(ws_url, timeout=5)

            # 뷰포트 설정 (1920x1080)
            ws.send(json.dumps({
                "id": 1, "method": "Emulation.setDeviceMetricsOverride",
                "params": {"width": 960, "height": 540, "deviceScaleFactor": 1, "mobile": False}
            }))
            ws.recv()

            # Page.enable
            ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
            ws.recv()

            # 주기적 캡처 (단계적 전환: DuckDuckGo → 실제 페이지 → DuckDuckGo)
            deadline = time.time() + 10
            frame_count = 0
            capture_id = 100
            last_buf_seq = _screencast_buffer.get("seq", 0)
            using_buffer = False  # 버퍼 프레임 사용 중 여부
            last_buf_time = 0     # 마지막 버퍼 프레임 시간

            while time.time() < deadline and not _screencast_stop:
                frame_sent = False

                # 버퍼에 새 프레임이 있는지 확인
                cur_seq = _screencast_buffer.get("seq", 0)
                if cur_seq > last_buf_seq and _screencast_buffer.get("frame"):
                    last_buf_seq = cur_seq
                    last_buf_time = time.time()
                    using_buffer = True
                    frame_count += 1
                    sse_data = json.dumps({"frame": _screencast_buffer["frame"], "source": _screencast_buffer.get("source", "url"), "url_idx": _screencast_buffer.get("url_idx", 0)})
                    self.wfile.write(f"data: {sse_data}\n\n".encode())
                    self.wfile.flush()
                    frame_sent = True

                # 버퍼 2초 이상 업데이트 없으면 DuckDuckGo로 복귀
                if using_buffer and time.time() - last_buf_time > 2.0:
                    using_buffer = False

                # 버퍼 사용 중이면 DuckDuckGo 캡처 건너뜀
                if not frame_sent and not using_buffer:
                    capture_id += 1
                    try:
                        ws.send(json.dumps({
                            "id": capture_id, "method": "Page.captureScreenshot",
                            "params": {"format": "jpeg", "quality": 25}
                        }))
                        for _ in range(5):
                            ws.sock.settimeout(0.3)
                            msg = ws.recv()
                            if not msg:
                                break
                            data = json.loads(msg)
                            if data.get("id") == capture_id:
                                frame = data.get("result", {}).get("data", "")
                                if frame:
                                    frame_count += 1
                                    sse_data = json.dumps({"frame": frame, "source": "ddg"})
                                    self.wfile.write(f"data: {sse_data}\n\n".encode())
                                    self.wfile.flush()
                                break
                    except socket.timeout:
                        pass
                    except Exception:
                        pass

                time.sleep(0.3)

            print(f"[browser_daemon] 📹 스크린캐스트 종료 ({frame_count}프레임)", flush=True)

        except Exception as e:
            print(f"[browser_daemon] 📹 스크린캐스트 실패: {e}", flush=True)
        finally:
            if ws:
                ws.close()
            _force_close_tab(tab_id)

        try:
            self.wfile.write(b"data: {\"done\": true}\n\n")
            self.wfile.flush()
        except Exception:
            pass


def main():
    if not start_chromium():
        print("[browser_daemon] ⚠ Chromium 없이 wget 폴백 모드로 동작합니다.", flush=True)

    server = ThreadedHTTPServer(("0.0.0.0", DAEMON_PORT), BrowserHandler)
    print(f"[browser_daemon] ✅ HTTP 서버 시작 (port={DAEMON_PORT}, threaded)", flush=True)

    def shutdown(sig, frame):
        print("\n[browser_daemon] 종료 중...", flush=True)
        if chromium_proc:
            chromium_proc.terminate()
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
