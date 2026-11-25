"""
PMS 주간보고 자동화 스크립트 (run_pms_sync.py)

[기능 요약]
1. Chrome 원격 디버깅 세션(CDP: http://localhost:9222)에 attach
2. Redmine 내 작업 일감 목록에서 weekly_issues.csv 다운로드
3. 대화형 CLI 프롬프트:
   - 사업관리 항목 선택 (주간보고, 휴가신청, 인력변동, 공지사항 등록, 월간보고)
   - 3개월(90일) 이상 장기 미완료 건 수기 확인 및 선택
4. Untitled-1.html 기반 데이터 가공 로직:
   - [핵심 예외 규칙] 완료건 중 완료기한(완료요청일)이 이번 주 월요일 이전(과거)인 건 자동 제외
   - 상위일감별 그룹화 (평균 진척도 계산 생략)
   - 범주별/상위일감별 계층 구조 텍스트 생성
5. PMS 주간보고 화면(https://pms.metabuild.co.kr/mypage/wkly/save) 진입:
   - 진척률: 100 입력
   - '금주 실적' 및 '차주 계획' Tiptap ProseMirror 에디터에 텍스트 주입
   - [저장] 버튼 클릭 및 저장 완료
"""

import os
import sys
import re
import csv
import json
import time
import ctypes
import socket
import shutil
import argparse
import datetime
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

# Windows 기본 콘솔(cp949)에서 한글 및 이모지(📢 등) 출력 시 UnicodeEncodeError 방지
if os.name == "nt":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

CURRENT_VERSION = "v1.0.3"
GITHUB_REPO = "nuevo-Sumin/pms-weekly-report"
EXE_ASSET_NAME = "PMS_Weekly_Report.exe"
USER_NOTICE_MESSAGE = "[📢📢📢📢📢] 사용자 여러분 최고 멋져 해피 연말"
_NOTICE_PRINTED = False

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext
except ImportError:
    print("[Error] 'playwright' 패키지가 필요합니다. (pip install playwright)")
    sys.exit(1)


# ==============================================================================
# 0. 한국 표준시(KST: UTC+9) 및 날짜/경로 계산 유틸리티
# ==============================================================================
KST = datetime.timezone(datetime.timedelta(hours=9))

def get_base_dir() -> Path:
    """
    스크립트 실행 환경을 판별하여 기준 작업 디렉토리를 반환합니다.
    - PyInstaller 빌드(.exe) 환경: sys.executable의 부모 디렉토리
    - 일반 Python 스크립트(.py) 환경: 본 파일(__file__)의 부모 디렉토리
    - 그 외 예외 상황: 현재 작업 디렉토리(Path.cwd())
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    try:
        return Path(__file__).parent.resolve()
    except Exception:
        return Path.cwd().resolve()


def get_safe_downloads_dir(base_dir: Path = None, run_date: datetime.date = None) -> Path:
    """
    실행일자가 포함된 임시 다운로드 폴더(downloads_YYYYMMDD)를 생성하여 반환합니다.
    - 1순위: base_dir / 'downloads_YYYYMMDD'
    - 2순위 (쓰기 권한 부족 시): Path.home() / '.pms_weekly_report' / 'downloads_YYYYMMDD'
    """
    if base_dir is None:
        base_dir = get_base_dir()
    if run_date is None:
        run_date = get_korea_today()

    folder_name = f"downloads_{run_date.strftime('%Y%m%d')}"
    primary_dir = base_dir / folder_name
    try:
        primary_dir.mkdir(parents=True, exist_ok=True)
        test_file = primary_dir / ".perm_check"
        test_file.touch()
        test_file.unlink()
        return primary_dir
    except (PermissionError, OSError):
        fallback_dir = Path.home() / ".pms_weekly_report" / folder_name
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir


def get_korea_today() -> datetime.date:
    """한국 시간(KST) 기준 오늘 날짜를 반환합니다."""
    return datetime.datetime.now(KST).date()


def is_port_open(host="127.0.0.1", port=9222, timeout=1.0) -> bool:
    """지정된 호스트/포트가 열려 있는지 소켓으로 빠르게 확인합니다."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def parse_version_tuple(ver_str: str) -> tuple:
    """'v1.0.2' 형태의 버전 문자열을 (1, 0, 2) 정수 튜플로 변환합니다."""
    nums = re.findall(r"\d+", str(ver_str))
    return tuple(int(n) for n in nums) if nums else (0,)


def unblock_file(file_path: Path):
    """Windows 인터넷 다운로드 보안 차단 스트림(:Zone.Identifier)을 자동으로 제거합니다."""
    if os.name != "nt":
        return
    try:
        zone_stream = f"{file_path}:Zone.Identifier"
        if os.path.exists(zone_stream):
            os.remove(zone_stream)
    except Exception:
        pass


def check_and_apply_auto_update() -> bool:
    """
    GitHub Releases API를 통해 최신 버전을 확인하고, 새 버전이 있으면 자동으로 다운로드하여
    현재 실행 파일(.exe)을 교체한 뒤 새 버전을 즉시 실행합니다.
    업데이트 및 재실행이 수행된 경우 True를 반환합니다.
    """
    if not getattr(sys, "frozen", False):
        return False

    current_exe = Path(sys.executable).resolve()
    unblock_file(current_exe)
    old_exe = current_exe.with_name(current_exe.name + ".old")
    new_exe = current_exe.with_name(current_exe.name + ".new")

    # 이전 업데이트에서 남은 .old 백업 파일 정리
    if old_exe.exists():
        try:
            old_exe.unlink()
        except Exception:
            pass

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    print(f"[*] 프로그램 버전 확인 중... (현재 버전: {CURRENT_VERSION})")

    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "PMS-Weekly-Report-Updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            release_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        # 네트워크 단절 또는 API 제한 시 업데이트 생략 후 정상 진행
        print(f"[*] 최신 버전 확인 생략 ({e})")
        return False

    latest_tag = str(release_data.get("tag_name", "")).strip()
    if not latest_tag:
        return False

    if parse_version_tuple(latest_tag) <= parse_version_tuple(CURRENT_VERSION):
        print(f"[+] 최신 버전을 사용 중입니다 ({CURRENT_VERSION}).")
        if USER_NOTICE_MESSAGE:
            global _NOTICE_PRINTED
            print(USER_NOTICE_MESSAGE)
            _NOTICE_PRINTED = True
        return False

    # 최신 릴리스 에셋 중 .exe 다운로드 URL 탐색
    download_url = None
    expected_size = 0
    for asset in release_data.get("assets", []):
        asset_name = str(asset.get("name", ""))
        if asset_name == EXE_ASSET_NAME or asset_name.lower().endswith(".exe"):
            download_url = asset.get("browser_download_url")
            expected_size = int(asset.get("size", 0))
            break

    if not download_url:
        return False

    print("\n" + "=" * 68)
    print(f" [★자동 업데이트★] 새로운 버전({latest_tag})이 출시되었습니다!")
    print(f" - 현재 버전: {CURRENT_VERSION}  ->  최신 버전: {latest_tag}")
    print(" - 최신 실행 파일을 자동으로 다운로드하고 업데이트합니다...")
    print("=" * 68)

    try:
        dl_req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "PMS-Weekly-Report-Updater"},
        )
        with urllib.request.urlopen(dl_req, timeout=60) as dl_resp:
            total_size = int(dl_resp.headers.get("Content-Length", expected_size) or 0)
            downloaded = 0
            chunk_size = 256 * 1024
            with open(new_exe, "wb") as out_f:
                while True:
                    chunk = dl_resp.read(chunk_size)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        mb_done = downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        print(f"\r[*] 다운로드 진행률: {pct:5.1f}% ({mb_done:.1f} MB / {mb_total:.1f} MB)", end="", flush=True)
            print()

        # 파일 크기 무결성 검증 (최소 5MB 이상)
        if not new_exe.exists() or new_exe.stat().st_size < 5 * 1024 * 1024:
            print("[!] 다운로드된 파일 크기가 비정상적이어서 업데이트를 중단합니다.")
            if new_exe.exists():
                new_exe.unlink()
            return False

        # Windows 실행 중 파일 이름 변경(current -> .old, .new -> current)
        if old_exe.exists():
            try:
                old_exe.unlink()
            except Exception:
                pass

        os.replace(str(current_exe), str(old_exe))
        os.replace(str(new_exe), str(current_exe))
        unblock_file(current_exe)
        print(f"[+] {latest_tag} 업데이트 완료! 최신 버전으로 다시 시작합니다...\n")

        child_args = [str(current_exe), "--skip-update"] + [a for a in sys.argv[1:] if a != "--skip-update"]
        child_env = os.environ.copy()
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        child_env.pop("_MEIPASS2", None)
        subprocess.call(child_args, env=child_env)
        return True

    except Exception as e:
        print(f"\n[!] 자동 업데이트 적용 중 오류 발생 (기존 버전으로 계속 진행합니다): {e}")
        # 복구 시도
        if not current_exe.exists() and old_exe.exists():
            try:
                os.replace(str(old_exe), str(current_exe))
            except Exception:
                pass
        if new_exe.exists():
            try:
                new_exe.unlink()
            except Exception:
                pass
        return False


REDMINE_MY_ISSUES_URL = (
    "https://support.kyci.or.kr/redmine/issues?utf8=%E2%9C%93&set_filter=1"
    "&sort=status%3Adesc%2Cid%3Adesc&f%5B%5D=status_id&op%5Bstatus_id%5D=%21"
    "&v%5Bstatus_id%5D%5B%5D=11&v%5Bstatus_id%5D%5B%5D=12&v%5Bstatus_id%5D%5B%5D=13"
    "&f%5B%5D=assigned_to_id&op%5Bassigned_to_id%5D=%3D&v%5Bassigned_to_id%5D%5B%5D=me"
    "&f%5B%5D=&c%5B%5D=project&c%5B%5D=tracker&c%5B%5D=status&c%5B%5D=priority"
    "&c%5B%5D=subject&c%5B%5D=assigned_to&c%5B%5D=updated_on&group_by=&t%5B%5D="
)
PMS_LIST_URL = "https://pms.metabuild.co.kr/mypage/wkly/list"


def launch_chrome_debug_session(base_dir: Path):
    """
    Chrome 원격 디버깅 모드(포트 9222)를 백그라운드 프로세스로 실행합니다.
    1번째 탭: Redmine 내 작업 일감 목록
    2번째 탭: PMS 주간보고 목록
    """
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    chrome_exe = None
    for c in candidates:
        if c.is_file():
            chrome_exe = str(c)
            break

    if not chrome_exe:
        raise FileNotFoundError(
            "Google Chrome 실행 파일(chrome.exe)을 찾을 수 없습니다.\n"
            "Chrome이 정상적으로 설치되어 있는지 확인해주세요."
        )

    profile_dir = base_dir / "chrome_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    url1 = REDMINE_MY_ISSUES_URL
    url2 = PMS_LIST_URL

    cmd = [
        chrome_exe,
        "--remote-debugging-port=9222",
        f"--user-data-dir={profile_dir}",
        url1,
        url2
    ]

    print(f"[*] Chrome 브라우저 실행 중 (실행 파일: {chrome_exe})")
    print(f"[*] 디버깅 프로필 경로: {profile_dir}")
    print("[*] 탭 1: Redmine 일감 목록 / 탭 2: PMS 주간보고 목록")
    subprocess.Popen(cmd)

    # 포트 9222 활성화 대기 (최대 10초)
    print("[*] Chrome CDP 디버깅 포트(9222) 연결 대기 중...")
    for _ in range(20):
        time.sleep(0.5)
        if is_port_open(port=9222):
            print("[+] Chrome 원격 디버깅 세션(포트 9222) 활성화 확인 완료!")
            return True

    print("[*] Chrome 창이 실행되었습니다.")
    return True


def get_this_week_range(base_date: datetime.date = None):
    """
    실행일(기본: 한국 시간 오늘)이 속한 주의 월요일 00:00:00부터 금요일 23:59:59까지의 범위를 반환합니다.
    """
    if base_date is None:
        base_date = get_korea_today()
        
    monday = base_date - datetime.timedelta(days=base_date.weekday())
    friday = monday + datetime.timedelta(days=4)
    
    monday_start = datetime.datetime(monday.year, monday.month, monday.day, 0, 0, 0)
    friday_end = datetime.datetime(friday.year, friday.month, friday.day, 23, 59, 59)
    
    return monday_start, friday_end


def parse_redmine_datetime(dt_str):
    """
    Redmine CSV의 다양한 날짜/일시 문자열을 datetime 객체로 변환합니다.
    (예: '2026-11-06', '2026-11-06 14:30', '2026-11-06 오후 02:30:00' 등)
    """
    if not dt_str:
        return None
    s = str(dt_str).strip()
    is_pm = "오후" in s
    is_am = "오전" in s
    s = s.replace("오전", "").replace("오후", "").strip()

    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))

    tm = re.search(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", s)
    hour, minute, second = 0, 0, 0
    if tm:
        hour, minute = int(tm.group(1)), int(tm.group(2))
        if tm.group(3):
            second = int(tm.group(3))
        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0

    return datetime.datetime(year, month, day, hour, minute, second)


# ==============================================================================
# 1. Redmine CSV 파싱 및 Untitled-1.html 기반 데이터 가공
# ==============================================================================
def load_redmine_issues_csv(csv_path: str):
    """CSV 파일을 인코딩(cp949, euc-kr 등) 순차 시도로 읽어 행 목록을 반환합니다."""
    encodings = ["cp949", "euc-kr", "utf-8-sig", "utf-8"]
    csv_rows = None
    last_err = None

    for enc in encodings:
        try:
            with open(csv_path, "r", encoding=enc) as f:
                reader = csv.DictReader(f)
                csv_rows = list(reader)
                print(f"[*] CSV 로드 성공 (인코딩: {enc}, 총 행수: {len(csv_rows)}건)")
                break
        except UnicodeDecodeError as e:
            last_err = e
            continue

    if csv_rows is None:
        raise RuntimeError(f"CSV 파일을 읽을 수 없습니다: {csv_path} (에러: {last_err})")

    return csv_rows


def get_field_val(row: dict, candidate_keys: list, default=""):
    """후보 키 목록 중 행에 존재하고 빈 문자열이 아닌 첫 번째 키의 값을 반환합니다."""
    for key in candidate_keys:
        for r_key in row.keys():
            if r_key and r_key.strip() == key:
                val = row[r_key]
                if val is not None and str(val).strip() != "":
                    return str(val).strip()
    return default


def parse_tasks_from_rows(csv_rows: list):
    """CSV 행들을 표준 태스크 딕셔너리 구조로 정규화하고 다단계 상위일감 계층을 해석합니다."""
    raw_tasks = []
    for r in csv_rows:
        raw_id = get_field_val(r, ["#", "ID", "id"])
        if not raw_id:
            continue
        try:
            task_id = int(raw_id)
        except ValueError:
            task_id = raw_id

        parent_raw = get_field_val(r, ["상위 일감", "상위일감", "Parent task"])
        parent_match = re.search(r"#(\d+)", parent_raw) or re.search(r"^(\d+)$", parent_raw) or re.search(r"(\d+)", parent_raw)
        has_parent = bool(parent_match)
        parent_id = parent_match.group(1) if has_parent else None

        tracker = get_field_val(r, ["유형", "Tracker"], default="")
        category = get_field_val(r, ["범주", "Category"], default="미분류") or "미분류"
        status = get_field_val(r, ["상태", "Status"], default="신규")
        title = get_field_val(r, ["제목", "Subject"], default="")
        parent_subject = get_field_val(r, ["Parent task subject", "상위 일감 제목"], default="")

        change_dt = parse_redmine_datetime(get_field_val(r, ["변경", "변경일", "변경일시", "수정일", "수정", "Updated", "updated_on"]))
        due_dt = parse_redmine_datetime(get_field_val(r, ["완료기한", "완료요청일", "완료일", "기한", "Due date", "due_date"]))
        create_dt = parse_redmine_datetime(get_field_val(r, ["등록", "등록일", "등록 일자", "등록일시", "생성일", "생성", "만든 날짜", "Created", "Created on", "created_on"]))

        progress_str = get_field_val(r, ["진척도", "% Done", "진척률"], default="0")
        try:
            progress = int(re.findall(r"\d+", progress_str)[0])
        except (IndexError, ValueError):
            progress = 0

        raw_tasks.append({
            "id": task_id,
            "tracker": tracker,
            "parentId": parent_id,
            "parentSubject": parent_subject,
            "category": category,
            "status": status,
            "title": title,
            "modDate": change_dt,
            "dueDate": due_dt,
            "createDate": create_dt,
            "progress": progress,
        })

    # 다른 일감의 상위일감으로 참조되거나 유형이 '상위일감 관리'인 컨테이너 일감 식별
    all_parent_ids = {str(t["parentId"]) for t in raw_tasks if t["parentId"]}
    by_id = {str(t["id"]): t for t in raw_tasks}

    tasks = []
    for t in raw_tasks:
        tid_str = str(t["id"])
        is_parent_container = (tid_str in all_parent_ids) or (t["tracker"] == "상위일감 관리")

        # 일반 수행 일감만 진척도 100% 시 완료 보정 (상위일감 컨테이너는 하위 평균 진척도가 100%여도 실제 상태 유지)
        status = t["status"]
        progress = t["progress"]
        if progress >= 100 and not is_parent_container:
            status = "완료"
            progress = 100

        t["status"] = status
        t["progress"] = progress
        t["isParentContainer"] = is_parent_container

        # 최상위 루트 상위일감 컨테이너(부모가 없는 '상위일감 관리' 등, 예: #12921, #10749)는
        # 하위 일감의 그룹 헤더(- 상위일감 #12921)로만 쓰이므로 개별 수행 태스크 목록에서 제외
        if is_parent_container and not t["parentId"]:
            continue

        # 다단계 상위일감(최상위 상위일감 -> 중간 상위일감 -> 일반일감) 추적
        parent_id = t["parentId"]
        mid_parent_id = None
        root_group_id = parent_id if parent_id else tid_str
        is_real_group = bool(parent_id)

        if parent_id and parent_id in by_id:
            parent_obj = by_id[parent_id]
            if parent_obj.get("parentId"):
                # 부모에게 또 상위일감이 있는 경우 (3단계 계층)
                mid_parent_id = parent_id
                root_group_id = str(parent_obj["parentId"])
                is_real_group = True

        t["groupId"] = root_group_id
        t["midParentId"] = mid_parent_id
        t["isRealGroup"] = is_real_group
        tasks.append(t)

    return tasks


def identify_long_term_tasks(tasks: list, base_date: datetime.date = None):
    """등록일이 90일(3개월) 이상 경과한 '신규' 또는 '진행중' 일반 일감을 식별합니다."""
    if base_date is None:
        base_date = get_korea_today()
    threshold_dt = datetime.datetime(base_date.year, base_date.month, base_date.day) - datetime.timedelta(days=90)

    long_term_tasks = []
    for t in tasks:
        if t.get("isParentContainer"):
            continue
        if t["status"] in ["신규", "진행중"] and t["createDate"]:
            if t["createDate"] < threshold_dt:
                elapsed_days = (datetime.datetime(base_date.year, base_date.month, base_date.day) - t["createDate"]).days
                long_term_tasks.append((t, elapsed_days))

    return long_term_tasks


def generate_weekly_report_text(tasks: list, monday_start: datetime.datetime, friday_end: datetime.datetime,
                                pm_items: list, allowed_long_term_ids: set, base_date: datetime.date = None):
    """
    Untitled-1.html 가공 로직, 과거 완료기한 제외 규칙 및 다단계 상위일감 계층 들여쓰기 규칙을 적용하여 보고서 텍스트를 생성합니다.
    """
    if base_date is None:
        base_date = get_korea_today()
    threshold_dt = datetime.datetime(base_date.year, base_date.month, base_date.day) - datetime.timedelta(days=90)

    by_id = {str(t["id"]): t for t in tasks}

    def passes_weekly_filter(t):
        status = t["status"]
        if status in ["신규", "진행중"]:
            if t["createDate"] and t["createDate"] < threshold_dt:
                if str(t["id"]) not in allowed_long_term_ids:
                    return False, "long_term"
            return True, None
        elif status == "완료":
            if not t["modDate"] or t["modDate"] < monday_start or t["modDate"] > friday_end:
                return False, "not_this_week"
            if t["dueDate"] and t["dueDate"] < monday_start:
                return False, "past_due"
            return True, None
        return False, "other"

    leaf_report_tasks = []
    excluded_past_completed_count = 0
    excluded_long_term_count = 0

    for t in tasks:
        if t.get("isParentContainer"):
            continue
        ok, reason = passes_weekly_filter(t)
        if not ok:
            if reason == "long_term":
                excluded_long_term_count += 1
            elif reason == "past_due":
                excluded_past_completed_count += 1
                print(f"[*] [과거 완료건 제외] #{t['id']} {t['title'][:25]}... (완료기한: {t['dueDate'].strftime('%Y-%m-%d')} < 이번주 월요일)")
            continue
        leaf_report_tasks.append(t)

    # 금주 보고 대상 하위 일감을 보유한 중간 상위일감 ID 집합
    active_mid_parent_ids = {str(t["midParentId"]) for t in leaf_report_tasks if t.get("midParentId")}

    # 중간 상위일감 중:
    # - 금주 보고되는 하위 일감이 없고
    # - 단순 '상위일감 관리' 껍데기가 아닌 실제 수행 일감(오류-개발 등)이면서 금주 필터를 통과하는 경우
    #   누락되지 않도록 1단계 일감으로 포함
    extra_mid_tasks = []
    for t in tasks:
        if not t.get("isParentContainer"):
            continue
        if t["tracker"] == "상위일감 관리":
            continue
        if str(t["id"]) in active_mid_parent_ids:
            continue
        ok, _ = passes_weekly_filter(t)
        if ok:
            extra_mid_tasks.append(t)

    report_tasks = leaf_report_tasks + extra_mid_tasks

    print(f"[*] 필터링 완료: 보고서 포함 건수 = {len(report_tasks)}건 (과거 완료건 제외: {excluded_past_completed_count}건, 장기 미선택 제외: {excluded_long_term_count}건)")

    # 제목 기준 정렬 (자연 정렬 지원: [#11737-2] -> [#11737-10])
    def natural_title_key(item):
        parts = re.split(r"(\d+)", str(item.get("title", "")))
        return [int(p) if p.isdigit() else p for p in parts]

    report_tasks.sort(key=natural_title_key)

    # 범주 -> 최상위 그룹(gid) -> (일반 items + 중간 상위일감 subGroups) 계층 구조 생성
    hierarchy = {}
    for t in report_tasks:
        cat = t["category"]
        gid = str(t["groupId"])
        if cat not in hierarchy:
            hierarchy[cat] = {}
        if gid not in hierarchy[cat]:
            hierarchy[cat][gid] = {
                "direct_items": [],
                "sub_groups": {},
                "isGroup": bool(t["isRealGroup"]),
            }
        else:
            if t["isRealGroup"]:
                hierarchy[cat][gid]["isGroup"] = True

        mid_id = t.get("midParentId")
        if mid_id:
            mid_id_str = str(mid_id)
            if mid_id_str not in hierarchy[cat][gid]["sub_groups"]:
                hierarchy[cat][gid]["sub_groups"][mid_id_str] = []
            hierarchy[cat][gid]["sub_groups"][mid_id_str].append(t)
        else:
            hierarchy[cat][gid]["direct_items"].append(t)

    # 텍스트 출력 서식 조립
    date_header = f"## 주간업무보고 ({monday_start.strftime('%Y. %m. %d.')} ~ {friday_end.strftime('%Y. %m. %d.')})\n\n\n"
    output = date_header

    section_num = 1
    if pm_items:
        output += f"({section_num}) 사업관리\n"
        for item in pm_items:
            output += f"   - {item}\n"
        output += "\n\n"
        section_num += 1

    new_count = len([t for t in report_tasks if t["status"] == "신규"])
    prog_count = len([t for t in report_tasks if t["status"] == "진행중"])
    comp_count = len([t for t in report_tasks if t["status"] == "완료"])

    output += f"({section_num}) 수행\n"
    output += f"   - 신규: {new_count}건\n"
    output += f"   - 진행중: {prog_count}건\n"
    output += f"   - 완료(금주): {comp_count}건\n\n"
    output += "[업무 세부 내역]\n"

    for cat in sorted(hierarchy.keys()):
        output += f"▶ {cat}\n"
        groups = hierarchy[cat]
        sorted_gids = sorted(groups.keys(), key=lambda x: int(x) if x.isdigit() else 0)
        for gid in sorted_gids:
            g = groups[gid]
            if g["isGroup"]:
                output += f"  - 상위일감 #{gid}\n"
            else:
                output += f"  - #{gid}\n"

            # 1) 최상위 그룹 직속 일감 (1단계 들여쓰기: "   . ")
            for t in g["direct_items"]:
                prog = f" ({t['progress']}%)" if t["status"] != "완료" else ""
                output += f"   . [{t['status']}] {t['title']}{prog}\n"

            # 2) 중간 상위일감이 있는 3단계 계층 (1단계: 중간 상위일감 "   . ", 2단계: 하위 일반일감 "     . ")
            sorted_mid_ids = sorted(g["sub_groups"].keys(), key=lambda x: int(x) if x.isdigit() else 0)
            for mid_id in sorted_mid_ids:
                child_items = g["sub_groups"][mid_id]
                mid_obj = by_id.get(mid_id)
                if mid_obj:
                    mid_status = mid_obj["status"]
                    if mid_obj["tracker"] == "상위일감 관리" and all(c["status"] == "완료" for c in child_items):
                        mid_status = "완료"
                    mid_prog = f" ({mid_obj['progress']}%)" if mid_status != "완료" else ""
                    mid_title = mid_obj["title"]
                else:
                    mid_status = "완료" if all(c["status"] == "완료" for c in child_items) else "진행중"
                    mid_prog = ""
                    mid_title = child_items[0].get("parentSubject") or f"상위일감 #{mid_id}"

                output += f"   . [{mid_status}] {mid_title}{mid_prog}\n"
                for c in child_items:
                    c_prog = f" ({c['progress']}%)" if c["status"] != "완료" else ""
                    output += f"     . [{c['status']}] {c['title']}{c_prog}\n"

        output += "\n"

    total_completed = len([t for t in tasks if not t.get("isParentContainer") and t["status"] == "완료"])
    output += f"※ 전체 누적 완료건(현재 기준): {total_completed}건\n"

    return output


def build_next_week_plan_text(report_text: str) -> str:
    """
    차주 계획용 텍스트를 생성합니다.
    - 첫 줄의 '## 주간업무보고 (...)'와 요약 통계((1) 수행 등)를 제거하고 '## 차주 계획'으로 시작합니다.
    - '[업무 세부 내역]'부터 세부 업무 항목만 전달하되, 이번 주에 이미 완료된 '. [완료]' 건은 제외합니다.
    - 3단계 계층(중간 상위일감 '   . ' 및 하위 일반일감 '     . ')의 경우 하위 미완료 건이 남아 있으면 중간 상위일감 라인도 보존합니다.
    - '[완료]' 건 제외 후 하위 항목이 하나도 남지 않은 상위일감/일감 그룹(- #...) 및 범주(▶ ...)도 함께 제외합니다.
    """
    marker = "[업무 세부 내역]"
    if marker in report_text:
        detail_part = report_text[report_text.index(marker):].strip()
    else:
        detail_part = report_text.strip()

    lines = detail_part.splitlines()
    filtered_lines = []
    current_cat_line = None
    current_cat_groups = []
    current_group_line = None
    current_group_items = []
    pending_mid_line = None
    pending_mid_children = []

    def flush_mid():
        nonlocal pending_mid_line, pending_mid_children
        if pending_mid_line is not None:
            if pending_mid_children:
                current_group_items.append(pending_mid_line)
                current_group_items.extend(pending_mid_children)
            elif not pending_mid_line.strip().startswith(". [완료]"):
                current_group_items.append(pending_mid_line)
            pending_mid_line = None
            pending_mid_children = []

    def flush_group():
        nonlocal current_group_line, current_group_items
        flush_mid()
        if current_group_line is not None:
            if current_group_items:
                current_cat_groups.append((current_group_line, list(current_group_items)))
            current_group_line = None
            current_group_items = []

    def flush_cat():
        nonlocal current_cat_line, current_cat_groups
        flush_group()
        if current_cat_line is not None:
            if current_cat_groups:
                filtered_lines.append(current_cat_line)
                for g_line, g_items in current_cat_groups:
                    filtered_lines.append(g_line)
                    filtered_lines.extend(g_items)
                filtered_lines.append("")
            current_cat_line = None
            current_cat_groups = []

    for line in lines:
        stripped = line.strip()
        leading_spaces = len(line) - len(line.lstrip(" "))
        if stripped == marker:
            filtered_lines.append(marker)
        elif stripped.startswith("▶"):
            flush_cat()
            current_cat_line = line
        elif stripped.startswith("- "):
            flush_group()
            current_group_line = line
        elif stripped.startswith(". "):
            if leading_spaces >= 5:
                # 2단계 하위 일반일감 ("     . ")
                if not stripped.startswith(". [완료]"):
                    pending_mid_children.append(line)
            else:
                # 1단계 하위 일감 또는 중간 상위일감 ("   . ")
                flush_mid()
                pending_mid_line = line
        elif stripped.startswith("※"):
            flush_cat()

    flush_cat()
    detail_result = "\n".join(filtered_lines).strip()
    return f"## 차주 계획\n\n\n{detail_result}\n"


# ==============================================================================
# 2. Playwright(CDP) 자동화 클래스
# ==============================================================================
class PmsWeeklyReportBot:
    def __init__(self, cdp_url="http://localhost:9222", work_dir=None):
        self.cdp_url = cdp_url
        if work_dir is None:
            self.work_dir = get_base_dir()
        else:
            self.work_dir = Path(work_dir).resolve()

        self.downloads_dir: Path = None
        self.weekly_csv_file: Path = None

        self.playwright = None
        self.browser = None

    def ensure_downloads_dir(self) -> Path:
        """다운로드 직전 실행일자 기반 임시 폴더(downloads_YYYYMMDD)를 생성합니다."""
        if self.downloads_dir is None or not self.downloads_dir.exists():
            self.downloads_dir = get_safe_downloads_dir(self.work_dir)
            self.weekly_csv_file = self.downloads_dir / "weekly_issues.csv"
        return self.downloads_dir

    def cleanup_downloads_dir(self):
        """사용 완료된 임시 다운로드 폴더(downloads_YYYYMMDD)만 안전하게 삭제합니다."""
        if (
            self.downloads_dir is not None
            and self.downloads_dir.exists()
            and self.downloads_dir.is_dir()
            and self.downloads_dir.name.startswith("downloads_")
        ):
            shutil.rmtree(self.downloads_dir, ignore_errors=True)
            print(f"[*] 임시 다운로드 폴더 자동 삭제 완료: {self.downloads_dir}")
        self.downloads_dir = None
        self.weekly_csv_file = None

    def connect(self):
        """Chrome 디버깅 포트에 CDP로 연결합니다."""
        print(f"[*] Chrome 원격 디버깅 세션(CDP) 연결 시도: {self.cdp_url}")
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(self.cdp_url)
            print("[+] Chrome CDP 연결 성공!")
        except Exception as e:
            raise ConnectionError(
                f"Chrome CDP({self.cdp_url})에 연결할 수 없습니다.\n"
                f"Chrome이 9222 포트로 실행 중인지 확인해주세요.\n"
                f"실행 예: start_chrome_debug.bat 실행\n"
                f"상세 에러: {e}"
            )

    def close(self):
        """연결 정리 및 임시 다운로드 폴더 자동 삭제"""
        self.cleanup_downloads_dir()
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
            self.browser = None
            print("[*] CDP 연결 세션 종료.")

    def get_or_create_page(self, url_keyword: str) -> Page:
        """열려 있는 탭 중 url_keyword를 포함하는 페이지를 찾거나, 없으면 새 페이지를 엽니다."""
        for ctx in self.browser.contexts:
            for p in ctx.pages:
                if url_keyword in p.url:
                    print(f"[*] 기존 탭 탐색 성공: {p.url}")
                    return p
        print(f"[*] '{url_keyword}' 관련 탭이 열려있지 않아 새 탭을 생성합니다.")
        if self.browser.contexts:
            return self.browser.contexts[0].new_page()
        raise RuntimeError("브라우저 컨텍스트를 찾을 수 없습니다.")

    # --------------------------------------------------------------------------
    # Step 1: Redmine CSV 다운로드
    # --------------------------------------------------------------------------
    def step1_download_redmine_csv(self) -> Path:
        print("\n=== [Step 1] Redmine 내 작업 일감 CSV 다운로드 ===")
        self.ensure_downloads_dir()
        page = self.get_or_create_page("support.kyci.or.kr")
        print(f"[*] Redmine 내 작업 일감 페이지로 이동/확인: {REDMINE_MY_ISSUES_URL}")
        page.goto(REDMINE_MY_ISSUES_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        csv_link = page.locator("p.other-formats a.csv, a.csv, a:has-text('CSV')").first
        print("[*] [내보내기] -> [CSV] 클릭 및 '모든 컬럼(all_inline)' 다운로드 진행...")

        # 1) 클릭 전 숨겨진 템플릿(#csv-export-options) 내 '모든 컬럼(all_inline)' 라디오 버튼을 미리 checked 상태로 설정
        try:
            page.evaluate("""() => {
                document.querySelectorAll("input[type='radio'][name='c[]'], input[type='radio'][name='columns']").forEach(el => {
                    el.checked = false;
                    el.removeAttribute('checked');
                });
                document.querySelectorAll(
                    "input#c__all_inline, input[value='all_inline'], input[name='c[]'][value='all_inline'], input#columns_all, input[value='all']"
                ).forEach(el => {
                    el.checked = true;
                    el.setAttribute('checked', 'checked');
                });
            }""")
        except Exception:
            pass

        with page.expect_download() as download_info:
            csv_link.click()
            # 2) Redmine의 showModal()은 #csv-export-options를 #ajax-modal로 복제하여 띄우므로
            #    잠시 대기 후 실제 화면에 표시된 모달(#ajax-modal / .ui-dialog)에서 '모든 컬럼(c__all_inline)' 선택 후 [내보내기] 클릭
            page.wait_for_timeout(600)
            try:
                modal_res = page.evaluate("""() => {
                    const allRadios = document.querySelectorAll(
                        "#ajax-modal input#c__all_inline, #ajax-modal input[value='all_inline'], " +
                        ".ui-dialog input#c__all_inline, .ui-dialog input[value='all_inline'], " +
                        "input#c__all_inline, input[value='all_inline'], input#columns_all, input[value='all']"
                    );
                    let radioChecked = false;
                    allRadios.forEach(r => {
                        r.checked = true;
                        if (r.offsetParent !== null) {
                            r.click();
                            radioChecked = true;
                        }
                    });

                    const submits = document.querySelectorAll(
                        "#ajax-modal input[type='submit'], .ui-dialog input[type='submit'], input[value='내보내기'], #csv-export-form input[type='submit']"
                    );
                    for (const btn of submits) {
                        if (btn.offsetParent !== null) {
                            btn.click();
                            return "VISIBLE_MODAL_SUBMITTED (radio=" + radioChecked + ")";
                        }
                    }
                    if (submits.length > 0) {
                        submits[submits.length - 1].click();
                        return "FALLBACK_MODAL_SUBMITTED (radio=" + radioChecked + ")";
                    }
                    return "NO_SUBMIT_BTN";
                }""")
                print(f"[*] CSV 내보내기 모달 자동 처리 완료: {modal_res}")
            except Exception as e:
                print(f"[*] CSV 옵션 모달 처리 예외 (무시 가능): {e}")

        download = download_info.value
        if self.weekly_csv_file.exists():
            self.weekly_csv_file.unlink()

        download.save_as(str(self.weekly_csv_file))
        print(f"[+] weekly_issues.csv 다운로드 성공: {self.weekly_csv_file}")

        return self.weekly_csv_file

    # --------------------------------------------------------------------------
    # Step 3: PMS 주간보고 화면 입력 및 저장
    # --------------------------------------------------------------------------
    def step3_submit_to_pms(self, report_text: str):
        print("\n=== [Step 3] PMS 주간보고 등록 화면 입력 및 저장 ===")
        page = self.get_or_create_page("pms.metabuild.co.kr")
        
        # PMS 다이얼로그(alert/confirm/beforeunload 나가기 확인창) 자동 수락 핸들러 등록
        def handle_dialog(dialog):
            print(f"[!] PMS 알림/확인 팝업 감지: '{dialog.message}' -> 자동 확인(Accept) 처리")
            dialog.accept()

        page.on("dialog", handle_dialog)

        # 0) 나가기 오류 방지를 위해 목록 화면으로 먼저 이동한 뒤 [등록] 버튼을 클릭해 진입
        list_url = "https://pms.metabuild.co.kr/mypage/wkly/list"
        print(f"[*] 0-1단계: PMS 주간보고 목록 페이지로 이동: {list_url}")
        page.goto(list_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        # 0-2단계: 페이지 진입 시 자동으로 활성화되는 알림창/오버레이 포커스를 해제하기 위해
        # 사용자 제안에 따라 화면 타이틀인 '주간보고' 텍스트 영역을 먼저 클릭합니다.
        try:
            title_elem = page.locator("span.text-h5.font-weight-black:has-text('주간보고'), span.text-h5:has-text('주간보고'), span:has-text('주간보고')").first
            if title_elem.is_visible(timeout=3000):
                print("[*] 0-2단계: 알림창 포커스 해제를 위해 '주간보고' 타이틀 영역 클릭")
                title_elem.click()
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
        except Exception as e:
            print(f"[*] 알림창 포커스 해제 클릭 시도 예외 (무시 가능): {e}")

        # 목록 화면의 [등록] 버튼 탐색 및 클릭 (Vuetify v-btn 컴포넌트 특성 완벽 반영)
        reg_btn = page.locator(
            "button.v-btn.bg-primary:has-text('등록'), "
            "button.v-btn:has(span.v-btn__content:has-text('등록')), "
            "button.v-btn:has-text('등록'), "
            "button:has-text('등록')"
        ).first

        if reg_btn.is_visible(timeout=5000):
            print("[*] 0-3단계: 목록 화면에서 [등록] 버튼 클릭하여 안전하게 등록 폼 진입")
            try:
                reg_btn.click()
            except Exception:
                reg_btn.click(force=True)
            page.wait_for_timeout(1500)
            page.wait_for_load_state("domcontentloaded")
        else:
            print("[!] 목록 화면에서 [등록] 버튼을 직접 찾지 못해 /save 주소로 직접 이동합니다.")
            pms_url = "https://pms.metabuild.co.kr/mypage/wkly/save"
            page.goto(pms_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

        print(f"[+] 주간보고 등록 폼 진입 완료: {page.url}")

        # 1) 진척률(%) 입력: 100
        # 라벨 '진척률' 상위 컨테이너(.wkly-field-col) 내 실제 입력란을 즉시 타겟팅하여 30초 대기 없이 0.1초 내 초고속 주입
        print("[*] 1. 진척률(100) 입력 시도...")
        prgr_input = page.locator("span:has-text('진척률')").locator("xpath=ancestor::div[contains(@class, 'wkly-field-col')]//input").first

        try:
            prgr_input.wait_for(state="attached", timeout=5000)
            prgr_input.evaluate("""el => {
                el.focus();
                el.value = '100';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""")
            page.keyboard.press("Tab")
            current_val = prgr_input.input_value()
            print(f"[+] 라벨 상위 컨테이너 기반 진척률 입력 완료 (확인된 값: {current_val}%)")
        except Exception as e:
            print(f"[!] 1차 진척률 입력 시도 중 예외 ({e}), 보조 셀렉터 시도...")
            backup_input = page.locator(".wkly-field-box--rate input, input[min='0'][max='100']:not([readonly])").first
            backup_input.evaluate("""el => {
                el.focus();
                el.value = '100';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""")
            print(f"[+] 보조 셀렉터 기반 진척률 입력 완료 (확인된 값: {backup_input.input_value()}%)")

        # 2) 보고 내용 주입 ('금주 실적' 및 '차주 계획' ProseMirror 에디터)
        print("[*] 2. Tiptap ProseMirror 에디터 내용 주입 시도...")
        editors = page.locator("div.tiptap.ProseMirror[contenteditable='true']")
        editor_count = editors.count()
        print(f"[*] 감지된 Tiptap ProseMirror 에디터 수: {editor_count}개")

        # 차주 계획 텍스트 생성 (첫 줄 차주 계획 변경 & 요약 통계 제외 후 세부 내역만 반영)
        next_week_text = build_next_week_plan_text(report_text)

        # 줄바꿈 텍스트를 ProseMirror용 <p> HTML로 변환하는 헬퍼 함수 (들여쓰기 공백 &nbsp; 보존)
        def to_tiptap_html(text_content: str) -> str:
            html_paragraphs = []
            for line in text_content.split("\n"):
                rstrip_line = line.rstrip("\r\n")
                if rstrip_line.strip():
                    leading_spaces = 0
                    for ch in rstrip_line:
                        if ch in (' ', '\xa0', '\t'):
                            leading_spaces += 1
                        else:
                            break
                    indent_html = "&nbsp;" * leading_spaces
                    content = rstrip_line[leading_spaces:]
                    escaped = (content.replace("&", "&amp;")
                                      .replace("<", "&lt;")
                                      .replace(">", "&gt;"))
                    html_paragraphs.append(f"<p>{indent_html}{escaped}</p>")
                else:
                    html_paragraphs.append("<p><br></p>")
            return "".join(html_paragraphs)

        this_week_html = to_tiptap_html(report_text)
        next_week_html = to_tiptap_html(next_week_text)

        def set_editor_content(editor_locator, html_str):
            try:
                editor_locator.click()
            except Exception:
                pass
            editor_locator.evaluate("""
                (el, html) => {
                    el.focus();
                    el.innerHTML = html;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
            """, html_str)

        if editor_count >= 2:
            # 1번째 에디터: 금주 실적 / 진척사항
            print("[*] '금주 실적' 에디터에 주간보고 내용 주입")
            set_editor_content(editors.nth(0), this_week_html)
            page.wait_for_timeout(500)

            # 2번째 에디터: 차주 계획 / 진행예정 사항
            print("[*] '차주 계획' 에디터에 차주 계획 내용 주입 (첫 줄 ## 차주 계획, 수행요약 제외 후 업무 세부 내역만)")
            set_editor_content(editors.nth(1), next_week_html)
            page.wait_for_timeout(500)
        elif editor_count == 1:
            print("[*] 단일 에디터에 내용 주입")
            set_editor_content(editors.nth(0), this_week_html)
        else:
            print("[!] Tiptap ProseMirror 에디터를 찾지 못했습니다. 일반 textarea를 탐색합니다.")
            textareas = page.locator("textarea")
            if textareas.count() >= 2:
                textareas.nth(0).fill(report_text)
                textareas.nth(1).fill(next_week_text)
            elif textareas.count() == 1:
                textareas.nth(0).fill(report_text)

        page.wait_for_timeout(1000)

        # 3) 상단 [저장] 버튼 클릭 및 2차 SweetAlert2 팝업 [저장] 버튼 클릭
        print("[*] 3-1단계: 상단 [저장] 버튼 클릭 시도...")
        save_btn = page.locator(
            "button.v-btn.bg-primary:has-text('저장'), "
            "button.v-btn:has(span.v-btn__content:has-text('저장')), "
            "button.v-btn:has-text('저장'), "
            "button:has-text('저장'), "
            "a:has-text('저장')"
        ).first
        save_btn.click()
        print("[+] 상단 [저장] 버튼 클릭 완료. 저장 확인 팝업 대기...")
        page.wait_for_timeout(1200)

        # 3-2단계: SweetAlert2 저장 확인 팝업 ("저장하시겠습니까?")의 [저장] 버튼 클릭
        print("[*] 3-2단계: SweetAlert2 저장 확인 팝업 처리 중...")
        swal_confirm_btn = page.locator(
            "button.swal2-confirm.custom-swal2-confrim, "
            "button.swal2-confirm:has-text('저장'), "
            "button.swal2-styled:has-text('저장')"
        ).first

        try:
            if swal_confirm_btn.is_visible(timeout=5000):
                print("[+] 저장 확인 팝업 감지: [저장] 버튼 클릭")
                swal_confirm_btn.click()
                page.wait_for_timeout(1500)
            else:
                print("[*] SweetAlert2 저장 팝업이 표시되지 않았거나 이미 처리되었습니다.")
        except Exception as e:
            print(f"[*] 확인 팝업 처리 중 예외 (무시 가능): {e}")

        # 3-3단계: 저장 완료 안내 팝업(예: '저장되었습니다' 확인 버튼) 처리
        try:
            swal_ok_btn = page.locator(
                "button.swal2-confirm:has-text('확인'), "
                "button.swal2-confirm"
            ).first
            if swal_ok_btn.is_visible(timeout=3000):
                print("[+] 저장 완료 안내 팝업 감지: [확인] 버튼 클릭")
                swal_ok_btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        print("\n" + "=" * 68)
        print(" [✔] PMS 주간보고 1차 등록(임시저장) 완료!")
        print("=" * 68)
        print(" 📌 [필독 안내]")
        print("    현재 PMS 시스템에 '작성중' 상태로 1차 저장이 완료되었습니다.")
        print("    열려 있는 브라우저 화면에서 입력된 내용을 최종 확인하신 후,")
        print("    진행상태를 [제출]로 변경하여 최종 제출을 완료해 주세요.")
        print("=" * 68)


# ==============================================================================
# 메인 CLI 인터페이스 (대화형 실행)
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="PMS 주간보고 자동화 스크립트")
    parser.add_argument("--cdp-url", default="http://localhost:9222", help="Chrome 원격 디버깅 CDP URL")
    parser.add_argument("--date", help="기준 실행일자 (YYYY-MM-DD, 기본값: 한국 시간 오늘)")
    parser.add_argument("--csv", help="로컬 CSV 파일 직접 지정 (Redmine 다운로드 생략)")
    parser.add_argument("--skip-pms", action="store_true", help="PMS 입력 및 저장 건너뛰기 (텍스트 생성만 수행)")
    parser.add_argument("--skip-update", action="store_true", help="GitHub 자동 업데이트 확인 건너뛰기")
    parser.add_argument("--auto-yes", "-y", action="store_true", help="모든 대화형 확인 프롬프트를 기본값으로 자동 통과")

    args = parser.parse_args()

    # 기준일자 및 주간 범위 계산 (금요일 기준)
    if args.date:
        base_dt = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        base_dt = get_korea_today()

    monday_start, friday_end = get_this_week_range(base_dt)
    if USER_NOTICE_MESSAGE and not _NOTICE_PRINTED:
        print(USER_NOTICE_MESSAGE)
    print("=" * 65)
    print(f" PMS 주간보고 자동화 동기화 ({CURRENT_VERSION})")
    print(f" - 기준일자: {base_dt.strftime('%Y-%m-%d')} (금요일 기준)")
    print(f" - 이번 주 보고 대상 범위: {monday_start.strftime('%Y-%m-%d')} ~ {friday_end.strftime('%Y-%m-%d')}")
    print("=" * 65)

    base_dir = get_base_dir()
    bot = PmsWeeklyReportBot(cdp_url=args.cdp_url, work_dir=base_dir)

    # --------------------------------------------------------------------------
    # 0. Chrome 디버깅 세션 자동 실행 및 로그인 확인
    # --------------------------------------------------------------------------
    if not args.csv:
        if not is_port_open(port=9222):
            print("\n[*] Chrome 디버깅 세션(포트 9222)이 실행되어 있지 않습니다.")
            print("[*] Chrome 브라우저를 디버깅 모드로 자동 실행합니다...")
            launch_chrome_debug_session(base_dir)
        else:
            print("[*] 기존 Chrome 디버깅 세션(포트 9222)이 감지되었습니다.")

        if not args.auto_yes:
            print("\n" + "=" * 68)
            print(" [★필독★ Chrome 브라우저 로그인 확인 안내]")
            print("=" * 68)
            print(" ⚠️  [주의] 기존에 열려 있던 개인 Chrome 브라우저 창이 아닙니다!")
            print("     방금 이 프로그램이 새로 띄운 [원격 디버깅 전용 Chrome 창]에서")
            print("     반드시 로그인을 완료해 주셔야 자동화가 정상 동작합니다.")
            print("-" * 68)
            print(" 새로 열린 디버깅 전용 Chrome 창의 2개 탭을 확인해주세요:")
            print("   - 1번째 탭: Redmine 내 작업 일감 목록")
            print("   - 2번째 탭: PMS 주간보고 목록")
            print("\n * 두 사이트 로그인 완료 후 Enter를 누르면 1번째 탭을 원래 지정된")
            print("   Redmine 일감 목록 페이지로 자동 이동하고 다음 단계로 진행합니다.")
            print("=" * 68)

            try:
                input("\n[?] 두 사이트 모두 로그인을 완료하셨습니까? (완료 후 Enter): ")
            except (EOFError, KeyboardInterrupt):
                pass

            print("\n[*] 1번째 탭을 지정된 Redmine 내 작업 일감 페이지로 자동 이동시킵니다...")
            try:
                if not bot.browser:
                    bot.connect()
                p_redmine = bot.get_or_create_page("support.kyci.or.kr")
                p_redmine.goto(REDMINE_MY_ISSUES_URL, wait_until="domcontentloaded")
                print("[+] 1번째 탭(Redmine 내 일감 목록)으로 이동 완료!")
            except Exception as e:
                print(f"[*] 1번째 탭 이동 중 예외 (무시 가능): {e}")

    # --------------------------------------------------------------------------
    # Step 1: CSV 확보
    # --------------------------------------------------------------------------
    if args.csv:
        csv_path = Path(args.csv).resolve()
        print(f"[*] 지정된 로컬 CSV 파일 사용: {csv_path}")
    else:
        if not bot.browser:
            bot.connect()
        try:
            csv_path = bot.step1_download_redmine_csv()
        except Exception as e:
            print(f"[!] Redmine CSV 다운로드 실패: {e}")
            bot.close()
            return False

    # --------------------------------------------------------------------------
    # Step 2: 대화형 확인 및 데이터 가공
    # --------------------------------------------------------------------------
    csv_rows = load_redmine_issues_csv(str(csv_path))
    if not args.csv:
        bot.cleanup_downloads_dir()
    tasks = parse_tasks_from_rows(csv_rows)
    print(f"[*] 유효 파싱 일감 수: {len(tasks)}건")

    # (1) [대화형 입력 1] 사업관리 항목 선택
    selected_pm_items = []
    print("\n------------------------------------------------------------")
    print(" [대화형 확인 1] 사업관리 항목 포함 여부")
    print("------------------------------------------------------------")
    if args.auto_yes:
        print("[*] --auto-yes 옵션으로 사업관리 미포함(N) 기본값 적용")
    else:
        try:
            pm_ans = input("[?] 사업관리 항목을 보고서에 포함하시겠습니까? (y/N, 기본값: N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            pm_ans = "n"

        if pm_ans in ["y", "yes"]:
            print("\n선택 가능한 사업관리 항목 목록:")
            print("  (1) 주간보고")
            print("  (2) 휴가신청 관련 처리")
            print("  (3) 인력 변동 관련 처리")
            print("  (4) 안전망시스템공지사항 등록")
            print("  (5) 월간보고")
            pm_map = {
                "1": "주간보고",
                "2": "휴가신청 관련 처리",
                "3": "인력 변동 관련 처리",
                "4": "안전망시스템공지사항 등록",
                "5": "월간보고"
            }
            try:
                pick = input("[?] 포함할 항목 번호를 입력하세요 (쉼표 또는 공백 구분, 예: 1,4): ").strip()
                tokens = re.split(r"[, \s]+", pick)
                for t in tokens:
                    if t in pm_map and pm_map[t] not in selected_pm_items:
                        selected_pm_items.append(pm_map[t])
            except (EOFError, KeyboardInterrupt):
                pass
            print(f"[*] 선택된 사업관리 항목: {selected_pm_items}")
        else:
            print("[*] 사업관리 항목 미포함 (수행 섹션 단독 구성)")

    # (2) [대화형 입력 2] 3개월(90일) 이상 장기 미완료 건 수기 확인
    long_term_list = identify_long_term_tasks(tasks, base_date=base_dt)
    allowed_long_term_ids = set()

    print("\n------------------------------------------------------------")
    print(" [대화형 확인 2] 3개월(90일) 이상 장기 미완료 건 수기 확인")
    print("------------------------------------------------------------")
    if not long_term_list:
        print("[*] 90일 이상 경과한 장기 신규/진행중 건이 없습니다.")
    else:
        print(f"[!] 등록일이 3개월(90일) 이상 경과한 장기 일감 총 {len(long_term_list)}건 발견:")
        for idx, (t, elapsed) in enumerate(long_term_list, start=1):
            created_str = t["createDate"].strftime("%Y-%m-%d") if t["createDate"] else "날짜미상"
            print(f"  [{idx}] #{t['id']} [{t['status']}] [등록일: {created_str} ({elapsed}일 경과)] {t['title'][:35]}")

        if args.auto_yes:
            print("[*] --auto-yes 옵션으로 장기 건 전체 미포함 적용")
        else:
            try:
                lt_ans = input("\n[?] 위 장기 건 중 이번 주간보고에 포함할 번호를 입력하세요\n    (Enter: 전체 미포함 / 번호 입력: 예: 1, 2 또는 13519, 13845 / 'all': 전체 포함): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                lt_ans = ""

            # [1]번, [2]번 형태의 목록 순번과 실제 일감번호(#) 둘 다 지원
            index_map = {str(idx): str(t["id"]) for idx, (t, _) in enumerate(long_term_list, start=1)}
            all_valid_ids = {str(t["id"]) for t, _ in long_term_list}

            if lt_ans == "all":
                allowed_long_term_ids = all_valid_ids
                print("[*] 장기 건 전체를 보고서에 포함합니다.")
            elif lt_ans:
                tokens = re.split(r"[, \s]+", lt_ans)
                selected_display = []
                for tok in tokens:
                    clean_tok = tok.replace("#", "").strip()
                    if clean_tok in index_map:
                        task_id = index_map[clean_tok]
                        allowed_long_term_ids.add(task_id)
                        selected_display.append(f"[{clean_tok}번 -> #{task_id}]")
                    elif clean_tok in all_valid_ids:
                        allowed_long_term_ids.add(clean_tok)
                        selected_display.append(f"#{clean_tok}")

                if allowed_long_term_ids:
                    print(f"[*] 선택된 장기 일감: {', '.join(selected_display)} (총 {len(allowed_long_term_ids)}건 포함)")
                else:
                    print("[!] 입력된 번호에 일치하는 장기 일감이 없어 제외 처리됩니다.")
            else:
                print("[*] 장기 건 전체를 보고서에서 제외합니다 (Enter 기본값).")

    # 텍스트 보고서 생성
    report_text = generate_weekly_report_text(
        tasks=tasks,
        monday_start=monday_start,
        friday_end=friday_end,
        pm_items=selected_pm_items,
        allowed_long_term_ids=allowed_long_term_ids,
        base_date=base_dt
    )

    print("\n" + "=" * 65)
    print(" [생성된 주간보고 내용 미리보기]")
    print("=" * 65)
    print(report_text)
    print("=" * 65)

    # --------------------------------------------------------------------------
    # Step 3: PMS 주간보고 등록 및 저장
    # --------------------------------------------------------------------------
    if args.skip_pms:
        print("[*] --skip-pms 옵션이 지정되어 PMS 등록은 건너뜁니다.")
        if bot.browser:
            bot.close()
        return False

    proceed_pms = False
    if args.auto_yes:
        proceed_pms = True
    else:
        try:
            pms_confirm = input("\n[?] 생성된 내용을 PMS(https://pms.metabuild.co.kr/mypage/wkly/save)에 입력하고 저장할까요? (y/N): ").strip().lower()
            proceed_pms = pms_confirm in ["y", "yes"]
        except (EOFError, KeyboardInterrupt):
            proceed_pms = False

    if proceed_pms:
        if not bot.browser:
            bot.connect()
        try:
            bot.step3_submit_to_pms(report_text)
            return True
        finally:
            bot.close()
    else:
        print("\n[*] PMS 등록이 보류되었습니다. 생성된 텍스트는 위 미리보기에서 복사하여 수동으로 활용하실 수 있습니다.")
        if bot.browser:
            bot.close()
        return False

    return False


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and "--skip-update" not in sys.argv:
        if check_and_apply_auto_update():
            sys.exit(0)

    saved_successfully = False
    try:
        saved_successfully = bool(main())
    except Exception as e:
        print(f"\n[오류 발생] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if getattr(sys, "frozen", False):
            try:
                if saved_successfully:
                    print("\n" + "-" * 68)
                    print(" ※ 브라우저에서 주간보고 내용을 최종 확인하시고,")
                    print("   진행상태를 [제출]로 변경하여 최종 제출을 완료해 주세요.")
                    print("-" * 68)
                    input("[완료] 확인 후 프로그램을 종료하려면 Enter 키를 누르세요...")
                else:
                    input("\n[완료] 프로그램을 종료하려면 Enter 키를 누르세요...")
            except Exception:
                pass
