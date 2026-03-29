"""
시스템 도구 - 셸 명령 실행, 날짜/시간, 계산기

환경 자동 감지:
  - VM 내부에서 실행 시: subprocess로 직접 실행
  - Windows에서 실행 시: SSH로 VM에서 실행
"""
import os
import subprocess
from i18n import t


def _is_in_vm() -> bool:
    """현재 VM 안에서 실행 중인지 확인"""
    return os.path.exists("/root/xoul") or os.name != "nt"


def tool_run_command(command: str, quiet: bool = False) -> str:
    """셸 명령 실행 (환경 자동 감지)"""
    if _is_in_vm():
        # VM 내부: 직접 실행
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, timeout=120
            )
            output = result.stdout + result.stderr
            output = output.strip()
            if len(output) > 5000:
                output = output[:5000] + "\n" + t("web.truncated")
            return output or t("system.exec_done")
        except subprocess.TimeoutExpired:
            return t("system.timeout")
        except Exception as e:
            return t("system.error", error=e)
    else:
        # Windows: SSH 경유
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_exec, is_vm_running
        if not is_vm_running():
            return t("system.vm_not_running")
        result = ssh_exec(command, timeout=120)
        if len(result) > 5000:
            result = result[:5000] + "\n" + t("web.truncated")
        return result or t("system.exec_done")


def tool_get_datetime() -> str:
    """현재 날짜/시간"""
    from datetime import datetime
    now = datetime.now()
    weekdays = t("system.weekdays")
    if isinstance(weekdays, list):
        wd = weekdays[now.weekday()]
    else:
        wd = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    y, mo, d = t("system.date_year"), t("system.date_month"), t("system.date_day")
    h, mi, s = t("system.date_hour"), t("system.date_minute"), t("system.date_second")
    date_str = f"{now.strftime('%Y')}{y} {now.strftime('%m')}{mo} {now.strftime('%d')}{d}"
    time_str = f"{now.strftime('%H')}{h} {now.strftime('%M')}{mi} {now.strftime('%S')}{s}"
    return t("system.date_format", date=date_str, weekday=wd, time=time_str)


def tool_calculate(expression: str) -> str:
    """수학 계산 (안전한 eval)"""
    import math
    safe_dict = {
        "__builtins__": {},
        "abs": abs, "round": round, "min": min, "max": max,
        "int": int, "float": float, "pow": pow, "sum": sum,
        "pi": math.pi, "e": math.e,
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "log": math.log, "log10": math.log10,
        "ceil": math.ceil, "floor": math.floor,
    }
    try:
        result = eval(expression, safe_dict)
        return f"{expression} = {result}"
    except Exception as e:
        return t("system.calc_error", error=e)
