"""
파일 관리 도구 - 파일 읽기/쓰기/목록

환경 자동 감지:
  - VM 내부에서 실행 시: 직접 파일 I/O
  - Windows에서 실행 시: SSH로 VM에서 실행
"""
import os


def _is_in_vm() -> bool:
    """현재 VM 안에서 실행 중인지 확인"""
    return os.path.exists("/root/xoul") or os.name != "nt"


VM_WORKSPACE = "/root/workspace"


def tool_read_file(path: str) -> str:
    """파일 읽기 (환경 자동 감지)"""
    if not path.startswith("/"):
        path = f"{VM_WORKSPACE}/{path}"

    if _is_in_vm():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > 5000:
                content = content[:5000] + "\n... (잘림)"
            return content if content else "(빈 파일)"
        except FileNotFoundError:
            return f"오류: 파일을 찾을 수 없습니다: {path}"
        except Exception as e:
            return f"오류: {e}"
    else:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_read_file, is_vm_running
        if not is_vm_running():
            return "오류: Ubuntu VM이 실행되고 있지 않습니다."
        content = ssh_read_file(path)
        if len(content) > 5000:
            content = content[:5000] + "\n... (잘림)"
        return content if content else "(빈 파일)"


def tool_write_file(path: str, content: str) -> str:
    """파일 쓰기 (환경 자동 감지)"""
    if not path.startswith("/"):
        path = f"{VM_WORKSPACE}/{path}"

    if _is_in_vm():
        try:
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"파일 저장 완료: {path} ({len(content)} 글자)"
        except Exception as e:
            return f"오류: {e}"
    else:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_exec, ssh_write_file, is_vm_running
        if not is_vm_running():
            return "오류: Ubuntu VM이 실행되고 있지 않습니다."
        dir_path = "/".join(path.split("/")[:-1])
        if dir_path:
            ssh_exec(f"mkdir -p '{dir_path}'")
        ssh_write_file(path, content)
        return f"파일 저장 완료: {path} ({len(content)} 글자)"


def tool_list_files(path: str = ".") -> str:
    """디렉토리 파일 목록 (환경 자동 감지)"""
    if not path.startswith("/"):
        path = f"{VM_WORKSPACE}/{path}"

    if _is_in_vm():
        try:
            import subprocess
            result = subprocess.run(
                f"ls -la '{path}' 2>&1",
                shell=True, capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            return output.strip() or f"{path} 디렉토리가 비어있습니다."
        except Exception as e:
            return f"오류: {e}"
    else:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vm_manager import ssh_exec, is_vm_running
        if not is_vm_running():
            return "오류: Ubuntu VM이 실행되고 있지 않습니다."
        result = ssh_exec(f"ls -la '{path}' 2>&1")
        return result or f"{path} 디렉토리가 비어있습니다."
