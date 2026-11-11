import os
import time
from datetime import datetime
from pathlib import Path

from pymcprotocol import Type3E


# ==================================================
# PLC 접속 설정
# ==================================================
PLC_IP = "192.168.0.20"
PLC_PORT = 7777

POLL_SEC = 0.2          # 전체 주소 읽기 주기
RECONNECT_SEC = 5.0
TIMEOUT_SEC = 2.0


# ==================================================
# 로그 설정
# ==================================================
# LOG_DIR = Path(r"C:\ca\AirLock\log")

LOG_DIR = Path.cwd() / "log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_LOG_FILES = 30


# ==================================================
# 읽을 PLC 주소 범위
# ==================================================
# 전체 읽기 범위: POLL_SEC 주기로 항상 읽음
READ_RANGES = [
    # ("L29", 1),

    # ("D1000", 350),   # D1000 ~ D1349
    # ("D2000", 350),   # D2000 ~ D2349
    # ("D100", 100),    # D100 ~ D199
    ("D1201", 2),

    # ("D2001", 4),
    ("D2101", 4),

    ("D2300", 8),
    # ("M0", 64),       # M0 ~ M63
    ("M1999", 1),     # M1999
    ("M4000", 2),
    ("M6000", 10),   
    ("R25000", 25),      # R
    ("X0", 30),       # X0 ~ X9
]


# ==================================================
# 값 변화 로그를 남길 주소
# ==================================================
WATCH_COMMENTS = {
    # "D1100": "CarrierMoveToAirlockCompleteEvent",
    # "D1101": "CarrierMoveToInnerCompleteEvent",

    "D1201": "CarrierInstallReply",
    "D1202": "CarrierRemoveReply",

    "X10": "CarrierUpDetectSensor1",
    "X11": "CarrierDownDetectSensor2",
    "X16": "ShutterOpenSensor",
    "X17": "ShutterCloseSensor",

    "D2101": "CarrierInstallEvent",
    "D2102": "CarrierRemoveEvent",

    "D2200": "CarrierMoveToAirlockCompleteReply",
    "D2201": "CarrierMoveToInnerCompleteReply",

    "L29": "L29",

    "M1999": "ALARM",
    "M4000": "INSTALL EVENT OVERTIME",
    "M4001": "REMOVE EVENT OVERTIME",
}


# ==================================================
# 값 코멘트
# ==================================================
VALUE_COMMENTS = {
    "M1999": {
        0: "OFF",
        1: "ON",
    },
}


# ==================================================
# 문자열 장치
# 4워드 = 최대 8문자
# ==================================================
STRING_DEVICES = {
    "D2300": ("InstallCarrierID", 4),
    "D2304": ("RemoveCarrierID", 4),
    # "D120": ("WaitCarrierData", 4),
}


# ==================================================
# 입고된 캐리어 리스트 (주기 로그)
# 변화와 상관없이 CARRIER_LIST_INTERVAL_SEC 마다 한 번 기록
# ==================================================
CARRIER_LIST_DEVICES = {
    "R25000": ("InputCarrierId#1", 4),
    "R25004": ("InputCarrierId#2", 4),
    "R25008": ("InputCarrierId#3", 4),
    "R25012": ("InputCarrierId#4", 4),
    "R25016": ("InputCarrierId#5", 4),
}

CARRIER_LIST_INTERVAL_SEC = 60


# ==================================================
# RFID 인식 결과 (M6000 = 1 일 때만 읽음)
# READ_RANGES 와 분리해서 M6000 이 0 -> 1 이 될 때 1회만 읽고 기록
# ==================================================
TAG_ENABLE_DEVICE = "M6000"

TAG_READ_RANGES = [
    ("R6200", 18),    # R6200 ~ R6217
]

# 태그별 결과 주소
# (이름, ID 시작주소, ID 워드수, 인식횟수(Count) 주소, 신호세기(RSSI) 주소)
RFID_TAG_DEVICES = [
    ("TAG1", "R6200", 4, "R6212", "R6215"),
    ("TAG2", "R6204", 4, "R6213", "R6216"),
    ("TAG3", "R6208", 4, "R6214", "R6217"),
]

# RSSI 원시값 x 0.1 = dBm
RSSI_SCALE = 0.1

WORD_DEVS = {"R", "D", "W"}
BIT_DEVS = {"M", "X", "Y", "B", "L"}


# ==================================================
# 시간 문자열
# ==================================================
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# ==================================================
# 로그 파일 경로 (하루 1개 파일에 모든 로그 기록)
# ==================================================
def get_log_path():
    day = datetime.now().strftime("%Y%m%d")
    return LOG_DIR / f"plc_log_{day}.txt"


# ==================================================
# 로그 쓰기
# ==================================================
def append_log(text):
    # 파일 기록 실패(다른 프로그램이 잠근 경우 등)로 PLC 루프가 끊기지 않도록 보호
    try:
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(text + "\n")
            f.flush()
    except Exception as e:
        print(f"[LOG ERR] 로그 파일 기록 실패: {e}", flush=True)


def write_plc_log(message):
    line = f"{now_text()} | {message}"

    # 파일에 먼저 기록 (콘솔 출력이 멈춰도 로그는 남도록)
    append_log(line)

    print(line, flush=True)


# ==================================================
# 오래된 로그 삭제
# ==================================================
def cleanup_old_logs():
    patterns = [
        "plc_log_*.txt",
        "carrier_event_*.txt",   # 예전 버전에서 남은 파일 정리용
    ]

    for pattern in patterns:
        files = sorted(
            LOG_DIR.glob(pattern),
            key=lambda p: p.stat().st_mtime
        )

        if len(files) <= MAX_LOG_FILES:
            continue

        delete_count = len(files) - MAX_LOG_FILES
        old_files = files[:delete_count]

        for file_path in old_files:
            try:
                file_path.unlink()
                print(f"[CLEANUP] 오래된 로그 삭제: {file_path}")
            except Exception as e:
                print(f"[CLEANUP ERR] {file_path} 삭제 실패: {e}")


# ==================================================
# PLC 주소 분리
# 예: D2300 -> D, 2300
# ==================================================
def split_device(device_name):
    dev = ""
    num_text = ""

    for ch in device_name:
        if ch.isalpha():
            dev += ch
        elif ch.isdigit():
            num_text += ch

    dev = dev.upper()
    num = int(num_text or "0")

    return dev, num


# ==================================================
# WORD 값을 문자열로 변환
# ==================================================
def words_to_string(words):
    chars = []

    for word in words:
        low = word & 0xFF
        high = (word >> 8) & 0xFF

        if low != 0:
            chars.append(chr(low))

        if high != 0:
            chars.append(chr(high))

    return "".join(chars).strip()


# ==================================================
# data 딕셔너리에서 문자열 만들기
# 예: D2300, 4워드 -> D2300~D2303 문자열 변환
# ==================================================
def decode_string_from_data(data, start_device, word_length):
    dev, base_num = split_device(start_device)

    words = []

    for i in range(word_length):
        device_name = f"{dev}{base_num + i}"
        word = data.get(device_name, 0)
        words.append(word)

    return words_to_string(words)


# ==================================================
# PLC 연결
# ==================================================
def connect_plc():
    mc = Type3E()
    mc.setaccessopt(commtype="binary")

    try:
        mc.settimeout(TIMEOUT_SEC)
    except Exception:
        pass

    print("[INFO] PLC 연결 시도 중...")
    mc.connect(PLC_IP, PLC_PORT)
    print(f"[OK] PLC 연결 성공: {PLC_IP}:{PLC_PORT}")

    return mc


# ==================================================
# PLC 연결 종료
# ==================================================
def close_plc(mc):
    if mc is None:
        return

    try:
        mc.close()
    except Exception:
        pass


# ==================================================
# PLC 한 범위 읽기
# 예: D1000, 350 -> D1000~D1349
# ==================================================
def read_range_to_dict(mc, head_device, read_size):
    dev, base_num = split_device(head_device)

    if dev in WORD_DEVS:
        values = mc.batchread_wordunits(
            headdevice=head_device,
            readsize=read_size
        )

    elif dev in BIT_DEVS:
        values = mc.batchread_bitunits(
            headdevice=head_device,
            readsize=read_size
        )

    else:
        return {}

    result = {}

    for i, value in enumerate(values):
        device_name = f"{dev}{base_num + i}"
        result[device_name] = value

    return result


# ==================================================
# 등록된 전체 범위 읽기
# ==================================================
def read_all(mc, read_ranges):
    data = {}

    for head_device, read_size in read_ranges:
        one_range_data = read_range_to_dict(
            mc=mc,
            head_device=head_device,
            read_size=read_size
        )

        data.update(one_range_data)

    return data


# ==================================================
# 값 코멘트 가져오기
# ==================================================
def get_value_comment(device_name, value):
    mapping = VALUE_COMMENTS.get(device_name)

    if mapping is None:
        return ""

    return mapping.get(value, "")


# ==================================================
# 일반 값 변화 처리
# ==================================================
def process_normal_changes(data, prev_values):
    for device_name, comment in WATCH_COMMENTS.items():
        if device_name not in data:
            continue

        new_value = data[device_name]
        old_value = prev_values.get(device_name)

        # 최초 1회는 이전값 저장만 하고 로그는 남기지 않음
        if old_value is None:
            prev_values[device_name] = new_value
            continue

        # 값이 바뀐 경우만 로그 기록
        if old_value != new_value:
            value_comment = get_value_comment(device_name, new_value)

            if value_comment:
                message = (
                    f"{device_name} | {comment} | "
                    f"{value_comment} | {old_value} -> {new_value}"
                )
            else:
                message = (
                    f"{device_name} | {comment} | "
                    f"{old_value} -> {new_value}"
                )

            write_plc_log(message)

            prev_values[device_name] = new_value


# ==================================================
# 문자열 변화 처리
# ==================================================
def process_string_changes(data, prev_strings):
    for start_device, info in STRING_DEVICES.items():
        label, word_length = info

        new_text = decode_string_from_data(
            data=data,
            start_device=start_device,
            word_length=word_length
        )

        old_text = prev_strings.get(start_device)

        # 최초 1회는 이전값 저장만 하고 로그는 남기지 않음
        if old_text is None:
            prev_strings[start_device] = new_text
            continue

        # 문자열이 바뀐 경우만 로그 기록
        if old_text != new_text:
            message = f"{start_device} | {label} | {old_text} -> {new_text}"

            write_plc_log(message)

            prev_strings[start_device] = new_text


# ==================================================
# Carrier 입고/출고 완료 처리
# ==================================================
def process_carrier_events(data, event_state):
    install_carrier_id = decode_string_from_data(data, "D2300", 4)
    remove_carrier_id = decode_string_from_data(data, "D2304", 4)

    install_event = int(data.get("D2101", 0) or 0)
    install_reply = int(data.get("D1201", 0) or 0)

    remove_event = int(data.get("D2102", 0) or 0)
    remove_reply = int(data.get("D1202", 0) or 0)

    # ------------------------------
    # 입고 완료 조건
    # ------------------------------
    install_condition = (
        install_carrier_id != ""
        and install_event == 1
        and install_reply == 1
    )

    if install_condition and not event_state["install_logged"]:
        write_carrier_block(
            "입고 완료",
            [f"InstallCarrierID={install_carrier_id}"]
        )
        write_carrier_list_log(data)

        event_state["install_logged"] = True

    # D2101, D1201이 둘 다 0으로 떨어지면 다음 입고 로그 가능
    if install_event == 0 and install_reply == 0:
        event_state["install_logged"] = False

    # ------------------------------
    # 출고 완료 조건
    # ------------------------------
    remove_condition = (
        remove_carrier_id != ""
        and remove_event == 1
        and remove_reply == 1
    )

    if remove_condition and not event_state["remove_logged"]:
        write_carrier_block(
            "출고 완료",
            [f"RemoveCarrierID={remove_carrier_id}"]
        )
        write_carrier_list_log(data)

        event_state["remove_logged"] = True

    # D2102, D1202가 둘 다 0으로 떨어지면 다음 출고 로그 가능
    if remove_event == 0 and remove_reply == 0:
        event_state["remove_logged"] = False


# ==================================================
# 입고된 캐리어 리스트 주기 로그
# ==================================================
def process_carrier_list(data, list_state):
    now = time.monotonic()
    last = list_state.get("last_time")

    # 아직 주기가 안 됐으면 기록하지 않음 (최초 1회는 바로 기록)
    if last is not None and now - last < CARRIER_LIST_INTERVAL_SEC:
        return

    write_carrier_list_log(data)

    list_state["last_time"] = now


def write_carrier_list_log(data):
    items = []

    for start_device, info in CARRIER_LIST_DEVICES.items():
        label, word_length = info

        carrier_id = decode_string_from_data(
            data=data,
            start_device=start_device,
            word_length=word_length
        )

        items.append(f"{label}={carrier_id or '-'}")

    write_carrier_block("입고된 캐리어 리스트", items)


# ==================================================
# 태그 리스트 로그 (M6000 = 1 일 때)
# ==================================================
def process_tag_list(mc, data, tag_state):
    enable = int(data.get(TAG_ENABLE_DEVICE, 0) or 0)

    # M6000 이 0 으로 떨어지면 다음 기록 가능
    if enable != 1:
        tag_state["logged"] = False
        return

    # M6000 = 1 유지 중에는 중복 기록하지 않음
    if tag_state["logged"]:
        return

    tag_data = read_all(mc, TAG_READ_RANGES)

    lines = []
    read_count = 0

    for name, id_device, id_length, count_device, rssi_device in RFID_TAG_DEVICES:
        tag_id = decode_string_from_data(
            data=tag_data,
            start_device=id_device,
            word_length=id_length
        )

        # 태그 ID 가 비어 있으면 미인식
        if tag_id == "":
            lines.append(f"{name} | 미인식")
            continue

        read_count += 1

        count = int(tag_data.get(count_device, 0) or 0)
        # RSSI: 0.1 스케일, dBm 단위 (예: -523 -> -52.3dBm)
        rssi = to_signed16(tag_data.get(rssi_device, 0)) * RSSI_SCALE

        lines.append(
            f"{name} | ID={tag_id} | Count={count} | RSSI={rssi:.1f}dBm"
        )

    write_carrier_block(
        f"RFID 인식 결과 | 인식 {read_count}/{len(RFID_TAG_DEVICES)}",
        lines
    )

    tag_state["logged"] = True


# ==================================================
# 16비트 부호 있는 값 변환 (RSSI 음수 dBm 대응)
# 예: 65484 -> -52
# ==================================================
def to_signed16(value):
    value = int(value or 0) & 0xFFFF

    if value >= 0x8000:
        value -= 0x10000

    return value


# ==================================================
# 구분선으로 감싼 블록 형태의 Carrier 로그
# ==================================================
def write_carrier_block(title, body_lines):
    separator = "-" * 63

    lines = [
        separator,
        f"{now_text()} | {title} | ",
        *body_lines,
        separator,
    ]
    text = "\n".join(lines)

    # 파일에 먼저 기록 (콘솔 출력이 멈춰도 로그는 남도록)
    append_log(text)

    print(text, flush=True)


# ==================================================
# Windows CMD 빠른 편집 모드(QuickEdit) 끄기
# 콘솔 창을 마우스로 클릭/드래그하면 "선택" 상태가 되어
# print() 에서 프로그램이 멈추고, 엔터를 쳐야 다시 진행되는 현상 방지
# ==================================================
def disable_quick_edit():
    if os.name != "nt":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32

        STD_INPUT_HANDLE = -10
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080

        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()

        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return

        new_mode = (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


# ==================================================
# 시작 정보 출력
# ==================================================
def print_start_info():
    print("==========================================")
    print(" AIRLOCK PLC LOGGER START")
    print("==========================================")
    print(f"PLC IP        : {PLC_IP}")
    print(f"PLC PORT      : {PLC_PORT}")
    print(f"POLL SEC      : {POLL_SEC}")
    print(f"RECONNECT SEC : {RECONNECT_SEC}")
    print(f"LOG DIR       : {LOG_DIR}")
    print(f"MAX LOG FILES : {MAX_LOG_FILES}")
    print("==========================================")


# ==================================================
# 메인 함수
# ==================================================
def main():
    disable_quick_edit()

    print_start_info()

    # 일반 값 이전값 저장
    prev_values = {}

    # 문자열 이전값 저장
    prev_strings = {}

    # Carrier 이벤트 중복 방지 상태
    event_state = {
        "install_logged": False,
        "remove_logged": False,
    }

    # 입고된 캐리어 리스트 마지막 기록 시각
    carrier_list_state = {
        "last_time": None,
    }

    # 태그 리스트 중복 방지 상태
    tag_state = {
        "logged": False,
    }

    # 로그 삭제를 하루에 한 번만 실행하기 위한 변수
    last_cleanup_day = None

    while True:
        mc = None

        try:
            mc = connect_plc()

            while True:
                today = datetime.now().date()

                # 하루에 한 번 오래된 로그 정리
                if last_cleanup_day != today:
                    cleanup_old_logs()
                    last_cleanup_day = today

                # 전체 범위 읽기
                data = read_all(mc, READ_RANGES)

                # 일반 주소 변화 로그
                process_normal_changes(data, prev_values)

                # 문자열 변화 로그
                process_string_changes(data, prev_strings)

                # Carrier 입고/출고 완료 전용 로그
                process_carrier_events(data, event_state)

                # 입고된 캐리어 리스트 (1분 주기)
                process_carrier_list(data, carrier_list_state)

                # 태그 리스트 (M6000 = 1 일 때만 별도 읽기)
                process_tag_list(mc, data, tag_state)

                time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            print("\n[STOP] 사용자 종료")
            close_plc(mc)
            break

        except Exception as e:
            print(f"[ERR] PLC 통신 오류: {e}")
            print(f"[INFO] {RECONNECT_SEC}초 후 재접속 시도")

            close_plc(mc)
            time.sleep(RECONNECT_SEC)


if __name__ == "__main__":
    main()