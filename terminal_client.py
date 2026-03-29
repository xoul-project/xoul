"""
Xoul 터미널 클라이언트
— REST API (server.py)에 연결하여 대화합니다.

사용법:
    python terminal_client.py [--config config.json]
"""
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error

# i18n 초기화
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
from i18n import t, init_from_config


def _spinner(stop_event, msg=None):
    """대기 중 스피너 표시"""
    if msg is None:
        msg = t("terminal.thinking").strip()
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f"\r  {chars[i % len(chars)]} {msg}...")
        sys.stdout.flush()
        i += 1
        time.sleep(0.1)
    sys.stdout.write("\r" + " " * 40 + "\r")  # 줄 지우기
    sys.stdout.flush()


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")

    if len(sys.argv) > 2 and sys.argv[1] == "--config":
        config_path = sys.argv[2]

    with open(config_path, "r", encoding="utf-8-sig") as f:
        config = json.load(f)

    # i18n 초기화 (config.json의 assistant.language 기반)
    init_from_config(config)

    port = config.get("server", {}).get("port", 3000)
    api_key = config.get("server", {}).get("api_key", "")
    base_url = f"http://127.0.0.1:{port}"

    # 연결 확인
    try:
        req = urllib.request.Request(f"{base_url}/status")
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = json.loads(resp.read().decode())
        print(t("terminal.connected", provider=status.get("llm_provider", "?")))
    except Exception as e:
        print(t("terminal.connect_fail", url=base_url))
        print(t("terminal.connect_error", error=e))
        print(t("terminal.connect_check_vm"))
        sys.exit(1)

    session_id = None
    debug_mode = False
    print()
    print(t("terminal.banner_title"))
    print(t("terminal.banner_sep"))
    print(t("terminal.banner_hint"))
    print()

    # 접속 시 미읽 알림 확인
    try:
        noti_req = urllib.request.Request(
            f"{base_url}/notifications",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(noti_req, timeout=5) as resp:
            noti_data = json.loads(resp.read().decode())
        unread = noti_data.get("unread_count", 0)
        if unread > 0:
            print(t("terminal.unread_noti", count=unread))
            print()
    except Exception:
        pass

    while True:
        try:
            user_input = input("🤖 Xoul > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + t("terminal.goodbye"))
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "종료"):
            print(t("terminal.goodbye"))
            break
        if user_input.lower() == "debug":
            debug_mode = not debug_mode
            print(t("terminal.debug_mode", state="ON" if debug_mode else "OFF"))
            continue

        # /알림 명령어 — 미읽 알림 확인
        if user_input in ("/알림", "/notifications"):
            try:
                noti_req = urllib.request.Request(
                    f"{base_url}/notifications",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(noti_req, timeout=10) as resp:
                    noti_data = json.loads(resp.read().decode())
                items = noti_data.get("notifications", [])
                if not items:
                    print(t("terminal.noti_empty"))
                else:
                    print(t("terminal.noti_list", count=len(items)))
                    print("  ───────────────────────────────────")
                    for i, n in enumerate(items, 1):
                        print(f"  [{i}] ⏰ {n.get('executed_at', '?')}")
                        print(t("terminal.noti_action", action=n.get("action", "?")))
                        result_lines = n.get("result", "").split("\n")
                        for line in result_lines:
                            print(f"      {line}")
                        print()
                    # 모두 읽음 처리
                    mark_req = urllib.request.Request(
                        f"{base_url}/notifications/read",
                        data=b"{}",
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        method="POST",
                    )
                    urllib.request.urlopen(mark_req, timeout=5)
                    print(t("terminal.noti_read_done"))
            except Exception as e:
                print(t("terminal.noti_read_fail", error=e))
            print()
            continue

        # SSE 스트리밍 요청
        payload = json.dumps({
            "message": user_input,
            "session_id": session_id,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/chat/stream",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=180)
            sys.stdout.write(t("terminal.thinking"))
            sys.stdout.flush()

            buffer = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue

                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                evt_type = event.get("type", "")

                if evt_type == "thinking":
                    attempt = event.get("attempt", 0)
                    label = t("terminal.attempt_label", num=attempt + 1) if debug_mode and attempt > 0 else ""
                    sys.stdout.write("\r" + t("terminal.llm_waiting", label=label))
                    sys.stdout.flush()

                elif evt_type == "reasoning":
                    reasoning = event.get("content", "")
                    if reasoning:
                        sys.stdout.write("\r" + " " * 40 + "\r")
                        print(t("terminal.reasoning_header"))
                        for rline in reasoning.strip().split("\n"):
                            print(f"     {rline}")
                        sys.stdout.write(t("terminal.generating"))
                        sys.stdout.flush()

                elif evt_type == "tool_start":
                    tool = event.get("tool", "?")
                    args = event.get("args", {})
                    sys.stdout.write("\r" + t("terminal.tool_running", tool=tool) + "\n")
                    sys.stdout.flush()
                    if debug_mode and args:
                        args_str = json.dumps(args, ensure_ascii=False)
                        print(f"  🐛 args: {args_str[:200]}")

                elif evt_type == "tool_result":
                    tool = event.get("tool", "?")
                    result = event.get("result", "")
                    lines = result.strip().split("\n")
                    # run_command/run_python_code: 마지막 줄만 한 줄로 갱신 표시
                    if tool in ("run_command", "run_python_code"):
                        try:
                            cols = os.get_terminal_size().columns - 4
                        except OSError:
                            cols = 76
                        last = lines[-1].strip() if lines else ""
                        if len(last) > cols:
                            last = last[:cols-3] + "..."
                        sys.stdout.write(f"\r  📋 {last}" + " " * max(0, cols - len(last) - 3))
                        sys.stdout.flush()
                        sys.stdout.write("\n" + t("terminal.analyzing"))
                    else:
                        for l in lines:
                            print(f"  📋 {l}")
                        sys.stdout.write(t("terminal.analyzing"))
                    sys.stdout.flush()

                elif evt_type == "error":
                    sys.stdout.write("\r" + " " * 40 + "\r")
                    print(f"\n  {event.get('content', t('terminal.error_event').strip())}")

                elif evt_type == "final":
                    sys.stdout.write("\r" + " " * 40 + "\r")
                    sys.stdout.flush()
                    session_id = event.get("session_id", session_id)
                    content = event.get("content", "")
                    tool_calls = event.get("tool_calls", [])
                    if debug_mode and tool_calls:
                        print(t("terminal.debug_tool_calls", count=len(tool_calls)))
                    if content:
                        print(f"\n  {content}")

            resp.close()

        except urllib.error.HTTPError as e:
            sys.stdout.write("\r" + " " * 40 + "\r")
            body = e.read().decode() if e.fp else ""
            print(t("terminal.llm_error", code=e.code, body=body[:200]))
        except urllib.error.URLError as e:
            sys.stdout.write("\r" + " " * 40 + "\r")
            print(t("terminal.connect_url_error", reason=e.reason))
        except Exception as e:
            sys.stdout.write("\r" + " " * 40 + "\r")
            print(t("terminal.general_error", error=e))

        print()


if __name__ == "__main__":
    main()
