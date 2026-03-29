"""
메타 도구 - 동적 도구 생성/관리

LLM이 런타임에 새 도구를 만들어 등록할 수 있게 합니다.
필요한 Linux 패키지를 자동 설치하고, 셸 명령어나 Python 코드를
래핑하여 도구로 등록합니다.
"""

import json
import os
from i18n import t as _t

# 동적 도구 저장 경로
CUSTOM_TOOLS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vm", "custom_tools.json"
)


def _load_custom_tools() -> list:
    """저장된 커스텀 도구 목록 로드"""
    if os.path.isfile(CUSTOM_TOOLS_FILE):
        try:
            with open(CUSTOM_TOOLS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_custom_tools(tools: list):
    """커스텀 도구 목록 저장"""
    os.makedirs(os.path.dirname(CUSTOM_TOOLS_FILE), exist_ok=True)
    with open(CUSTOM_TOOLS_FILE, "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)


def create_tool(name: str, description: str, command_template: str,
                packages: str = "", parameters: str = "") -> str:
    """
    새 도구를 동적으로 생성하여 등록합니다.
    
    Args:
        name: 도구 이름 (영문 소문자+언더스코어, 예: "resize_image")
        description: 도구 설명 (한글 OK)
        command_template: 실행할 셸 명령어 템플릿.
            파라미터는 {param_name} 형식으로 삽입.
            예: "convert {input} -resize {size} {output}"
        packages: 필요한 apt 패키지 (공백 구분, 예: "imagemagick ffmpeg")
        parameters: 파라미터 설명 (쉼표 구분, 예: "input:입력파일,size:크기,output:출력파일")
    
    Returns:
        생성 결과 메시지
    """
    from . import TOOLS, TOOL_DESCRIPTIONS
    from .system_tools import tool_run_command
    
    # 이름 검증
    if not name.replace("_", "").isalpha():
        return _t("meta.name_invalid", name=name)
    
    if name in TOOLS:
        return _t("meta.already_exists", name=name)
    
    # 1. 필요한 패키지 설치
    if packages.strip():
        pkg_list = packages.strip().split()
        print(_t("meta.pkg_installing", packages=", ".join(pkg_list)))
        install_result = tool_run_command(f"apt-get install -y {' '.join(pkg_list)}")
        if "ERROR" in install_result.upper():
            return _t("meta.pkg_install_fail", result=install_result)
        print(_t("meta.pkg_installed"))
    
    # 2. 파라미터 파싱
    param_info = {}
    if parameters.strip():
        for p in parameters.split(","):
            p = p.strip()
            if ":" in p:
                pname, pdesc = p.split(":", 1)
                param_info[pname.strip()] = pdesc.strip()
            else:
                param_info[p.strip()] = p.strip()
    
    # 3. 래퍼 함수 생성
    def tool_wrapper(args: dict, _cmd=command_template, _params=param_info) -> str:
        cmd = _cmd
        for pname in _params:
            value = args.get(pname, "")
            # 셸 인젝션 기본 방지
            value = str(value).replace(";", "").replace("&", "").replace("|", "")
            cmd = cmd.replace(f"{{{pname}}}", value)
        return tool_run_command(cmd)
    
    # 4. TOOLS에 등록
    TOOLS[name] = tool_wrapper
    
    # 5. TOOL_DESCRIPTIONS 업데이트
    param_str = ", ".join(f'"{k}": "{v}"' for k, v in param_info.items())
    new_desc = f"\n- {name}: {description}. 파라미터: {{{param_str}}}" if param_info else f"\n- {name}: {description}. 파라미터: 없음"
    
    # 모듈 레벨 TOOL_DESCRIPTIONS에 추가
    import tools as tools_module
    tools_module.TOOL_DESCRIPTIONS += f"\n## 커스텀 도구{new_desc}" if "커스텀 도구" not in tools_module.TOOL_DESCRIPTIONS else new_desc
    
    # 6. 영속 저장 (다음 재시작에도 유지)
    custom_tools = _load_custom_tools()
    custom_tools.append({
        "name": name,
        "description": description,
        "command_template": command_template,
        "packages": packages,
        "parameters": parameters,
    })
    _save_custom_tools(custom_tools)
    
    return _t("meta.tool_created", name=name, description=description, command=command_template, params=param_info or _t("meta.no_params"))


def list_custom_tools() -> str:
    """등록된 커스텀 도구 목록 조회"""
    tools = _load_custom_tools()
    if not tools:
        return "등록된 커스텀 도구가 없습니다."
    
    lines = ["📦 커스텀 도구 목록:\n"]
    for t in tools:
        lines.append(f"  - {t['name']}: {t['description']}")
        lines.append(f"    명령어: {t['command_template']}")
        if t.get('packages'):
            lines.append(f"    패키지: {t['packages']}")
    return "\n".join(lines)


def remove_tool(name: str) -> str:
    """커스텀 도구 제거"""
    from . import TOOLS
    
    if name not in TOOLS:
        return f"도구 '{name}'을 찾을 수 없습니다."
    
    # 기본 도구는 삭제 불가
    builtin = {"fetch_url", "web_search", "read_file", "write_file", "list_files",
               "run_command", "get_datetime", "calculate", "run_python_code",
               "create_tool", "list_custom_tools", "remove_tool"}
    if name in builtin:
        return f"기본 도구 '{name}'은 삭제할 수 없습니다."
    
    del TOOLS[name]
    
    # 영속 저장에서도 제거
    custom_tools = _load_custom_tools()
    custom_tools = [t for t in custom_tools if t["name"] != name]
    _save_custom_tools(custom_tools)
    
    return f"✅ 도구 '{name}' 제거 완료"


def restore_custom_tools():
    """저장된 커스텀 도구들을 시작 시 복원"""
    from . import TOOLS
    from .system_tools import tool_run_command
    
    tools = _load_custom_tools()
    restored = 0
    
    for t in tools:
        name = t["name"]
        if name in TOOLS:
            continue
        
        # 파라미터 파싱
        param_info = {}
        if t.get("parameters", "").strip():
            for p in t["parameters"].split(","):
                p = p.strip()
                if ":" in p:
                    pname, pdesc = p.split(":", 1)
                    param_info[pname.strip()] = pdesc.strip()
                else:
                    param_info[p.strip()] = p.strip()
        
        # 래퍼 함수 생성
        cmd_template = t["command_template"]
        def tool_wrapper(args, _cmd=cmd_template, _params=param_info):
            cmd = _cmd
            for pname in _params:
                value = str(args.get(pname, "")).replace(";", "").replace("&", "").replace("|", "")
                cmd = cmd.replace(f"{{{pname}}}", value)
            return tool_run_command(cmd)
        
        TOOLS[name] = tool_wrapper
        restored += 1
    
    if restored > 0:
        print(f"  🔧 커스텀 도구 {restored}개 복원됨")


# ─────────────────────────────────────────────
# 스킬 자동 축적 (진화 시스템)
# ─────────────────────────────────────────────

SKILLS_DIR = "/root/.xoul/skills"


def evolve_skill(name: str, description: str, trigger_keywords: str,
                 method: str, packages: str = "", script: str = "") -> str:
    """
    성공한 작업을 재사용 가능한 스킬로 저장합니다.
    다음에 비슷한 요청이 오면 이 스킬을 사용합니다.

    Args:
        name: 스킬 이름 (영문 소문자+언더스코어)
        description: 스킬 설명 (한글 OK)
        trigger_keywords: 이 스킬을 발동시킬 키워드들 (쉼표 구분)
        method: 실행 방법 설명 (다음에 재현할 수 있도록 상세히)
        packages: 필요한 패키지 (apt:pkg1,pkg2 pip:pkg3,pkg4)
        script: 실행 스크립트 내용 (있으면 파일로 저장)

    Returns:
        저장 결과 메시지
    """
    from .system_tools import tool_run_command
    from .memory_tools import _get_db, _get_embedding, remember
    from datetime import datetime

    # 디렉토리 생성
    tool_run_command(f"mkdir -p {SKILLS_DIR}")

    now = datetime.now().strftime("%Y-%m-%d")
    keywords_str = ", ".join(k.strip() for k in trigger_keywords) if isinstance(trigger_keywords, list) else ", ".join(k.strip() for k in trigger_keywords.split(","))

    # 스크립트 저장 (있으면)
    script_path = ""
    if script.strip():
        ext = ".py" if "import " in script or "def " in script else ".sh"
        script_path = f"{SKILLS_DIR}/{name}{ext}"
        escaped_script = script.replace("'", "'\\''")
        tool_run_command(f"cat > {script_path} << 'SKILL_SCRIPT_EOF'\n{escaped_script}\nSKILL_SCRIPT_EOF")
        if ext == ".sh":
            tool_run_command(f"chmod +x {script_path}")

    # 임베딩 생성
    embed = _get_embedding(f"{name} {description} {trigger_keywords}")

    # SQLite에 upsert
    try:
        db = _get_db()
        existing = db.execute("SELECT success_count FROM skills WHERE name = ?", (name,)).fetchone()
        prev_count = existing["success_count"] if existing else 0

        db.execute("""
            INSERT INTO skills (name, description, trigger_keywords, method, packages, script, embedding, created, last_used, success_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                trigger_keywords = excluded.trigger_keywords,
                method = excluded.method,
                packages = excluded.packages,
                script = excluded.script,
                embedding = excluded.embedding,
                last_used = excluded.last_used,
                success_count = success_count + 1
        """, (name, description, keywords_str, method[:500], packages, script_path, embed, now, now, prev_count + 1))
        db.commit()
    except Exception as e:
        return f"⚠ 스킬 저장 실패: {e}"

    # memory에도 교차 기록
    remember(f"skill/{name}", f"{description} (키워드: {trigger_keywords})")

    return (
        f"✅ 스킬 '{name}' 저장 완료!\n"
        f"설명: {description}\n"
        f"키워드: {trigger_keywords}\n"
        f"방법: {method[:100]}...\n"
        f"다음에 유사한 요청 시 자동으로 이 방법을 사용합니다."
    )


def find_skill(query: str) -> str:
    """
    요청에 맞는 기존 스킬을 검색합니다.

    Args:
        query: 검색 키워드

    Returns:
        매칭된 스킬 정보 또는 없음
    """
    from .memory_tools import _get_db, _get_embedding, _cosine_similarity

    try:
        db = _get_db()
        rows = db.execute("SELECT name, description, method, script, embedding, success_count, trigger_keywords FROM skills").fetchall()
    except Exception:
        return "📭 저장된 스킬이 없습니다."

    if not rows:
        return "📭 저장된 스킬이 없습니다."

    # 시맨틱 검색
    q_emb = _get_embedding(query)
    if q_emb:
        scored = []
        for s in rows:
            if s["embedding"]:
                sim = _cosine_similarity(q_emb, s["embedding"])
                if sim >= 0.3:
                    scored.append((sim, s))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            lines = [f"🎯 '{query}' 관련 스킬:\n"]
            for sim, s in scored[:5]:
                lines.append(f"  📌 {s['name']}: {s['description']}")
                lines.append(f"     방법: {s['method']}")
                if s["script"]:
                    lines.append(f"     스크립트: {s['script']}")
                lines.append(f"     사용: {s['success_count']}회")
            return "\n".join(lines)

    # 폴백: 키워드 검색
    q_lower = query.lower()
    matches = [s for s in rows if q_lower in (s["name"] + " " + s["description"] + " " + s["trigger_keywords"]).lower()]
    if matches:
        lines = [f"🎯 '{query}' 관련 스킬:\n"]
        for s in matches[:5]:
            lines.append(f"  📌 {s['name']}: {s['description']}")
            lines.append(f"     방법: {s['method']}")
            if s["script"]:
                lines.append(f"     스크립트: {s['script']}")
            lines.append(f"     사용: {s['success_count']}회")
        return "\n".join(lines)

    return f"📭 '{query}' 관련 스킬이 없습니다."


# ─────────────────────────────────────────────
# 백그라운드 서비스 설치
# ─────────────────────────────────────────────

def install_service(name: str, description: str, command: str,
                    schedule: str = "", oneshot: bool = False) -> str:
    """
    systemd 서비스/타이머를 생성하고 등록합니다.

    Args:
        name: 서비스 이름 (영문)
        description: 서비스 설명
        command: 실행할 커맨드
        schedule: 반복 일정 (cron 형식, 예: "*:0/5" = 5분마다, "" = 상시 실행)
        oneshot: True면 실행 후 종료, False면 데몬으로 상시 실행

    Returns:
        등록 결과
    """
    from .system_tools import tool_run_command

    svc_name = f"xoul-{name}"
    svc_type = "oneshot" if oneshot or schedule else "simple"
    restart = "" if oneshot or schedule else "Restart=always\nRestartSec=10"

    # 서비스 유닛 파일
    service_content = f"""[Unit]
Description={description}
After=network.target

[Service]
Type={svc_type}
WorkingDirectory=/root/xoul
ExecStart={command}
{restart}

[Install]
WantedBy=multi-user.target
"""

    # 서비스 파일 저장
    svc_path = f"/etc/systemd/system/{svc_name}.service"
    escaped = service_content.replace("'", "'\\''")
    tool_run_command(f"cat > {svc_path} << 'SVC_EOF'\n{service_content}SVC_EOF")
    tool_run_command("systemctl daemon-reload")
    tool_run_command(f"systemctl enable {svc_name}")

    # 타이머 (일정이 있을 때)
    if schedule:
        timer_content = f"""[Unit]
Description=Timer for {description}

[Timer]
OnCalendar={schedule}
Persistent=true

[Install]
WantedBy=timers.target
"""
        timer_path = f"/etc/systemd/system/{svc_name}.timer"
        tool_run_command(f"cat > {timer_path} << 'TMR_EOF'\n{timer_content}TMR_EOF")
        tool_run_command("systemctl daemon-reload")
        tool_run_command(f"systemctl enable --now {svc_name}.timer")
        status = tool_run_command(f"systemctl status {svc_name}.timer --no-pager | head -5")
        return f"✅ 타이머 서비스 '{svc_name}' 등록 완료!\n일정: {schedule}\n{status}"
    else:
        tool_run_command(f"systemctl start {svc_name}")
        status = tool_run_command(f"systemctl status {svc_name} --no-pager | head -5")
        return f"✅ 서비스 '{svc_name}' 등록 및 시작 완료!\n{status}"


