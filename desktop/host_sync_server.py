"""
Xoul Host Sync Server — VM → Host 파일 동기화 API

VM에서 vm_to_host 호출 시, 이 서버에 HTTP 요청을 보내면
SCP로 파일을 VM에서 호스트 share/로 가져옵니다.

데스크톱 클라이언트(main.py) 시작 시 백그라운드 스레드로 자동 실행됩니다.
"""

import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SYNC_PORT = 3100


class SyncHandler(BaseHTTPRequestHandler):
    """파일 동기화 요청 처리"""

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "service": "xoul-host-sync"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/sync":
            self._handle_sync()
        else:
            self._respond(404, {"error": "not found"})

    def _handle_sync(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            filename = body.get("filename", "")
            vm_path = body.get("vm_path", "")

            if not filename and not vm_path:
                self._respond(400, {"error": "filename or vm_path required"})
                return

            from vm_manager import scp_from_vm, SHARE_DIR

            # VM share/ 경로에서 SCP로 가져오기
            if not vm_path:
                vm_path = f"/root/xoul/share/{filename}"

            if not filename:
                filename = os.path.basename(vm_path)

            local_path = os.path.join(SHARE_DIR, filename)
            os.makedirs(SHARE_DIR, exist_ok=True)

            result = scp_from_vm(vm_path, local_path)

            if "✅" in result or os.path.isfile(local_path):
                size = os.path.getsize(local_path) if os.path.isfile(local_path) else 0
                self._respond(200, {
                    "status": "ok",
                    "filename": filename,
                    "local_path": local_path,
                    "size": size,
                    "message": result
                })
                print(f"[Sync] ✅ {vm_path} → {local_path} ({size} bytes)")
            else:
                self._respond(500, {"error": result})
                print(f"[Sync] ❌ {vm_path}: {result}")

        except ImportError:
            self._respond(500, {"error": "vm_manager not available"})
        except Exception as e:
            self._respond(500, {"error": str(e)})
            print(f"[Sync] ❌ Exception: {e}")

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        # 일반 요청 로그 숨기기 (에러만 표시)
        pass


def start_sync_server(port=SYNC_PORT):
    """동기화 서버 시작 (블로킹)"""
    server = HTTPServer(("0.0.0.0", port), SyncHandler)
    print(f"[Sync Server] 🔄 Listening on port {port}")
    server.serve_forever()


def start_sync_server_thread(port=SYNC_PORT):
    """동기화 서버를 백그라운드 스레드로 시작 (논블로킹)"""
    t = threading.Thread(target=start_sync_server, args=(port,), daemon=True)
    t.start()
    print(f"[Sync Server] 🔄 Background thread started on port {port}")
    return t


if __name__ == "__main__":
    print(f"Xoul Host Sync Server — port {SYNC_PORT}")
    start_sync_server()
