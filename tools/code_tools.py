"""
코드 실행 도구 - Python 코드 실행

환경 자동 감지:
  - VM 내부에서 실행 시: 직접 subprocess로 실행
  - Windows에서 실행 시: SSH로 VM에서 실행
"""
import json as _json
import os
import re
import signal
import subprocess
import tempfile
import time as _time

XOUL_TMP = "/root/.xoul/tmp"
MAX_TIMEOUT = 600  # 최대 허용 타임아웃 (초)
FULL_OUTPUT_PATH = "/root/share/last_code_output.txt"
RUNNING_CODES_PATH = "/root/.xoul/running_codes.json"


def _save_full_output(output: str):
    """긴 코드 실행 결과를 파일로 저장 (LLM이 첨부/참조 가능하도록)"""
    try:
        if _is_in_vm():
            os.makedirs(os.path.dirname(FULL_OUTPUT_PATH), exist_ok=True)
            with open(FULL_OUTPUT_PATH, "w", encoding="utf-8") as f:
                f.write(output)
        else:
            # Windows: SSH로 VM에 저장
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from vm_manager import ssh_exec, ssh_write_file
            ssh_exec("mkdir -p /root/share", quiet=True)
            ssh_write_file(FULL_OUTPUT_PATH, output)
    except Exception:
        pass  # best-effort


def _is_in_vm() -> bool:
    """현재 VM 안에서 실행 중인지 확인"""
    return os.path.exists("/root/xoul") or os.name != "nt"


# ── Running Code PID Tracking ──

def _register_running_code(name: str, pid: int):
    """실행 중인 코드 PID 등록"""
    try:
        entries = _load_running_codes()
        entries.append({
            "name": name,
            "pid": pid,
            "started_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _save_running_codes(entries)
    except Exception:
        pass


def _unregister_running_code(pid: int):
    """코드 종료 시 PID 제거"""
    try:
        entries = _load_running_codes()
        entries = [e for e in entries if e.get("pid") != pid]
        _save_running_codes(entries)
    except Exception:
        pass


def _load_running_codes() -> list:
    """running_codes.json 로드"""
    path = RUNNING_CODES_PATH
    if not _is_in_vm():
        # Windows: VM에서 읽기
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_exec
        result = ssh_exec(f"cat {path} 2>/dev/null", quiet=True)
        try:
            return _json.loads(result) if result.strip() else []
        except Exception:
            return []
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return []


def _save_running_codes(entries: list):
    """running_codes.json 저장"""
    path = RUNNING_CODES_PATH
    data = _json.dumps(entries, ensure_ascii=False, indent=2)
    if not _is_in_vm():
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_exec, ssh_write_file
        ssh_exec(f"mkdir -p {os.path.dirname(path)}", quiet=True)
        ssh_write_file(path, data)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)


def _cleanup_dead_pids(entries: list) -> list:
    """이미 종료된 PID 제거 (좀비 방지)"""
    alive = []
    for e in entries:
        pid = e.get("pid")
        try:
            os.kill(pid, 0)  # 프로세스 존재 확인 (신호 안 보냄)
            alive.append(e)
        except (OSError, ProcessLookupError):
            pass  # 이미 종료됨
    return alive


def list_running_codes() -> str:
    """현재 실행 중인 코드 목록 조회"""
    from i18n import t
    entries = _load_running_codes()
    entries = _cleanup_dead_pids(entries)
    _save_running_codes(entries)

    if not entries:
        return "현재 실행 중인 코드가 없습니다."

    lines = [f"🔄 실행 중인 코드 ({len(entries)}개):"]
    for e in entries:
        lines.append(t("code_tool.process_info", name=e['name'], pid=e['pid'], started_at=e['started_at']))
    return "\n".join(lines)


def stop_running_code(name: str = "") -> str:
    """실행 중인 코드를 중지

    Args:
        name: 중지할 코드 이름 (부분 매칭). 비워두면 실행 중인 코드가 1개일 때 자동 선택.
    """
    entries = _load_running_codes()
    entries = _cleanup_dead_pids(entries)
    _save_running_codes(entries)

    if not entries:
        return "현재 실행 중인 코드가 없습니다."

    # 매칭: substring → 실패 시 토큰 분리 매칭
    if name:
        query = name.lower()
        matched = [e for e in entries if query in e["name"].lower()]
        if not matched:
            # 토큰 분리 매칭: "토론에이전트" → ["토론", "에이전트"] 각각 포함 확인
            import re as _re
            # 한글/영문 단어 단위로 분리
            tokens = _re.findall(r'[가-힣]+|[a-zA-Z]+', query)
            if tokens:
                matched = [
                    e for e in entries
                    if all(t in e["name"].lower() for t in tokens)
                ]
    else:
        matched = entries

    if len(matched) == 0:
        names = ", ".join(e["name"] for e in entries)
        return f"'{name}'와 일치하는 실행 중인 코드가 없습니다.\n현재 실행 중: {names}"

    if len(matched) > 1 and not name:
        lines = ["여러 코드가 실행 중입니다. 이름을 지정해주세요:"]
        for e in matched:
            lines.append(f"  • **{e['name']}** (PID: {e['pid']})")
        return "\n".join(lines)

    # Kill
    target = matched[0]
    pid = target["pid"]
    target_name = target["name"]
    try:
        if _is_in_vm():
            # start_new_session=True로 실행했으므로 pid == pgid
            # killpg로 sh + python3 자식 프로세스 전체 종료
            try:
                os.killpg(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            # 1초 후에도 안 죽으면 SIGKILL
            _time.sleep(1)
            try:
                os.killpg(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass  # 이미 종료됨
        else:
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from vm_manager import ssh_exec
            # kill -TERM/-KILL을 프로세스 그룹 전체에 전송 (음수 PID = PGID)
            ssh_exec(f"kill -- -{pid} 2>/dev/null; sleep 1; kill -9 -- -{pid} 2>/dev/null", quiet=True)

        _unregister_running_code(pid)
        return f"⏹ '{target_name}' 코드를 중지했습니다. (PID: {pid})"
    except Exception as e:
        _unregister_running_code(pid)
        return f"⏹ '{target_name}' 중지 시도 완료 (PID: {pid}, {e})"

def _check_infinite_loop(code: str) -> str | None:
    """무한 루프 패턴 감지. '# arena-loop' 주석이 있으면 허용."""
    # arena-loop 주석이 있으면 게임 루프로 간주, 허용
    if "# arena-loop" in code:
        return None
    patterns = [
        r'while\s+(True|1)\s*:',
        r'while\s+not\s+\w+\s*:',
    ]
    for p in patterns:
        if re.search(p, code):
            return (
                "⚠ Infinite loops (while True) are not allowed — they become zombie processes.\n"
                "Use stop_running_code to stop a running code.\n"
                "For repeated tasks, use create_workflow(name='task', prompts='what to do', schedule='every 5min').\n"
                "If you need a game loop, add a '# arena-loop' comment to your code."
            )
    return None


def run_python_code(code: str, description: str = "", timeout: str = "30", on_output=None) -> str:
    """Python 코드 실행 (환경 자동 감지)

    Args:
        code: 실행할 Python 코드
        description: 코드 설명
        timeout: 실행 제한 시간 (초, 기본 30, 최대 600). 아레나 게임 등 장시간 작업은 600.
        on_output: 실시간 출력 콜백 함수. on_output(line: str) 형태.
                   None이면 기존 방식(subprocess.run), 있으면 Popen 스트리밍.
    """
    # 타임아웃 파싱
    try:
        timeout_sec = min(int(timeout), MAX_TIMEOUT)
    except (ValueError, TypeError):
        timeout_sec = 30

    # 무한 루프 차단
    block_msg = _check_infinite_loop(code)
    if block_msg:
        return block_msg

    if _is_in_vm():
        # VM 내부: 직접 실행 (UTF-8 강제)
        try:
            os.makedirs(XOUL_TMP, exist_ok=True)
            tmp_path = f"{XOUL_TMP}/code.py"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(code)
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"

            if on_output:
                # 스트리밍 모드: Popen + 라인별 실시간 출력
                # start_new_session=True: 새 프로세스 그룹 → killpg로 python3 자식까지 완전 종료 가능
                proc = subprocess.Popen(
                    f"python3 -u '{tmp_path}' 2>&1",
                    shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", env=env,
                    start_new_session=True,
                )
                # PID 등록 (code_name은 description에서 추출)
                code_name = (description.replace("Stored code: ", "") if description.startswith("Stored code: ") else description) or "unnamed"
                _register_running_code(code_name, proc.pid)
                output_lines = []
                start = _time.time()
                try:
                    for line in proc.stdout:
                        line = line.rstrip('\n')
                        output_lines.append(line)
                        try:
                            on_output(line)
                        except Exception:
                            pass
                        if _time.time() - start > timeout_sec:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except (OSError, ProcessLookupError):
                                proc.kill()
                            on_output(f"⏰ Timeout ({timeout_sec}s)")
                            break
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        proc.kill()
                finally:
                    _unregister_running_code(proc.pid)
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                output = "\n".join(output_lines).strip()
                if len(output) > 2000:
                    _save_full_output(output)
                    output = output[:5000] + f"\n... (truncated, full output saved to /root/share/last_code_output.txt)"
                return output or "(completed, no output)"
            else:
                # 기존 모드: subprocess.run (메신저/호환용)
                result = subprocess.run(
                    f"python3 '{tmp_path}' 2>&1",
                    shell=True, capture_output=True, text=True, timeout=timeout_sec,
                    encoding="utf-8", env=env
                )
                output = (result.stdout + result.stderr).strip()
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                if len(output) > 2000:
                    _save_full_output(output)
                    output = output[:5000] + f"\n... (truncated, full output saved to /root/share/last_code_output.txt)"
                return output or "(completed, no output)"
        except subprocess.TimeoutExpired:
            return f"Error: code execution timed out ({timeout_sec}s)"
        except Exception as e:
            return f"Error: {e}"
    else:
        # Windows: SSH 경유 (스트리밍 미지원)
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_exec, ssh_write_file, is_vm_running
        if not is_vm_running():
            return "Error: Ubuntu VM is not running."
        tmp_path = f"{XOUL_TMP}/code.py"
        ssh_exec(f"mkdir -p {XOUL_TMP}", quiet=True)
        ssh_write_file(tmp_path, code)
        result = ssh_exec(f"PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python3 '{tmp_path}' 2>&1", timeout=timeout_sec)
        ssh_exec(f"rm -f '{tmp_path}'", quiet=True)
        if len(result) > 2000:
            _save_full_output(result)
            result = result[:5000] + f"\n... (truncated, full output saved to /root/share/last_code_output.txt)"
        return result or "(completed, no output)"


def _ensure_used_by_column(conn):
    """codes 테이블에 used_by 컬럼이 없으면 추가 (DB 마이그레이션)"""
    try:
        conn.execute("SELECT used_by FROM codes LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE codes ADD COLUMN used_by TEXT DEFAULT '[]'")
        conn.commit()


def update_code_used_by(code_name: str, wf_name: str, action: str = "add") -> bool:
    """코드의 used_by 배열에서 워크플로우 참조를 추가/제거
    action: "add" | "remove"
    Returns: 성공 여부
    """
    import sqlite3
    import json

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    if not os.path.isfile(db_path):
        return False

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _ensure_used_by_column(conn)
        row = conn.execute("SELECT used_by FROM codes WHERE name = ?", (code_name,)).fetchone()
        if not row:
            conn.close()
            return False

        used_by = json.loads(row["used_by"] or "[]")

        if action == "add":
            if wf_name not in used_by:
                used_by.append(wf_name)
        elif action == "remove":
            used_by = [w for w in used_by if w != wf_name]

        conn.execute("UPDATE codes SET used_by = ? WHERE name = ?",
                      (json.dumps(used_by, ensure_ascii=False), code_name))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def list_codes() -> str:
    """Import된 코드 목록 조회 (CODE_DATA 태그 포함)"""
    import sqlite3
    import json
    import base64
    import os
    from i18n import t

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    if not os.path.isfile(db_path):
        return t("codes.empty")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _ensure_used_by_column(conn)
        try:
            rows = conn.execute(
                "SELECT name, description, category, code, params, used_by FROM codes ORDER BY created DESC"
            ).fetchall()
        except Exception:
            conn.close()
            return t("codes.empty_no_table")
        conn.close()

        if not rows:
            return t("codes.empty")

        # Markdown table
        lines = [t("codes.list_header", count=len(rows))]
        lines.append("")
        lines.append(f"| # | {t('codes.th_name')} | {t('codes.th_category')} | {t('codes.th_params')} | {t('codes.th_desc')} | {t('codes.th_used_by')} |")
        lines.append("|---|------|----------|--------|------|------|")

        items = []
        for i, r in enumerate(rows, 1):
            desc = (r["description"] or "")[:30]
            raw_params = json.loads(r["params"]) if r["params"] else []
            # params가 list of dict 또는 list of str일 수 있음
            param_list = []
            if isinstance(raw_params, list):
                for p in raw_params:
                    if isinstance(p, dict):
                        param_list.append(p)
                    elif isinstance(p, str):
                        param_list.append({"name": p, "type": "str", "desc": p})
            category = r["category"] or "Other"
            # used_by
            used_by = json.loads(r["used_by"] or "[]") if r["used_by"] else []
            used_by_str = ", ".join(f"🔗{w}" for w in used_by) if used_by else "-"
            # Param 요약: *required, default 있으면 optional
            if param_list:
                param_strs = []
                for p in param_list:
                    pname = p.get("name", "?")
                    if "default" not in p:
                        param_strs.append(f"*{pname}")
                    else:
                        param_strs.append(f"{pname}={p['default']}")
                param_summary = ", ".join(param_strs)
            else:
                param_summary = "-"
            lines.append(f"| {i} | {r['name']} | {category} | {param_summary} | {desc} | {used_by_str} |")
            items.append({
                "name": r["name"],
                "description": desc,
                "category": category,
                "param_count": len(param_list),
                "params": [{"name": p.get("name"), "type": p.get("type", "str"), "desc": p.get("desc", ""), "default": p.get("default")} for p in param_list],
                "used_by": used_by,
            })

        # Desktop UI용 base64 데이터
        payload = {
            "_meta": {"total": len(rows)},
            "items": items,
        }
        data_json = json.dumps(payload, ensure_ascii=False)
        data_b64 = base64.b64encode(data_json.encode()).decode()
        lines.append(f"\n<!--CODE_DATA:{data_b64}-->")
        lines.append(f"\n[{t('codes.pass_through')}]")

        return "\n".join(lines)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return t("codes.error", error=str(e))


def delete_code(name: str) -> str:
    """Import된 코드 삭제 — used_by 참조 있으면 차단"""
    import sqlite3
    import json
    import os
    from i18n import t

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    if not os.path.isfile(db_path):
        return t("codes.empty")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _ensure_used_by_column(conn)
        # 정확한 이름 매칭
        row = conn.execute("SELECT name, used_by FROM codes WHERE name = ?", (name,)).fetchone()
        if not row:
            # 부분 매칭
            row = conn.execute("SELECT name, used_by FROM codes WHERE name LIKE ?", (f"%{name}%",)).fetchone()
        if not row:
            conn.close()
            return t("codes.not_found", name=name)

        actual_name = row["name"]
        # used_by 참조 확인
        used_by = json.loads(row["used_by"] or "[]") if row["used_by"] else []
        if used_by:
            conn.close()
            return t("codes.used_by_block", name=actual_name, count=len(used_by), workflows=", ".join(used_by))

        conn.execute("DELETE FROM codes WHERE name = ?", (actual_name,))
        conn.commit()
        conn.close()
        return t("codes.deleted", name=actual_name)
    except Exception as e:
        return t("codes.error", error=str(e))


def create_code(name: str, code: str, description: str = "", category: str = "Other", params: str = "[]") -> str:
    """새 코드를 Code Store DB에 저장

    Args:
        name: 코드 이름
        code: Python 코드
        description: 설명
        category: 카테고리 (Other, Finance, System 등)
        params: JSON 파라미터 정의 (예: [{"name":"text","type":"str","desc":"입력 텍스트"}])
    """
    import sqlite3
    import json
    import os
    import ast
    import inspect
    from i18n import t

    # params에 default 누락 시 코드의 함수 시그니처에서 자동 추출
    try:
        parsed_params = json.loads(params) if isinstance(params, str) else params
        if isinstance(parsed_params, list) and code:
            # def run(...) 시그니처에서 기본값 추출
            sig_defaults = {}
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name == "run":
                        args = node.args
                        # defaults는 뒤에서부터 매핑
                        num_defaults = len(args.defaults)
                        arg_names = [a.arg for a in args.args]
                        for i, default_node in enumerate(args.defaults):
                            arg_idx = len(arg_names) - num_defaults + i
                            arg_name = arg_names[arg_idx]
                            # default 값 추출
                            try:
                                default_val = ast.literal_eval(default_node)
                            except (ValueError, TypeError):
                                default_val = ""
                            sig_defaults[arg_name] = default_val
                        break
            except Exception:
                pass
            # params에 default 없는 항목에 시그니처 기본값 추가
            if sig_defaults:
                for p in parsed_params:
                    if isinstance(p, dict) and "default" not in p:
                        pname = p.get("name", "")
                        if pname in sig_defaults:
                            p["default"] = sig_defaults[pname]
                params = json.dumps(parsed_params, ensure_ascii=False)
    except Exception:
        pass

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    try:
        conn = sqlite3.connect(db_path)
        # codes 테이블 없으면 생성
        conn.execute("""CREATE TABLE IF NOT EXISTS codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            code TEXT DEFAULT '',
            params TEXT DEFAULT '[]',
            category TEXT DEFAULT 'Other',
            created TEXT DEFAULT '',
            used_by TEXT DEFAULT '[]',
            embedding BLOB
        )""")
        _ensure_used_by_column(conn)
        # embedding 컬럼 없으면 추가 (기존 DB 마이그레이션)
        try:
            conn.execute("SELECT embedding FROM codes LIMIT 1")
        except Exception:
            conn.execute("ALTER TABLE codes ADD COLUMN embedding BLOB")
            conn.commit()
        # 중복 체크
        existing = conn.execute("SELECT name FROM codes WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.close()
            return t("codes.already_exists", name=name)

        # 임베딩 생성 (이름 + 설명)
        embedding = None
        try:
            from tools.workflow_tools import _get_embed
            embed_text = f"{name} {description}" if description else name
            embedding = _get_embed(embed_text)
        except Exception:
            pass

        from datetime import datetime
        conn.execute(
            "INSERT INTO codes (name, description, code, params, category, created, used_by, embedding) VALUES (?, ?, ?, ?, ?, ?, '[]', ?)",
            (name, description, code, params, category, datetime.now().isoformat(), embedding)
        )
        conn.commit()
        conn.close()

        # 다른 테이블 누락 embedding도 채우기
        try:
            from tools.workflow_tools import _fill_missing_embeddings_async
            _fill_missing_embeddings_async()
        except Exception:
            pass

        return t("codes.created", name=name)
    except Exception as e:
        return t("codes.error", error=str(e))


def run_stored_code(name: str, params: str = "{}", timeout: str = "30", on_output=None) -> str:
    """Import된 코드를 파라미터와 함께 실행

    IMPORTANT: You MUST scan the entire conversation for values relevant to this code's parameters and pass them in `params`.
    Each code has defined parameters (visible via list_codes). Match conversation context to those parameter names.
    If the user mentioned or implied ANY value that corresponds to a parameter, include it in params — do NOT rely on code defaults.
    Code default values are FALLBACKS only. If conversation contains a matching value, ALWAYS override.
    NEVER call with empty params when the conversation contains values matching expected parameters.

    Args:
        name: 실행할 코드 이름 (codes 테이블에서 검색)
        params: JSON 문자열로 된 파라미터. 대화에서 언급된 관련 값을 반드시 포함할 것.
        timeout: 실행 제한 시간 (초, 기본 30, 최대 600)
        on_output: 실시간 출력 콜백 (옵션)
    """
    import sqlite3
    import json
    import os
    from i18n import t

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    if not os.path.isfile(db_path):
        return t("codes.no_db")

    # DB에서 코드 검색
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # 1) 정확한 이름 매칭
        row = conn.execute("SELECT * FROM codes WHERE name = ?", (name,)).fetchone()
        if not row:
            # 2) 부분 매칭 (LIKE)
            row = conn.execute("SELECT * FROM codes WHERE name LIKE ?", (f"%{name}%",)).fetchone()
        if not row:
            # 3) Embedding 유사도 검색
            try:
                from tools.workflow_tools import _get_embed, _cosine_sim
                # embedding 컬럼 확인
                try:
                    conn.execute("SELECT embedding FROM codes LIMIT 1")
                except Exception:
                    conn.execute("ALTER TABLE codes ADD COLUMN embedding BLOB")
                    conn.commit()
                q_emb = _get_embed(name)
                if q_emb:
                    emb_rows = conn.execute("SELECT name, embedding FROM codes WHERE embedding IS NOT NULL").fetchall()
                    best_name, best_score = None, 0.0
                    for er in emb_rows:
                        if er["embedding"]:
                            try:
                                sim = _cosine_sim(q_emb, er["embedding"])
                                if sim > best_score:
                                    best_score = sim
                                    best_name = er["name"]
                            except Exception:
                                continue
                    if best_name and best_score >= 0.75:
                        row = conn.execute("SELECT * FROM codes WHERE name = ?", (best_name,)).fetchone()
            except Exception:
                pass
        if not row:
            conn.close()
            available = list_codes()
            return t("codes.not_found_with_list", name=name) + f"\n\n{available}"
        code = row["code"]
        db_params_raw = row["params"] if "params" in row.keys() else "[]"
        conn.close()
    except Exception as e:
        return t("codes.query_error", error=str(e))

    if not code:
        return t("codes.code_empty", name=name)

    # 파라미터 파싱
    try:
        if isinstance(params, str):
            param_dict = json.loads(params) if params.strip() else {}
        else:
            param_dict = params
    except (json.JSONDecodeError, TypeError):
        param_dict = {}

    # ── 빈 파라미터 감지: 사용자에게 입력 요청 ──
    try:
        db_params = json.loads(db_params_raw) if db_params_raw else []
        if isinstance(db_params, list):
            missing_params = []
            for p in db_params:
                if not isinstance(p, dict):
                    continue
                pname = p.get("name", "")
                pdesc = p.get("desc", pname)
                has_default = "default" in p
                default_val = p.get("default")
                user_provided = pname in param_dict and param_dict[pname] not in ("", None)
                # 필수(no default) 또는 default가 빈 문자열인 파라미터
                if not user_provided and (not has_default or default_val == ""):
                    missing_params.append({"name": pname, "desc": pdesc})
            if missing_params:
                # ── LTM에서 자동 조회: (1) 정확 키 매칭 → (2) 임베딩 유사도 검색 ──
                try:
                    import sqlite3 as _sq
                    _mem_db = os.path.expanduser("~/.xoul/memory.db")
                    if os.path.isfile(_mem_db):
                        _mconn = _sq.connect(_mem_db)
                        _still_missing = []
                        for mp in missing_params:
                            _ltm_key = f"{name} {mp['name']}"
                            # Step 1: 정확 키 매칭
                            _row = _mconn.execute(
                                "SELECT value FROM ltm WHERE key = ?", (_ltm_key,)
                            ).fetchone()
                            if _row and _row[0]:
                                param_dict[mp["name"]] = _row[0]
                                print(f"[AUTO-FILL] {mp['name']} from LTM (exact)", flush=True)
                            else:
                                # Step 2: 임베딩 유사도 검색
                                _found = False
                                try:
                                    from tools.memory_tools import _get_embedding, _cosine_sim
                                    _q_emb = _get_embedding(mp["name"])
                                    if _q_emb:
                                        _all = _mconn.execute(
                                            "SELECT key, value, embedding FROM ltm WHERE embedding IS NOT NULL"
                                        ).fetchall()
                                        _best_val, _best_score = None, 0.6  # 최소 유사도 임계값
                                        for _r in _all:
                                            try:
                                                _sim = _cosine_sim(_q_emb, _r[2])
                                                if _sim > _best_score:
                                                    _best_score = _sim
                                                    _best_val = _r[1]
                                            except Exception:
                                                continue
                                        if _best_val:
                                            param_dict[mp["name"]] = _best_val
                                            _found = True
                                            print(f"[AUTO-FILL] {mp['name']} from LTM (similarity={_best_score:.2f})", flush=True)
                                except Exception:
                                    pass
                                if not _found:
                                    _still_missing.append(mp)
                        _mconn.close()
                        missing_params = _still_missing
                except Exception:
                    pass

            if missing_params:
                param_lines = "\n".join(f"  • **{mp['name']}**: {mp['desc']}" for mp in missing_params)
                # 구조적 신호: 서버/데스크톱이 확정적으로 감지
                import json as _mj
                _meta = _mj.dumps({"code_name": name, "params": missing_params}, ensure_ascii=False)
                return (
                    f"__NEEDS_INPUT__{_meta}__END_NEEDS_INPUT__\n"
                    f"⚠ '{name}' 코드를 실행하려면 다음 파라미터를 입력해주세요:\n"
                    f"{param_lines}\n\n"
                    f"사용자에게 위 값들을 물어본 후 다시 run_stored_code를 호출하세요."
                )
    except Exception:
        pass

    # 코드 형식 감지: def run( 시그니처가 있으면 함수 호출 방식
    import re as _re
    has_run_func = bool(_re.search(r'^def\s+run\s*\(', code, _re.MULTILINE))

    if has_run_func:
        # ── 함수 호출 방식 (def run(params): 시그니처) ──
        # 함수 정의 뒤에 호출 코드 추가
        call_args = ", ".join(
            f"{k}={repr(v)}" for k, v in param_dict.items()
        ) if param_dict else ""
        code = code + f"\n\n# ── Auto Call ──\n_result = run({call_args})\nif _result is not None:\n    print(_result)\n"
    else:
        # ── 기존 변수 주입 방식 (플랫 코드) ──
        if param_dict:
            inject_lines = ["# ── Injected Parameters ──"]
            for k, v in param_dict.items():
                if isinstance(v, str):
                    # 문자열 내 따옴표 이스케이프
                    escaped = v.replace("\\", "\\\\").replace('"', '\\"')
                    inject_lines.append(f'{k} = "{escaped}"')
                else:
                    inject_lines.append(f"{k} = {repr(v)}")
            # 환경변수로도 설정 (os.environ 방식 호환)
            inject_lines.append("import os as _os")
            for k, v in param_dict.items():
                inject_lines.append(f'_os.environ["{k.upper()}"] = str({k})')
            inject_lines.append("# ── End Parameters ──\n")
            code = "\n".join(inject_lines) + "\n" + code

    return run_python_code(code, description=f"Stored code: {name}", timeout=timeout, on_output=on_output)


def share_to_store(share_type: str, name: str, category: str = "Other") -> str:
    """로컬 코드/워크플로우/페르소나를 Xoul Store에 공유 (GitHub PR 생성)

    Args:
        share_type: 공유 타입 — "code", "workflow", "persona" 중 하나
        name: 공유할 아이템의 이름
        category: 스토어 카테고리 (기본 "Other")
    """
    import sqlite3, json, os, urllib.request, time
    from i18n import t

    db_path = os.path.expanduser("~/.xoul/workflows.db")
    if not os.path.isfile(db_path):
        return "❌ DB not found"

    # 1) DB에서 아이템 조회
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    table = {"code": "codes", "workflow": "workflows", "persona": "personas"}.get(share_type)
    if not table:
        conn.close()
        return f"❌ Unknown type: {share_type}"

    row = conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,)).fetchone()
    conn.close()
    if not row:
        return f"❌ '{name}' not found"

    item = dict(row)
    import re as _re
    ascii_name = _re.sub(r'[^\x00-\x7F]', '', name).strip().lower().replace(" ", "_").replace("/", "_")
    if not ascii_name:
        ascii_name = f"{share_type}_item"
    item_id = ascii_name[:40] + f"_{int(time.time()) % 10000}"

    # 2) ShareRequest 구성
    # 작성자 이름 읽기 (config.json → user.name)
    author = "Xoul"
    try:
        cfg_paths = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
            os.path.expanduser("~/.xoul/config.json"),
        ]
        for cfg_path in cfg_paths:
            if os.path.isfile(cfg_path):
                cfg = json.loads(open(cfg_path, encoding="utf-8-sig").read())
                uname = cfg.get("user", {}).get("name", "")
                if uname:
                    author = uname
                    break
    except Exception:
        pass

    payload = {
        "type": share_type,
        "id": item_id,
        "name": name,
        "description": item.get("description", ""),
        "category": category,
        "author": author,
    }

    if share_type == "code":
        payload["code"] = item.get("code", "")
        params = item.get("params", "[]")
        if isinstance(params, str):
            try:
                payload["params"] = json.loads(params)
            except Exception:
                payload["params"] = []
        else:
            payload["params"] = params
    elif share_type == "workflow":
        steps = item.get("steps", "[]")
        if isinstance(steps, str):
            try:
                payload["steps"] = json.loads(steps)
            except Exception:
                payload["steps"] = []
        else:
            payload["steps"] = steps
        payload["schedule"] = item.get("schedule", "")
    elif share_type == "persona":
        payload["prompt"] = item.get("prompt", "")

    # 3) 웹 서버 /api/share 호출
    try:
        # config.json에서 web backend URL 읽기
        cfg_paths = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
            os.path.expanduser("~/.xoul/config.json"),
        ]
        backend_url = "http://localhost:8080"
        for cfg_path in cfg_paths:
            if os.path.isfile(cfg_path):
                try:
                    cfg = json.loads(open(cfg_path, encoding="utf-8-sig").read())
                    url = cfg.get("web", {}).get("backend_url", "")
                    if url:
                        backend_url = url.rstrip("/")
                        break
                except Exception:
                    pass

        url = f"{backend_url}/api/share"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())

        if result.get("ok"):
            pr_url = result.get("pr_url", "")
            return f"✅ Store 공유 완료!\n📋 PR: {pr_url}"
        else:
            return f"⚠ 공유 실패: {result}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()
        except Exception:
            detail = str(e)
        return f"⚠ 공유 실패: {e.code} — {detail}"
    except Exception as e:
        return f"⚠ 공유 실패: {e}"
