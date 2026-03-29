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
    "--disable-software-rasterizer --disable-dev-shm-usage "
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
_screencast_buffer = {"frame": "", "ts": 0, "seq": 0, "source": "url", "url_idx": 0}  # 공유 프레임 버퍼
_screencast_queue = collections.deque(maxlen=64)  # 프레임 큐 (버퍼 폴링 모드용, 유실 방지)
_screencast_lock = threading.Lock()  # 큐 동기화


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


def _cleanup_stale_tabs():
    """기본 about:blank 탭만 남기고 나머지 모두 닫기 (탭 누적 방지)"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROMIUM_PORT}/json", timeout=2) as resp:
            tabs = json.loads(resp.read().decode())
        closed = 0
        for tab in tabs:
            if tab.get("url", "") != "about:blank":
                _force_close_tab(tab.get("id"))
                closed += 1
        if closed:
            print(f"[browser_daemon] 🧹 stale tabs closed: {closed}", flush=True)
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
    """Chromium headless 프로세스를 시작합니다."""
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


def fetch_page_cdp(url: str, max_timeout: int = 10, idle_timeout: float = 2.0) -> str:
    """상주 Chromium에 CDP WebSocket으로 페이지 로드 + DOM 텍스트 추출
    
    max_timeout: 최대 대기 시간 (로딩 중이라도 이 시간이 지나면 중단)
    idle_timeout: 이벤트 없이 이 시간이 지나면 "막힌 것"으로 판단하고 중단
    """
    tab_id = None
    ws = None
    try:
        # 1. 새 탭 생성
        encoded_url = urllib.request.quote(url, safe='')
        new_tab_url = f"http://127.0.0.1:{CHROMIUM_PORT}/json/new?{encoded_url}"
        req = urllib.request.Request(new_tab_url, method='PUT')
        with urllib.request.urlopen(req, timeout=5) as resp:
            tab_info = json.loads(resp.read().decode())
        tab_id = tab_info.get("id", "")
        ws_url = tab_info.get("webSocketDebuggerUrl", "")
        if not ws_url:
            return ""

        # 2. WebSocket 연결
        ws = MiniWebSocket(ws_url, timeout=idle_timeout)

        # 3. Page + Network enable (이벤트로 로딩 상태 감지)
        # 뷰포트 설정 (1920x1080)
        ws.send(json.dumps({
            "id": 0, "method": "Emulation.setDeviceMetricsOverride",
            "params": {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False}
        }))
        ws.recv()

        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()  # ack
        ws.send(json.dumps({"id": 2, "method": "Network.enable"}))
        ws.recv()  # ack

        # 4. 활동 기반 대기 + 스크린캐스트 캡처
        #    - CDP 이벤트가 오면 "로딩 중" → 타이머 리셋
        #    - idle_timeout(3초) 동안 이벤트 없으면 "막힘" → 중단
        #    - max_timeout(10초)이면 무조건 중단
        #    - 로딩 중 주기적으로 스크린샷 캡처 → 공유 버퍼에 저장
        global _screencast_buffer
        # URL 인덱스 증가 (새 URL 시작 알림, 병렬 browse 대비 lock)
        with _screencast_lock:
            _screencast_buffer["url_idx"] = _screencast_buffer.get("url_idx", 0) + 1
            my_url_idx = _screencast_buffer["url_idx"]
        deadline = time.time() + max_timeout
        last_activity = time.time()
        last_capture = time.time()  # 첫 캡처는 0.5초 후 (interval 0.5s)
        capture_id = 900
        load_done = False

        while time.time() < deadline:
            # 스크린샷 캡처 요청 (~2fps, 500ms 간격 — 렌더링 후 캡처)
            now = time.time()
            if now - last_capture >= 0.5:
                capture_id += 1
                try:
                    ws.send(json.dumps({
                        "id": capture_id, "method": "Page.captureScreenshot",
                        "params": {"format": "jpeg", "quality": 45}
                    }))
                except Exception:
                    pass
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

                # 스크린샷 응답 처리 (id >= 900)
                if data.get("id", 0) >= 900:
                    frame = data.get("result", {}).get("data", "")
                    # 빈 페이지 필터 (빈 JPEG는 ~1KB 미만, 실제 콘텐츠는 10KB+)
                    if frame and len(frame) > 2000:
                        _screencast_buffer["frame"] = frame
                        _screencast_buffer["ts"] = time.time()
                        _screencast_buffer["seq"] += 1
                        _screencast_buffer["source"] = "url"  # URL 탐색 프레임
                        # 큐에도 추가 (버퍼 폴링 모드에서 유실 방지)
                        with _screencast_lock:
                            _screencast_queue.append({
                                "frame": frame,
                                "source": "url",
                                "url_idx": my_url_idx
                            })
                        if _screencast_buffer["seq"] % 5 == 1:
                            print(f"[browser_daemon] 📸 buffer seq={_screencast_buffer['seq']} ({len(frame)}B)", flush=True)
                    continue

                # 페이지 이벤트 처리
                last_activity = time.time()
                if data.get("method") == "Page.loadEventFired":
                    load_done = True
                    time.sleep(0.5)
                    # 최종 스크린샷
                    try:
                        capture_id += 1
                        ws.send(json.dumps({
                            "id": capture_id, "method": "Page.captureScreenshot",
                            "params": {"format": "jpeg", "quality": 45}
                        }))
                        cap_msg = ws.recv()
                        cap_data = json.loads(cap_msg)
                        frame = cap_data.get("result", {}).get("data", "")
                        if frame:
                            with _screencast_lock:
                                _screencast_queue.append({
                                    "frame": frame,
                                    "source": "url",
                                    "url_idx": my_url_idx
                                })
                            _screencast_buffer["frame"] = frame
                            _screencast_buffer["ts"] = time.time()
                            _screencast_buffer["seq"] += 1
                            _screencast_buffer["source"] = "url"
                    except Exception:
                        pass
                    break
            except socket.timeout:
                idle = time.time() - last_activity
                if idle >= idle_timeout:
                    print(f"[browser_daemon] ⏱ {idle:.1f}초 무응답 → 중단", flush=True)
                    break
            except Exception:
                break

        # 5. DOM 텍스트 추출
        ws.send(json.dumps({
            "id": 2,
            "method": "Runtime.evaluate",
            "params": {"expression": "document.documentElement.outerHTML"}
        }))
        result_msg = ws.recv()
        result_data = json.loads(result_msg)
        html = result_data.get("result", {}).get("result", {}).get("value", "")

        # 6. 최종 스크린샷 보장 (빠른 페이지도 최소 1프레임)
        try:
            ws.sock.settimeout(2)
            ws.send(json.dumps({
                "id": 999, "method": "Page.captureScreenshot",
                "params": {"format": "jpeg", "quality": 50}
            }))
            final_msg = ws.recv()
            final_data = json.loads(final_msg)
            final_frame = final_data.get("result", {}).get("data", "")
            if final_frame and len(final_frame) > 2000:
                _screencast_buffer["frame"] = final_frame
                _screencast_buffer["ts"] = time.time()
                _screencast_buffer["seq"] += 1
                _screencast_buffer["source"] = "url"
                with _screencast_lock:
                    _screencast_queue.append({
                        "frame": final_frame,
                        "source": "url",
                        "url_idx": my_url_idx
                    })
                print(f"[browser_daemon] 📸 final frame for url_idx={my_url_idx} ({len(final_frame)}B)", flush=True)
        except Exception:
            pass

        return html

    except Exception as e:
        print(f"[browser_daemon] CDP 오류: {e}", flush=True)
        return ""
    finally:
        if ws:
            ws.close()
        _force_close_tab(tab_id)


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

            # 1차: 상주 Chromium CDP (JS 렌더링 지원) — 기본
            html = fetch_page_cdp(url)

            # 2차: CDP 실패 시 wget 폴백
            if not html or len(clean_html(html)) < 100:
                try:
                    cmd = f"wget -qO- --timeout=5 --header='User-Agent: Mozilla/5.0' '{url}' 2>/dev/null"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=12)
                    if result.returncode == 0 and len(result.stdout) > len(html or ""):
                        html = result.stdout
                except Exception:
                    pass

            elapsed = time.time() - start_time

            if html:
                content = extract_content(html, url)
                print(f"[browser_daemon] ✅ {len(content)}자 추출 ({elapsed:.1f}초)", flush=True)
            else:
                content = f"❌ 페이지를 로드할 수 없습니다: {url}"
                print(f"[browser_daemon] ❌ 로드 실패 ({elapsed:.1f}초)", flush=True)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"content": content, "elapsed": elapsed}).encode("utf-8"))
            return

        if parsed.path == "/screencast":
            self._handle_screencast(parsed)
            return

        if parsed.path == "/screencast/stop":
            global _screencast_stop
            _screencast_stop = True
            # 모든 열린 탭 정리 (OOM 방지)
            _cleanup_stale_tabs()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "stopping"}).encode())
            return

        self.send_response(404)
        self.end_headers()

    def _handle_screencast(self, parsed):
        """SSE 스트리밍 — 전용 탭을 열어 검색 페이지를 캡처"""
        global _screencast_stop
        _screencast_stop = False

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
            deadline = time.time() + 30
            frame_count = 0
            while time.time() < deadline and not _screencast_stop:
                try:
                    # 큐에서 모든 대기 프레임 드레인
                    frames_to_send = []
                    with _screencast_lock:
                        while _screencast_queue:
                            frames_to_send.append(_screencast_queue.popleft())
                    for fdata in frames_to_send:
                        frame_count += 1
                        sse_data = json.dumps(fdata)
                        self.wfile.write(f"data: {sse_data}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(0.15)  # ~6fps 폴링
                except (BrokenPipeError, ConnectionResetError):
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
                "params": {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False}
            }))
            ws.recv()

            # Page.enable
            ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
            ws.recv()

            # 주기적 캡처 (단계적 전환: DuckDuckGo → 실제 페이지 → DuckDuckGo)
            deadline = time.time() + 15
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
                            "params": {"format": "jpeg", "quality": 55}
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
