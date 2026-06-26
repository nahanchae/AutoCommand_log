import time
from datetime import datetime
from pathlib import Path

from pymcprotocol import Type3E


# ==================================================
# PLC 접속 설정
# ==================================================
PLC_IP = "192.168.1.20"
PLC_PORT = 7777

POLL_SEC = 0.2
RECONNECT_SEC = 2.0
TIMEOUT_SEC = 2.0


# ==================================================
# 로그 설정
# ==================================================
LOG_DIR = Path(r"C:\ca\AirLock\log")
LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_LOG_FILES = 30


# ==================================================
# 읽을 PLC 주소 범위
# ==================================================
READ_RANGES = [
    ("D1000", 350),   # D1000 ~ D1349
    ("D2000", 350),   # D2000 ~ D2349
    ("D100", 100),    # D100 ~ D199

    ("M0", 64),       # M0 ~ M63
    ("M1999", 1),     # M1999

    ("R0", 200),      # R0 ~ R199
    ("X0", 10),       # X0 ~ X9
]


# ==================================================
# 값 변화 로그를 남길 주소
# ==================================================
WATCH_COMMENTS = {
    "D1100": "CarrierMoveToAirlockCompleteEvent",
    "D1101": "CarrierMoveToInnerCompleteEvent",

    "D1201": "CarrierInstallReply",
    "D1202": "CarrierRemoveReply",

    "D2001": "CarrierUpDetectSensor1",
    "D2002": "CarrierDownDetectSensor2",
    "D2003": "ShutterOpenSensor",
    "D2004": "ShutterCloseSensor",
    "D2005": "InstallCompSw",
    "D2006": "RemoveCompSw",
    "D2007": "SmokesensorTemperature",
    "D2008": "SmokesensorSetting_TemperWarningThreshold",
    "D2009": "SmokesensorSetting_TemperErrorThreshold",
    "D2010": "SmokesensorHumidity",

    "D2101": "CarrierInstallEvent",
    "D2102": "CarrierRemoveEvent",

    "D2200": "CarrierMoveToAirlockCompleteReply",
    "D2201": "CarrierMoveToInnerCompleteReply",

    "M0": "AutoMode",
    "M1999": "ALARM",
}


# ==================================================
# 값 코멘트
# ==================================================
VALUE_COMMENTS = {
    "M0": {
        0: "OFF",
        1: "ON",
    },
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
    "D120": ("WaitCarrierData", 4),
}


WORD_DEVS = {"R", "D", "W"}
BIT_DEVS = {"M", "X", "Y", "B"}


# ==================================================
# 시간 문자열
# ==================================================
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# ==================================================
# 로그 파일 경로
# ==================================================
def get_plc_log_path():
    day = datetime.now().strftime("%Y%m%d")
    return LOG_DIR / f"plc_log_{day}.txt"


def get_carrier_event_log_path():
    day = datetime.now().strftime("%Y%m%d")
    return LOG_DIR / f"carrier_event_{day}.txt"


# ==================================================
# 로그 쓰기
# ==================================================
def write_plc_log(message):
    line = f"{now_text()} | {message}"

    print(line)

    with open(get_plc_log_path(), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_carrier_event_log(message):
    line = f"{now_text()} | {message}"

    print("[CARRIER]", line)

    with open(get_carrier_event_log_path(), "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ==================================================
# 오래된 로그 삭제
# ==================================================
def cleanup_old_logs():
    patterns = [
        "plc_log_*.txt",
        "carrier_event_*.txt",
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
def read_all(mc):
    data = {}

    for head_device, read_size in READ_RANGES:
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
        write_carrier_event_log(
            f"InstallCarrierID : {install_carrier_id} 입고 완료"
        )

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
        write_carrier_event_log(
            f"RemoveCarrierID : {remove_carrier_id} 출고 완료"
        )

        event_state["remove_logged"] = True

    # D2102, D1202가 둘 다 0으로 떨어지면 다음 출고 로그 가능
    if remove_event == 0 and remove_reply == 0:
        event_state["remove_logged"] = False


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

                data = read_all(mc)

                # 일반 주소 변화 로그
                process_normal_changes(data, prev_values)

                # 문자열 변화 로그
                process_string_changes(data, prev_strings)

                # Carrier 입고/출고 완료 전용 로그
                process_carrier_events(data, event_state)

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