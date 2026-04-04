"""
Xoul Desktop Client — 호스트 사이드 도구 실행

Tier 1 (자동 실행): host_open_url, host_find_file, host_show_notification
Tier 2 (확인 필요): host_open_app, host_organize_files, host_run_command
"""

import os
import glob
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from i18n import t


# ─────────────────────────────────────────────
# 설치된 앱 목록 (시작 시 스캔)
# ─────────────────────────────────────────────
_INSTALLED_APPS: dict = {}  # {"app_name": "shortcut_path"}


def scan_installed_apps() -> dict:
    """Start Menu에서 .lnk 파일을 스캔하여 설치된 앱 목록 반환"""
    global _INSTALLED_APPS
    apps = {}
    start_dirs = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft\\Windows\\Start Menu\\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft\\Windows\\Start Menu\\Programs"),
    ]
    for start_dir in start_dirs:
        if not os.path.exists(start_dir):
            continue
        for root, dirs, files in os.walk(start_dir):
            for f in files:
                if f.endswith(".lnk"):
                    name = f.replace(".lnk", "")
                    # 중복 시 사용자 폴더 우선
                    if name not in apps:
                        apps[name] = os.path.join(root, f)
    _INSTALLED_APPS = apps
    return apps


def get_installed_app_names() -> list:
    """설치된 앱 이름 목록 반환 (LLM 컨텍스트용)"""
    if not _INSTALLED_APPS:
        scan_installed_apps()
    return sorted(_INSTALLED_APPS.keys())


# ─────────────────────────────────────────────
# URI 스킴 스캔 및 카테고리별 요약 (레지스트리 HKCR)
# ─────────────────────────────────────────────
_URI_SUMMARY: str = ""

# 카테고리 매핑 — scheme 이름(소문자) prefix → 카테고리
_SCHEME_CATEGORIES = {
    # Gaming
    "steam": "Gaming", "steamvr": "Gaming", "steamlink": "Gaming",
    "steamtours": "Gaming", "battlenet": "Gaming", "blizzard": "Gaming",
    "heroes": "Gaming", "starcraft": "Gaming", "origin": "Gaming",
    "origin2": "Gaming", "ealink": "Gaming", "link2ea": "Gaming",
    "eaconnect": "Gaming", "iracing": "Gaming", "citadel": "Gaming",
    "hlvr": "Gaming", "oculus": "Gaming", "vrmonitor": "Gaming",
    # Chat & Communication
    "discord": "Chat", "kakaotalk": "Chat", "kakaoopen": "Chat",
    "msteams": "Chat", "ms-teams": "Chat", "mailto": "Chat",
    "callto": "Chat", "tel": "Chat", "sip": "Chat", "sms": "Chat",
    # Office
    "ms-word": "Office", "ms-excel": "Office", "ms-powerpoint": "Office",
    "ms-publisher": "Office", "ms-access": "Office", "onenote": "Office",
    "ms-outlook": "Office", "outlookmail": "Office", "outlookcal": "Office",
    "outlook.url": "Office", "webcal": "Office", "feed": "Office",
    # System & Settings
    "ms-settings": "Settings", "ms-availablenetworks": "Settings",
    "calculator": "Settings", "ms-calculator": "Settings",
    "ms-paint": "Settings", "ms-clock": "Settings",
    "ms-screenclip": "Settings", "ms-screensketch": "Settings",
    "ms-notepad": "Settings", "ms-photos": "Settings",
    "ms-copilot": "Settings", "ms-gamebar": "Settings",
    "ms-windows-store": "Settings", "windowsdefender": "Settings",
    # Dev Tools
    "cursor": "Dev", "unityhub": "Dev", "com.unity3d": "Dev",
    "lmstudio": "Dev", "ollama": "Dev", "vsweb": "Dev",
    # Media
    "audacity": "Media", "capcut": "Media", "acrobat": "Media",
    "launchreader": "Media", "gomcmd": "Media", "avis": "Media",
    "magnet": "Media", "bittorrent": "Media",
    # Network & VPN
    "nordvpn": "VPN", "nvidiaapp": "Other",
    # Browser
    "http": "Browser", "https": "Browser", "google-chrome": "Browser",
    "microsoft-edge": "Browser",
}

# 무시할 시스템/내부용 prefix
_SKIP_PREFIXES = (
    "ms-xbl-", "ms-cxh", "ms-oobe", "ms-retaildemo", "ms-device-",
    "ms-wpc", "ms-wpdrmv", "ms-wxh", "ms-xbet", "ms-woah",
    "ms-lwh", "ms-ipmessaging", "ms-holographic", "ms-eyecontrol",
    "ms-edu-", "ms-fulltrust", "ms-rdx-", "ms-apprep", "ms-aad-",
    "ms-appinstaller", "ms-inputapp", "ms-shellhost", "ms-snaplaunch",
    "ms-stt", "ms-taskswitcher", "ms-virtualtouchpad", "ms-voip",
    "ms-walk-", "ms-wcrv", "ms-widgetboard", "ms-widgets",
    "ms-startfeeds", "ms-sticker", "ms-newsandinterests",
    "ms-msime", "ms-meetnow", "ms-launchremote", "ms-insights",
    "ms-contact", "ms-crossdevice", "ms-discoverwidget",
    "ms-officecmd", "ms-officeapp", "ms-office-storage",
    "ms-office-ai", "ms-get", "ms-gaming", "ms-gamebarservices",
    "ms-print-", "ms-personacard", "ms-phone", "ms-playto",
    "ms-powerautomate", "ms-quick-assist", "ms-media-player",
    "ms-search", "ms-windows-search", "ms-windows-store-deskext",
    "ms-windows-store2", "ms-windowsbackup", "ms-unistore",
    "ms-mmsys", "ms-msdt", "ms-controlcenter",
    "xbox-", "xbls", "msxbox", "msgame", "msnews", "msnnews",
    "msnweather", "mswindows", "msonedrivesyncserviceclient",
    "msteamscanary", "msul",
    "explorer.", "ie.", "iehistory", "ierss",
    "res", "mk", "read", "file", "ftp",
    "jnlp", "ldap", "rlogin", "telnet", "tn3270",
    "tbauth", "windows.tbauth", "pw.oauth2",
    "wp-autoplay", "wpa", "wtsapp", "wsl-settings",
    "action-", "armodelviewing", "asusac",
    "adobe.genuine", "som", "dzreportx", "anysignforpc",
    "wizvera", "receiver", "microsoft.windows.camera",
    "microsoft.windows.photos", "com.adobe", "com.clipchamp",
    "com.microsoft.3d", "gaming-tcui", "gamingservicesui",
    "git-client", "grvopen", "odopen", "insiderhub",
    "feedback-hub", "windows-feedback", "lpa", "mapi",
    "search-ms", "search", "skype", "vsls", "vstfs",
    "web+msteams", "wifi", "bingmaps", "bingnews", "bingweather",
    "im", "sips", "onenote-cmd", "microsoft.workfolders",
    "ms-olk-", "outlookaccounts", "edimakor",
    "wmp11", "mms", "dlna", "ms-clipchamp",
    "antigravity", "discord-1034",
    "microsoftmusic", "microsoftvideo", "microsoftsolitaire",
    "blizzard.uri",  # battlenet:// 으로 충분
    "acrobat20", "acrobat2",  # acrobat:// 으로 충분
    "onenotedesktop", "onenote.url", "oneindex",  # onenote:// 으로 충분
    "outlook.url",  # mailto:// 으로 충분
    "stssync", "feeds",
    "ms-settings-",  # ms-settings:// 으로 충분 (서브 스킴은 LLM이 알고 있음)
    "ms-penworkspace", "ms-people", "ms-actioncenter",
    "ms-default-location", "ms-devhome", "ms-drive-to",
    "ms-clicktodo", "ms-to-do",
    "ms-copilot", "ms-cortana",
    "vsweb+",  # vsweb:// 계열 중복
    "nordvpn.notification",  # nordvpn:// 으로 충분
    "gogms", "gomlogo", "jamak",  # gomcmd:// 으로 충분
)


def scan_uri_schemes() -> list:
    """Windows 레지스트리에서 등록된 URI 스킴 목록을 스캔"""
    try:
        import winreg
    except ImportError:
        return []

    results = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, '')
        i = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root, i)
                i += 1
                try:
                    sk = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, subkey_name)
                    try:
                        winreg.QueryValueEx(sk, 'URL Protocol')
                    except FileNotFoundError:
                        sk.Close()
                        continue
                    results.append(subkey_name)
                    sk.Close()
                except Exception:
                    pass
            except OSError:
                break
        root.Close()
    except Exception:
        pass
    return results


def get_uri_schemes_summary() -> str:
    """카테고리별로 그룹핑된 URI 스킴 요약 문자열 반환 (LLM 프롬프트용, ~200 토큰)"""
    global _URI_SUMMARY
    if _URI_SUMMARY:
        return _URI_SUMMARY

    schemes = scan_uri_schemes()
    if not schemes:
        return ""

    # 카테고리별 분류
    categorized = {}
    for scheme in schemes:
        lower = scheme.lower()

        # 무시 대상 확인
        if any(lower.startswith(skip.lower()) for skip in _SKIP_PREFIXES):
            continue

        # 카테고리 매핑
        cat = None
        for prefix, category in _SCHEME_CATEGORIES.items():
            if lower.startswith(prefix.lower()):
                cat = category
                break

        if cat and cat != "Browser":  # http/https는 이미 당연하므로 제외
            if cat not in categorized:
                categorized[cat] = []
            display = f"{scheme}://"
            if display not in categorized[cat]:
                categorized[cat].append(display)

    if not categorized:
        return ""

    # 카테고리 순서 고정
    order = ["Gaming", "Chat", "Office", "Settings", "Dev", "Media", "VPN", "Other"]
    lines = []
    for cat in order:
        items = categorized.get(cat, [])
        if items:
            lines.append(f"{cat}: {', '.join(items)}")

    _URI_SUMMARY = "\n".join(lines)
    return _URI_SUMMARY


# ─────────────────────────────────────────────
# 보안: 허용 경로
# ─────────────────────────────────────────────
ALLOWED_SEARCH_DIRS = [
    str(Path.home() / "Desktop"),
    str(Path.home() / "Documents"),
    str(Path.home() / "Downloads"),
]

BLOCKED_URL_SCHEMES = {"file", "javascript", "data", "vbscript"}

MAX_SEARCH_DEPTH = 3
MAX_SEARCH_RESULTS = 50
SEARCH_TIMEOUT_SEC = 5


# ─────────────────────────────────────────────
# Tier 1 도구
# ─────────────────────────────────────────────

def host_open_url(url: str) -> str:
    """브라우저에서 URL 열기"""
    if not url:
        return t("host.url_empty")

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme in BLOCKED_URL_SCHEMES:
        return t("host.url_blocked", scheme=scheme)

    if not scheme:
        url = "https://" + url

    try:
        webbrowser.open(url)
        return t("host.url_opened", url=url)
    except Exception as e:
        return t("host.url_fail", e=str(e))


def host_find_file(query: str, directory: str = "") -> str:
    """파일 검색 (이름/크기만 반환, 내용 읽지 않음)"""
    if not query:
        return t("host.search_empty")

    # 기본 검색 경로
    if directory:
        # 별칭 매핑 — LLM이 "Downloads", "다운로드" 등만 전달해도 OK
        DIR_ALIASES = {
            "downloads": str(Path.home() / "Downloads"),
            "다운로드": str(Path.home() / "Downloads"),
            "desktop": str(Path.home() / "Desktop"),
            "바탕화면": str(Path.home() / "Desktop"),
            "documents": str(Path.home() / "Documents"),
            "문서": str(Path.home() / "Documents"),
        }
        resolved = DIR_ALIASES.get(directory.lower().strip(), "")
        if not resolved:
            resolved = str(Path(directory).resolve())

        # .. 경로 탐색 차단
        allowed = any(resolved.startswith(d) for d in ALLOWED_SEARCH_DIRS)
        if not allowed:
            return (
                t("host.dir_not_allowed", directory=directory)
                + t("host.dir_allowed_list")
            )
        search_dirs = [resolved]
    else:
        search_dirs = [d for d in ALLOWED_SEARCH_DIRS if os.path.exists(d)]

    results = []
    for search_dir in search_dirs:
        for root, dirs, files in os.walk(search_dir):
            # 깊이 제한
            depth = root[len(search_dir):].count(os.sep)
            if depth >= MAX_SEARCH_DEPTH:
                dirs.clear()
                continue

            for name in files:
                if query.lower() in name.lower():
                    fpath = os.path.join(root, name)
                    try:
                        size = os.path.getsize(fpath)
                        rel = os.path.relpath(fpath, Path.home())
                        if size < 1024:
                            size_str = f"{size}B"
                        elif size < 1024 * 1024:
                            size_str = f"{size / 1024:.1f}KB"
                        else:
                            size_str = f"{size / (1024 * 1024):.1f}MB"
                        results.append(f"  ~/{rel}  ({size_str})")
                    except OSError:
                        pass

                    if len(results) >= MAX_SEARCH_RESULTS:
                        break
            if len(results) >= MAX_SEARCH_RESULTS:
                break
        if len(results) >= MAX_SEARCH_RESULTS:
            break

    if not results:
        return f"🔍 '{query}' " + t("host.search_no_result")

    header = f"🔍 '{query}' " + t("host.search_result", count=len(results))
    return header + "\n".join(results)


def host_show_notification(title: str, message: str) -> str:
    """Windows 알림 표시 (데스크톱 앱의 popup 사용)"""
    return t("host.notify_ok", title=title, message=message)


# ─────────────────────────────────────────────
# Tier 2 도구 (확인 팝업 후 실행)
# ─────────────────────────────────────────────

# 차단 명령어 패턴
BLOCKED_COMMANDS = [
    "del ", "rmdir", "rd ", "format ", "reg ", "regedit",
    "shutdown", "taskkill", "attrib", "icacls", "takeown",
    "rm -rf", "rm -r", "mkfs", "dd if=",
    "net user", "net localgroup",
]


def host_open_app(app_name: str) -> str:
    """Windows 앱 실행"""
    if not app_name:
        return t("host.launch_empty")

    # URL이 들어오면 host_open_url로 리다이렉트
    if app_name.startswith(("http://", "https://", "www.")):
        return host_open_url(app_name)

    import subprocess
    import shutil

    # 시스템 유틸리티 별칭 (실행 파일명이 직관적이지 않은 것들만)
    APP_ALIASES = {
        "calculator": "calc.exe",
        "notepad": "notepad.exe",
        "explorer": "explorer.exe",
        "paint": "mspaint.exe",
        "task manager": "taskmgr.exe",
        "settings": "ms-settings:",
    }

    target = APP_ALIASES.get(app_name.lower().strip(), app_name)
    search_names = list(set([app_name.lower(), target.lower()]))

    # ms- URI 스킴 (설정 등)
    if target.startswith("ms-"):
        try:
            os.startfile(target)
            return t("host.launch_ok", app=app_name)
        except Exception as e:
            return t("host.launch_fail", e=str(e))

    # PATH에서 찾기
    found = shutil.which(target)
    if found:
        try:
            subprocess.Popen([found], shell=False)
            return t("host.launch_ok", app=f"{app_name} ({found})")
        except Exception as e:
            return t("host.launch_fail", e=str(e))

    # 캐시된 앱 목록에서 검색
    if not _INSTALLED_APPS:
        scan_installed_apps()

    # 정확한 매칭
    for name, path in _INSTALLED_APPS.items():
        if app_name.lower() == name.lower():
            try:
                os.startfile(path)
                return t("host.launch_ok", app=name)
            except Exception as e:
                return t("host.launch_fail", e=str(e))

    # 부분 매칭 (app_name이 shortcut 이름에 포함)
    for name, path in _INSTALLED_APPS.items():
        if any(s in name.lower() for s in search_names):
            try:
                os.startfile(path)
                return t("host.launch_ok", app=name)
            except Exception as e:
                return t("host.launch_fail", e=str(e))

    return t("host.app_not_found", apps=', '.join(list(_INSTALLED_APPS.keys())[:10]) + '...')


def host_organize_files(source: str, destination: str, pattern: str = "*") -> str:
    """파일 정리 (이동)"""
    import shutil

    # 별칭 매핑
    DIR_ALIASES = {
        "downloads": str(Path.home() / "Downloads"),
        "다운로드": str(Path.home() / "Downloads"),
        "desktop": str(Path.home() / "Desktop"),
        "바탕화면": str(Path.home() / "Desktop"),
        "documents": str(Path.home() / "Documents"),
        "문서": str(Path.home() / "Documents"),
    }
    src = DIR_ALIASES.get(source.lower().strip(), str(Path(source).resolve()))
    dst = DIR_ALIASES.get(destination.lower().strip(), str(Path(destination).resolve()))

    # 경로 검증
    for path, label in [(src, t("host.label_source")), (dst, t("host.label_dest"))]:
        if not any(path.startswith(d) for d in ALLOWED_SEARCH_DIRS):
            return t("host.path_not_allowed", label=label, path=path)

    if not os.path.exists(src):
        return t("host.path_not_exist", src=src)

    os.makedirs(dst, exist_ok=True)

    import fnmatch
    moved = []
    for f in os.listdir(src):
        if fnmatch.fnmatch(f.lower(), pattern.lower()):
            src_file = os.path.join(src, f)
            if os.path.isfile(src_file):
                shutil.move(src_file, os.path.join(dst, f))
                moved.append(f)

    if not moved:
        return f"🔍 '{pattern}' " + t("host.no_match")
    return t("host.move_ok", count=len(moved)) + "\n".join(f"  {f}" for f in moved[:20])


def host_run_command(command: str) -> str:
    """PowerShell 명령 실행 (위험 명령 차단)"""
    if not command:
        return t("host.shell_empty")

    # 위험 명령 차단
    cmd_lower = command.lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return t("host.shell_blocked", cmd=blocked.strip())

    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout.strip()
        error = result.stderr.strip()
        if error and not output:
            return t("host.shell_error", error=error[:500])
        if output:
            return t("host.shell_ok", output=output[:1000])
        return t("host.shell_ok_no_output")
    except subprocess.TimeoutExpired:
        return t("host.shell_timeout")
    except Exception as e:
        return t("host.shell_fail", e=str(e))


# ─────────────────────────────────────────────
# 도구 디스패처
# ─────────────────────────────────────────────

# Tier 1: 자동 실행
TIER1_TOOLS = {
    "host_open_url": lambda args: host_open_url(args.get("url", args.get("command", args.get("link", "")))),
    "host_find_file": lambda args: host_find_file(
        args.get("query", ""), args.get("directory", "")
    ),
    "host_show_notification": lambda args: host_show_notification(
        args.get("title", "Xoul"), args.get("message", "")
    ),
    "host_open_app": lambda args: host_open_app(args.get("app_name", args.get("name", ""))),
}

# Tier 2: 확인 필요
TIER2_TOOLS = {
    "host_organize_files": lambda args: host_organize_files(
        args.get("source", ""), args.get("destination", ""), args.get("pattern", "*")
    ),
    "host_run_command": lambda args: t("host.shell_disabled"),
}


def is_host_tool(tool_name: str) -> bool:
    """host_ 접두어 도구인지 확인"""
    return tool_name.startswith("host_")


def is_tier2(tool_name: str) -> bool:
    """Tier 2 (확인 필요) 도구인지"""
    return tool_name in TIER2_TOOLS


def execute_host_tool(tool_name: str, args: dict) -> str:
    """호스트 도구 실행"""
    # Tier 1
    handler = TIER1_TOOLS.get(tool_name)
    if handler:
        try:
            return handler(args)
        except Exception as e:
            return t("host.execute_error", e=str(e))

    # Tier 2 (확인 후 호출됨)
    handler = TIER2_TOOLS.get(tool_name)
    if handler:
        try:
            return handler(args)
        except Exception as e:
            return t("host.execute_error", e=str(e))

    return t("host.unknown_tool", tool_name=tool_name)
