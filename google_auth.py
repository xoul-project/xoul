"""
Google API 인증 모듈
- OAuth 2.0 인증/토큰 관리
- Calendar + Gmail scope
"""
import os
import json
import sys

# i18n 을 위한 프로젝트 루트 로드
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
from i18n import t, init_from_config
init_from_config()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.json")


def get_credentials():
    """저장된 토큰 로드 + 자동 갱신. 없으면 None 반환."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if os.path.isfile(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
        except Exception:
            creds = None

    return creds


def run_oauth_flow():
    """브라우저에서 Google 로그인 → token.json 저장"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.isfile(CREDENTIALS_FILE):
        print(t("google_auth.no_credentials", path=CREDENTIALS_FILE))
        return None

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    print(t("google_auth.auth_done"))
    return creds


def _save_token(creds):
    """토큰을 파일에 저장"""
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


def build_service(api, version):
    """Google API 서비스 빌드 (예: gmail v1, calendar v3)"""
    from googleapiclient.discovery import build

    creds = get_credentials()
    if not creds:
        return None
    return build(api, version, credentials=creds)


if __name__ == "__main__":
    # 직접 실행 시 OAuth 인증 수행
    creds = get_credentials()
    if creds:
        print(t("google_auth.already_auth"))
    else:
        print(t("google_auth.auth_starting"))
        run_oauth_flow()
