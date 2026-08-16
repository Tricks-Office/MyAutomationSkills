"""kakaotalk_sender 진입점.

macOS에 설치되어 이미 로그인된 카카오톡 데스크톱 앱을 cliclick(실클릭)과
System Events(AppleScript)로 조작해, 지정한 대화방(들)에 텍스트 메시지를
전송한다. 요구사항은 docs/SRS.md 참고.

Phase 0 스파이크에서 확인된 대로, 이 앱의 커스텀 렌더링 UI 요소(채팅방 목록/
검색 결과 행, 전송 버튼)는 System Events의 click/AXPress/일부 keystroke에
안정적으로 반응하지 않는다. 방 열기·입력창 포커스·붙여넣기·전송은 cliclick으로
수행하고, 검색어 타이핑처럼 안정적으로 확인된 부분만 System Events keystroke를
사용한다.
"""
from __future__ import annotations

import argparse
import logging
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

KAKAOTALK_PROCESS_NAME = "KakaoTalk"
KAKAOTALK_MAIN_WINDOW_NAME = "카카오톡"
KAKAOTALK_APP_PATH = Path("/Applications/KakaoTalk.app")

# cron/launchd 등 비대화형 환경의 PATH에는 Homebrew 경로가 없을 수 있어(SRS 8절),
# shutil.which로 못 찾으면 이 경로들도 확인한다.
CLICLICK_COMMON_PATHS = [
    "/opt/homebrew/bin/cliclick",  # Apple Silicon Homebrew
    "/usr/local/bin/cliclick",  # Intel Homebrew
]

OSASCRIPT_TIMEOUT_SECONDS = 30
CLICLICK_TIMEOUT_SECONDS = 10
SEARCH_OPEN_DELAY_SECONDS = 0.5
SEARCH_TYPE_DELAY_SECONDS = 0.8
ROOM_OPEN_DELAY_SECONDS = 1.0
PASTE_DELAY_SECONDS = 0.6
SEND_VERIFY_POLL_INTERVAL_SECONDS = 0.5
# 대화 내역에서 보낸 메시지를 찾는 AppleScript 호출(message_appears_in_history)은
# 메시지가 길수록(예: 여러 항목을 담은 리포트) 문자열 비교에 걸리는 시간이 늘어나
# OSASCRIPT_TIMEOUT_SECONDS에 가깝게 걸릴 수 있음이 실제 운영(ai_news_telegram 연동,
# 약 2500자 리포트)에서 확인됐다. 짧게 잡으면 실제로는 전송에 성공했는데도 검증만
# 시간 초과로 실패 처리되는 오탐이 발생해, 최소 한 번은 넉넉히 기다리도록 설정한다.
SEND_VERIFY_TIMEOUT_SECONDS = 35
APP_READY_TIMEOUT_SECONDS = 10
APP_READY_POLL_INTERVAL_SECONDS = 0.5

_ACCESSIBILITY_DENIED_MARKERS = ("보조 접근", "not allowed", "not permitted")


class ArgumentError(ValueError):
    """CLI 인자 조합이 잘못됐을 때 발생 (SRS FR-1)."""


class CliclickNotFoundError(RuntimeError):
    """cliclick이 설치되어 있지 않을 때 발생 (SRS FR-3)."""


class FrontmostMismatchError(RuntimeError):
    """frontmost 안전장치(SRS FR-5) 발동 시 발생."""

    def __init__(self, actual: str):
        super().__init__(f"frontmost 프로세스가 KakaoTalk이 아닙니다: {actual}")
        self.actual = actual


class KakaoTalkNotInstalledError(RuntimeError):
    """KakaoTalk.app이 설치되어 있지 않을 때 발생 (SRS FR-4)."""


class KakaoTalkNotLoggedInError(RuntimeError):
    """앱은 실행되나 타임아웃 내에 메인 창(로그인 상태)이 나타나지 않을 때 발생 (SRS FR-4)."""


class AccessibilityPermissionError(RuntimeError):
    """손쉬운 사용(Accessibility) 권한이 거부됐을 때 발생 (SRS FR-16)."""

    def __init__(self, detail: str):
        super().__init__(
            "손쉬운 사용(Accessibility) 권한이 없습니다. 시스템 설정 > 개인정보 보호 및 "
            "보안 > 손쉬운 사용에서 권한을 부여한 뒤 다시 실행하세요. "
            f"(원본 오류: {detail})"
        )
        self.detail = detail


class ClamshellClosedError(RuntimeError):
    """맥북 뚜껑이 닫혀 있어 GUI 자동화가 불가능할 때 발생 (SRS FR-18)."""

    def __init__(self):
        super().__init__(
            "맥북 뚜껑(clamshell)이 닫혀 있습니다. 외부 모니터 없이 뚜껑이 닫히면 "
            "디스플레이/윈도우 서버가 비활성화돼 화면 자동화가 근본적으로 불가능합니다. "
            "뚜껑을 열어두거나 외부 모니터를 연결한 채로 실행하세요."
        )


def _is_accessibility_denied(error_message: str) -> bool:
    lowered = error_message.lower()
    return any(marker.lower() in lowered for marker in _ACCESSIBILITY_DENIED_MARKERS)


@dataclass
class RoomSendResult:
    room: str
    success: bool
    reason: str = ""


def _escape_applescript_string(text: str) -> str:
    """AppleScript 문자열 리터럴에 안전하게 넣기 위해 백슬래시/큰따옴표를 이스케이프한다."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=OSASCRIPT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript 실행 실패: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_cliclick_path() -> str | None:
    """cliclick 실행 파일의 절대 경로를 찾는다.

    cron/launchd 등 비대화형 환경에서는 PATH에 Homebrew 경로(`/opt/homebrew/bin`)가
    없어 `shutil.which`만으로는 찾지 못하는 사고가 실제로 있었다 — Hermes 크론으로
    실행된 `ai_news_telegram`이 텔레그램 발송은 성공하고 카카오톡 발송만 매번 조용히
    실패해, 실제 크론 환경의 PATH를 그대로 재현해보니 `cliclick`을 못 찾는 것으로
    확인됐다(SRS 8절). `PATH`에 없으면 Homebrew의 일반적인 설치 위치도 확인한다."""
    found = shutil.which("cliclick")
    if found:
        return found
    for candidate in CLICLICK_COMMON_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def run_cliclick(*commands: str) -> None:
    cliclick_path = resolve_cliclick_path()
    if cliclick_path is None:
        raise CliclickNotFoundError(
            "cliclick이 설치되어 있지 않습니다. 'brew install cliclick'으로 설치한 뒤 다시 실행하세요."
        )
    result = subprocess.run(
        [cliclick_path, *commands],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=CLICLICK_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cliclick 실행 실패: {result.stderr.strip()}")


def ensure_platform_macos() -> None:
    if platform.system() != "Darwin":
        raise ArgumentError("이 스킬은 macOS에서만 실행할 수 있습니다.")


def ensure_cliclick_installed() -> None:
    if resolve_cliclick_path() is None:
        raise CliclickNotFoundError(
            "cliclick이 설치되어 있지 않습니다. 'brew install cliclick'으로 설치한 뒤 다시 실행하세요."
        )


def is_clamshell_closed() -> bool | None:
    """맥북 뚜껑(clamshell)이 닫혀 있어 GUI 자동화가 실제로 불가능한 상태인지 확인한다.
    데스크톱 Mac 등 관련 속성이 없는 환경에서는 None을 반환한다(판단 불가 — 자동화를
    막지 않고 그대로 진행).

    Hermes 크론으로 매일 새벽 자동 실행됐을 때, 텔레그램은 성공했지만 카카오톡만
    계속 실패하는 사고가 있었다. `log show`로 실제 실행 시각의 시스템 로그를 확인해
    보니 그 시간대에 뚜껑이 계속 닫혀 있었다 — 외부 모니터가 없는 노트북은 뚜껑이
    닫히면 디스플레이/윈도우 서버가 꺼져 GUI 자동화가 근본적으로 불가능하다. 이전에는
    이 상태에서 각 단계가 개별적으로 타임아웃되며 조용히 실패해 원인 파악이 어려웠다.

    2026-08-16 재발 조사: 외부 모니터를 연결해뒀는데도 여전히 실패했다. `AppleClamshellState`
    만으로는 "뚜껑이 물리적으로 닫혀 있는지"만 알 수 있을 뿐, 외부 모니터+전원 연결로
    macOS가 실제로는 잠들지 않는 클램쉘 모드인지는 구분하지 못한다 — 그 시간대 로그에는
    `AppleClamshellState = Yes`와 함께 `inFullWake: YES`(디스플레이가 실제로 켜져 있음)가
    같이 찍혀 있었는데도 이 함수가 무조건 뚜껑 닫힘으로 판정해 어떤 화면 조작(osascript/
    cliclick)도 시도하기 전에 조기 실패했다. 같은 `ioreg` 출력에 있는
    `AppleClamshellCausesSleep`은 macOS 자신이 "현재 조건(외부 디스플레이+전원 연결 등)에서
    뚜껑을 닫아도 실제로 잠들지 않는다"고 판단했는지를 그대로 알려주는 속성이라, 외부
    모니터 유무를 별도로 조사하는 것보다 이 값을 그대로 신뢰하는 편이 더 정확하다."""
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    state_match = re.search(r'"AppleClamshellState"\s*=\s*(Yes|No)', result.stdout)
    if not state_match:
        return None
    if state_match.group(1) != "Yes":
        return False
    causes_sleep_match = re.search(r'"AppleClamshellCausesSleep"\s*=\s*(Yes|No)', result.stdout)
    if causes_sleep_match and causes_sleep_match.group(1) == "No":
        return False
    return True


def ensure_clamshell_open() -> None:
    if is_clamshell_closed():
        raise ClamshellClosedError()


def get_frontmost_process_name() -> str:
    script = (
        'tell application "System Events" '
        "to return name of first application process whose frontmost is true"
    )
    return run_applescript(script)


def ensure_kakaotalk_frontmost() -> None:
    """SRS FR-5 frontmost 안전장치: 실제로 다른 앱(VS Code)으로 키 입력이 샌
    오동작이 Phase 0에서 재현되어, 클릭/키 입력 직전마다 호출한다."""
    frontmost = get_frontmost_process_name()
    if frontmost != KAKAOTALK_PROCESS_NAME:
        raise FrontmostMismatchError(frontmost)


def ensure_kakaotalk_installed() -> None:
    if not KAKAOTALK_APP_PATH.exists():
        raise KakaoTalkNotInstalledError(
            f"{KAKAOTALK_APP_PATH}가 존재하지 않습니다. 카카오톡을 설치한 뒤 다시 실행하세요."
        )


def ensure_kakaotalk_ready() -> None:
    """SRS FR-4/FR-16: 앱을 활성화하고 지정 타임아웃(기본 10초) 내에 메인 창이
    나타나는지 확인한다. Accessibility 권한 거부는 즉시(재시도 없이) 구분해 실패
    처리하고, 메인 창이 없는 상태(로그인 안 됨 등)는 타임아웃까지 폴링한다."""
    ensure_kakaotalk_installed()

    try:
        get_frontmost_process_name()
    except RuntimeError as exc:
        if _is_accessibility_denied(str(exc)):
            raise AccessibilityPermissionError(str(exc)) from exc
        raise

    run_applescript(f'tell application "{KAKAOTALK_PROCESS_NAME}" to activate')

    escaped_window_name = _escape_applescript_string(KAKAOTALK_MAIN_WINDOW_NAME)
    check_window_script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        try
            set winEl to window "{escaped_window_name}"
            return "READY"
        on error
            return "NOT_READY"
        end try
    end tell
end tell
"""
    deadline = time.monotonic() + APP_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            status = run_applescript(check_window_script)
        except RuntimeError as exc:
            if _is_accessibility_denied(str(exc)):
                raise AccessibilityPermissionError(str(exc)) from exc
            raise
        if status == "READY":
            return
        time.sleep(APP_READY_POLL_INTERVAL_SECONDS)

    raise KakaoTalkNotLoggedInError(
        "카카오톡 메인 창을 찾을 수 없습니다. 로그인이 되어 있는지 확인한 뒤 다시 실행하세요."
    )


def set_clipboard(text: str) -> None:
    """로케일(LANG/LC_ALL) 미설정 환경에서 셸의 pbcopy가 한글을 깨뜨리는 문제가
    Phase 0에서 확인되어, UTF-8 인코딩을 명시해 바이트로 직접 전달한다."""
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def read_clipboard() -> str:
    result = subprocess.run(["pbpaste"], capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace")


def activate_kakaotalk_and_open_search() -> tuple[int, int, int, int]:
    """앱을 활성화하고 검색창을 연 뒤 위치를 반환한다. frontmost가 KakaoTalk이
    아니면 즉시 실패한다.

    Cmd+F(키보드 단축키)와 "검색" 버튼 클릭 모두 실제로는 토글처럼 동작해(이미 검색이
    열려 있으면 오히려 닫힘) 멱등적이지 않다는 것이 실제 환경 검증(Phase 1)에서
    확인됐다. 그래서 먼저 검색창(AXTextField)이 이미 열려 있는지 확인하고, 없을 때만
    "검색" 버튼을 클릭한다."""
    escaped_window_name = _escape_applescript_string(KAKAOTALK_MAIN_WINDOW_NAME)
    check_script = f"""
tell application "KakaoTalk" to activate
delay 0.4
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    if frontApp is not "{KAKAOTALK_PROCESS_NAME}" then
        return "ABORT:" & frontApp
    end if
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winEl to window "{escaped_window_name}"
        try
            set searchField to (first UI element of winEl whose role is "AXTextField")
            set p to position of searchField
            set s to size of searchField
            return "FIELD:" & ((item 1 of p) as string) & "," & ((item 2 of p) as string) & "," & ((item 1 of s) as string) & "," & ((item 2 of s) as string)
        end try
        set searchBtn to (first UI element of winEl whose description is "검색")
        set p to position of searchBtn
        set s to size of searchBtn
        return "BUTTON:" & (((item 1 of p) + (item 1 of s) / 2) as integer as string) & "," & (((item 2 of p) + (item 2 of s) / 2) as integer as string)
    end tell
end tell
"""
    output = run_applescript(check_script)
    if output.startswith("ABORT:"):
        raise FrontmostMismatchError(output.split(":", 1)[1])
    if output.startswith("FIELD:"):
        x, y, w, h = (int(v) for v in output[len("FIELD:") :].split(","))
        return x, y, w, h
    if not output.startswith("BUTTON:"):
        raise RuntimeError(f"검색창/검색 버튼을 찾을 수 없습니다: {output}")

    btn_x, btn_y = (int(v) for v in output[len("BUTTON:") :].split(","))
    ensure_kakaotalk_frontmost()
    run_cliclick(f"c:{btn_x},{btn_y}")
    time.sleep(SEARCH_OPEN_DELAY_SECONDS)

    field_script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winEl to window "{escaped_window_name}"
        set searchField to (first UI element of winEl whose role is "AXTextField")
        set p to position of searchField
        set s to size of searchField
        return ((item 1 of p) as string) & "," & ((item 2 of p) as string) & "," & ((item 1 of s) as string) & "," & ((item 2 of s) as string)
    end tell
end tell
"""
    field_output = run_applescript(field_script)
    x, y, w, h = (int(v) for v in field_output.split(","))
    return x, y, w, h


def focus_and_type_search_query(field_bounds: tuple[int, int, int, int], query: str) -> None:
    """검색창에 방 이름을 입력한다.

    당초 System Events의 `keystroke "a" using {command down}` + 문자열 keystroke로
    "전체 선택 후 새로 입력"을 시도했으나, 실제 환경 검증(Phase 1)에서 select-all이
    간헐적으로 실패해 이전 검색어에 새 검색어가 그대로 이어붙는(및 일부 한글 자모가
    깨지는) 문제가 재현됐다. 메시지 입력창과 동일하게 클립보드+cliclick 조합(Cmd+A,
    Cmd+V 모두 cliclick 키 이벤트)으로 대체해 신뢰성을 확보한다."""
    x, y, w, h = field_bounds
    center_x, center_y = x + w // 2, y + h // 2
    ensure_kakaotalk_frontmost()
    run_cliclick(f"c:{center_x},{center_y}")
    time.sleep(0.3)
    ensure_kakaotalk_frontmost()
    set_clipboard(query)
    run_cliclick("kd:cmd", "t:a", "ku:cmd")
    time.sleep(0.2)
    run_cliclick("kd:cmd", "t:v", "ku:cmd")
    time.sleep(SEARCH_TYPE_DELAY_SECONDS)


@dataclass
class RoomMatch:
    center_x: int
    center_y: int


def find_exact_room_match(room_name: str) -> RoomMatch | None:
    """검색 결과에서 이름이 정확히 일치하는 행을 찾는다. 0개/2개 이상이면 None을
    반환한다(SRS FR-6, 임의 선택 금지)."""
    escaped_name = _escape_applescript_string(room_name)
    script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winEl to window "{_escape_applescript_string(KAKAOTALK_MAIN_WINDOW_NAME)}"
        set topElems to UI elements of winEl
        set matchCount to 0
        set matchPos to "NONE"
        repeat with e in topElems
            if role of e is "AXScrollArea" then
                try
                    set tbl to (first UI element of e whose role is "AXTable")
                    set rowList to rows of tbl
                    repeat with r in rowList
                        set cellEl to item 1 of (UI elements of r)
                        set grandkids to UI elements of cellEl
                        repeat with g in grandkids
                            if role of g is "AXStaticText" then
                                try
                                    if (value of g as string) is "{escaped_name}" then
                                        set matchCount to matchCount + 1
                                        set p to position of r
                                        set s to size of r
                                        set matchPos to ((item 1 of p) + (item 1 of s) / 2) as integer as string
                                        set matchPos to matchPos & "," & (((item 2 of p) + (item 2 of s) / 2) as integer as string)
                                    end if
                                end try
                            end if
                        end repeat
                    end repeat
                end try
            end if
        end repeat
        return (matchCount as string) & ":" & matchPos
    end tell
end tell
"""
    output = run_applescript(script)
    count_str, _, pos_str = output.partition(":")
    match_count = int(count_str)
    if match_count != 1:
        logger.warning("방 '%s' 검색 결과 %d건 (정확히 1건이어야 함)", room_name, match_count)
        return None
    x_str, y_str = pos_str.split(",")
    return RoomMatch(center_x=int(x_str), center_y=int(y_str))


def open_room_and_verify(room_name: str, match: RoomMatch) -> bool:
    """cliclick 더블클릭으로 방을 연 뒤, 열린 창 이름이 정확히 일치하는지
    재확인한다 (SRS FR-7 — misfire로 다른 실제 대화방이 열린 사례가 있어 필수)."""
    ensure_kakaotalk_frontmost()
    run_cliclick(f"dc:{match.center_x},{match.center_y}")
    time.sleep(ROOM_OPEN_DELAY_SECONDS)
    script = f'tell application "System Events" to tell process "{KAKAOTALK_PROCESS_NAME}" to return name of every window'
    window_names = run_applescript(script)
    opened_names = [n.strip() for n in window_names.split(",")]
    if room_name not in opened_names:
        logger.error("방 열기 검증 실패: 예상=%s, 실제 열린 창=%s", room_name, opened_names)
        return False
    return True


def get_message_input_center(room_name: str) -> tuple[int, int]:
    escaped_name = _escape_applescript_string(room_name)
    script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winEl to window "{escaped_name}"
        set topElems to UI elements of winEl
        repeat with e in topElems
            if role of e is "AXScrollArea" then
                try
                    set ta to (first UI element of e whose role is "AXTextArea")
                    set p to position of ta
                    set s to size of ta
                    return (((item 1 of p) + (item 1 of s) / 2) as integer as string) & "," & (((item 2 of p) + (item 2 of s) / 2) as integer as string)
                end try
            end if
        end repeat
        return "NOT_FOUND"
    end tell
end tell
"""
    output = run_applescript(script)
    if output == "NOT_FOUND":
        raise RuntimeError(f"'{room_name}' 방의 메시지 입력창을 찾을 수 없습니다.")
    x_str, y_str = output.split(",")
    return int(x_str), int(y_str)


def get_send_button_center(room_name: str) -> tuple[int, int]:
    escaped_name = _escape_applescript_string(room_name)
    script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winEl to window "{escaped_name}"
        set sendBtn to (first UI element of winEl whose name is "전송")
        set p to position of sendBtn
        set s to size of sendBtn
        return (((item 1 of p) + (item 1 of s) / 2) as integer as string) & "," & (((item 2 of p) + (item 2 of s) / 2) as integer as string)
    end tell
end tell
"""
    output = run_applescript(script)
    x_str, y_str = output.split(",")
    return int(x_str), int(y_str)


def paste_message(input_center: tuple[int, int], message: str) -> None:
    """SRS FR-8/FR-9: 클립보드는 UTF-8로 설정하고, 붙여넣기는 cliclick의 키 조합으로
    수행한다 (System Events keystroke 기반 Cmd+V는 간헐적으로 무시됨이 확인됨)."""
    set_clipboard(message)
    x, y = input_center
    ensure_kakaotalk_frontmost()
    run_cliclick(f"c:{x},{y}")
    time.sleep(0.3)
    ensure_kakaotalk_frontmost()
    run_cliclick("kd:cmd", "t:v", "ku:cmd")
    time.sleep(PASTE_DELAY_SECONDS)


def verify_pasted_text(expected: str) -> bool:
    """SRS FR-10: 입력창의 접근성 value는 신뢰할 수 없어(빈 값 오보고), 전체선택+복사
    후 클립보드를 다시 읽어 확인한다."""
    ensure_kakaotalk_frontmost()
    script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        keystroke "a" using {{command down}}
        delay 0.2
        keystroke "c" using {{command down}}
        delay 0.3
    end tell
end tell
"""
    run_applescript(script)
    return read_clipboard() == expected


def click_send_button(send_center: tuple[int, int]) -> None:
    """SRS FR-11: Return 키/AXPress는 입력창을 비우기만 하고 실제 전송이 되지
    않는 오탐이 확인되어, cliclick으로 마우스를 이동한 뒤 클릭한다."""
    x, y = send_center
    ensure_kakaotalk_frontmost()
    run_cliclick(f"m:{x},{y}")
    time.sleep(0.2)
    run_cliclick(f"c:{x},{y}")


def message_appears_in_history(room_name: str, expected_text: str) -> bool:
    escaped_name = _escape_applescript_string(room_name)
    escaped_text = _escape_applescript_string(expected_text)
    script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winEl to window "{escaped_name}"
        set topElems to UI elements of winEl
        set found to "0"
        repeat with e in topElems
            if role of e is "AXScrollArea" then
                try
                    set tbl to (first UI element of e whose role is "AXTable")
                    set rowList to rows of tbl
                    repeat with r in rowList
                        set L1 to UI elements of r
                        repeat with a in L1
                            set L2 to UI elements of a
                            repeat with b in L2
                                if role of b is "AXTextArea" or role of b is "AXStaticText" then
                                    try
                                        if (value of b as string) is "{escaped_text}" then
                                            set found to "1"
                                        end if
                                    end try
                                end if
                            end repeat
                        end repeat
                    end repeat
                end try
            end if
        end repeat
        return found
    end tell
end tell
"""
    return run_applescript(script) == "1"


def verify_message_sent(room_name: str, expected_text: str) -> bool:
    """SRS FR-12: 전송 검증. 입력창이 비워졌는지는 신뢰할 수 없어(Phase 0에서 반복
    재현) 대화 내역에서 원문을 직접 확인한다."""
    deadline = time.monotonic() + SEND_VERIFY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if message_appears_in_history(room_name, expected_text):
            return True
        time.sleep(SEND_VERIFY_POLL_INTERVAL_SECONDS)
    return False


def close_all_room_windows() -> None:
    """메인 목록 창("카카오톡")을 제외한 모든 창을 닫는다.

    여러 방을 순차 처리할 때, 이전 방의 대화창(성공/실패/오동작으로 엉뚱하게 열린
    창 포함)이 열려 있는 채로 다음 방을 검색하면 화면 좌표 클릭이 메인 목록 창이
    아니라 열려 있는 대화창을 대신 맞힐 수 있다 — 실제로 여러 방을 연속 처리하는
    시나리오(ai_news_telegram 연동)에서 이전 방 대화창의 메시지 입력창에 다음 방
    검색어가 잘못 입력되는 사고가 재현됐다. 방 하나를 처리할 때마다(성공하든
    실패하든) 반드시 호출해 다음 방 처리 전에 메인 목록 창만 남긴다."""
    escaped_main = _escape_applescript_string(KAKAOTALK_MAIN_WINDOW_NAME)
    script = f"""
tell application "System Events"
    tell process "{KAKAOTALK_PROCESS_NAME}"
        set winList to every window
        repeat with w in winList
            if name of w is not "{escaped_main}" then
                try
                    click (button 1 of w whose subrole is "AXCloseButton")
                end try
            end if
        end repeat
    end tell
end tell
"""
    try:
        run_applescript(script)
    except Exception:
        logger.warning("방 창 정리 실패(다음 방 처리에 영향을 줄 수 있음)")


def send_message_to_room(room_name: str, message: str) -> RoomSendResult:
    try:
        field_bounds = activate_kakaotalk_and_open_search()
        focus_and_type_search_query(field_bounds, room_name)

        match = find_exact_room_match(room_name)
        if match is None:
            return RoomSendResult(room_name, False, "검색 결과가 0건이거나 2건 이상입니다.")

        if not open_room_and_verify(room_name, match):
            return RoomSendResult(room_name, False, "방을 열었으나 창 이름이 일치하지 않습니다(오동작 방지).")

        input_center = get_message_input_center(room_name)
        paste_message(input_center, message)

        if not verify_pasted_text(message):
            return RoomSendResult(room_name, False, "붙여넣기 확인 실패(클립보드 재확인 불일치).")

        send_center = get_send_button_center(room_name)
        click_send_button(send_center)

        if not verify_message_sent(room_name, message):
            return RoomSendResult(room_name, False, "전송 검증 실패(대화 내역에서 확인되지 않음).")

        return RoomSendResult(room_name, True)
    except FrontmostMismatchError as exc:
        return RoomSendResult(room_name, False, str(exc))
    except Exception as exc:  # noqa: BLE001 - 방 단위 실패로 변환해 다음 방을 계속 처리
        logger.exception("방 '%s' 처리 중 예외 발생", room_name)
        return RoomSendResult(room_name, False, f"예외: {exc}")
    finally:
        close_all_room_windows()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="카카오톡 지정 대화방에 메시지를 전송한다.")
    parser.add_argument("--room", action="append", required=True, help="대상 대화방 이름(반복 가능)")
    message_group = parser.add_mutually_exclusive_group(required=True)
    message_group.add_argument("--message", type=str, help="전송할 메시지 본문")
    message_group.add_argument("--message-file", type=Path, help="전송할 메시지 본문 파일 경로")
    return parser


def resolve_message(args: argparse.Namespace) -> str:
    if args.message is not None:
        return args.message
    try:
        return args.message_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArgumentError(f"--message-file을 읽을 수 없습니다: {args.message_file}") from exc


def run(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        message = resolve_message(args)
        ensure_platform_macos()
        ensure_cliclick_installed()
        ensure_clamshell_open()
    except (ArgumentError, CliclickNotFoundError, ClamshellClosedError) as exc:
        logger.error("사전 검증 실패: %s", exc)
        return 1

    try:
        ensure_kakaotalk_ready()
    except (
        KakaoTalkNotInstalledError,
        KakaoTalkNotLoggedInError,
        AccessibilityPermissionError,
    ) as exc:
        logger.error("카카오톡 앱 준비 실패: %s", exc)
        return 1

    results = [send_message_to_room(room, message) for room in args.room]

    for result in results:
        if result.success:
            logger.info("방 '%s' 전송 성공", result.room)
        else:
            logger.error("방 '%s' 전송 실패: %s", result.room, result.reason)

    failure_count = sum(1 for r in results if not r.success)
    logger.info("완료: 성공 %d건, 실패 %d건 (총 %d건)", len(results) - failure_count, failure_count, len(results))
    return 1 if failure_count > 0 else 0


if __name__ == "__main__":
    sys.exit(run())
