import configparser
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from pymcprotocol import Type3E


# ==================================================
# 기본 폴더 / 설정 파일
# ==================================================
# exe(PyInstaller)로 실행하면 exe 가 있는 폴더, 스크립트면 .py 가 있는 폴더 기준
# (작업 스케줄러/시작프로그램으로 실행하면 현재 폴더가 C:\Windows\System32 등이 될 수 있음)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.txt"


# ==================================================
# PLC 접속 / 로그 설정
# 실제 값은 시작할 때 config.txt 에서 읽어서 덮어씀 (apply_config)
# ==================================================
PLC_IP = "192.168.0.20"
PLC_PORT = 7777

POLL_SEC = 0.2          # 전체 주소 읽기 주기
RECONNECT_SEC = 5.0
TIMEOUT_SEC = 2.0

LOG_DIR = BASE_DIR / "log"
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
# PLC 루프와 DB 스레드가 동시에 로그를 써도 줄이 섞이지 않도록
_log_lock = threading.Lock()


def append_log(text):
    # 파일 기록 실패(다른 프로그램이 잠근 경우 등)로 PLC 루프가 끊기지 않도록 보호
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(text + "\n")
            f.flush()
    except Exception as e:
        print(f"[LOG ERR] 로그 파일 기록 실패: {e}", flush=True)


def write_plc_log(message):
    line = f"{now_text()} | {message}"

    with _log_lock:
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
                write_plc_log(f"[CLEANUP] 오래된 로그 삭제: {file_path.name}")
            except Exception as e:
                write_plc_log(f"[CLEANUP ERR] {file_path.name} 삭제 실패: {e}")


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

    print("[INFO] PLC 연결 시도 중...", flush=True)
    mc.connect(PLC_IP, PLC_PORT)

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

    with _log_lock:
        # 파일에 먼저 기록 (콘솔 출력이 멈춰도 로그는 남도록)
        append_log(text)

        print(text, flush=True)


# ==================================================
# ==================================================
#  MSSQL -> PLC 전송
#  - SERVER_ID 일치 + FLOW_TYPE + CURRENT_PORT_ID 조건의 CARRIER_ID -> R6500~R6503
#  - CURRENT_PORT_ID / CURRENT_VEHICLE_ID / TARGET_PORT_ID 중 하나라도 값이 있는
#    CARRIER_ID 목록 -> R6600~
#  DB 조회는 별도 스레드(DbWorker)에서 DB_POLL_SEC 마다 하고,
#  PLC 쓰기는 메인 루프(PLC 연결 1개)에서 함
# ==================================================
# ==================================================
class ConfigError(Exception):
    pass


def load_config():
    if not CONFIG_PATH.exists():
        raise ConfigError(f"설정 파일이 없습니다: {CONFIG_PATH}")

    # interpolation=None : 비밀번호에 '%' 가 있어도 그대로 읽음
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str.upper

    # utf-8-sig : 메모장에서 저장한 BOM 포함 파일도 읽음
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        parser.read_file(f)

    def get(section, key, default=None):
        try:
            value = parser.get(section, key).strip()
        except (configparser.NoSectionError, configparser.NoOptionError):
            value = ""

        if value == "":
            if default is None:
                raise ConfigError(f"설정 누락: [{section}] {key}")
            return default

        return value

    def get_bool(section, key, default):
        value = get(section, key, "yes" if default else "no").lower()
        return value in ("yes", "true", "1", "on")

    try:
        cfg = {
            "db_enable": get_bool("MSSQL", "ENABLE", True),
            "db_server": make_db_server(
                get("MSSQL", "DB_IP", get("MSSQL", "SERVER", "")),
                get("MSSQL", "DB_PORT", ""),
            ),
            "db_name": get("MSSQL", "DATABASE", ""),
            "db_table": get("MSSQL", "TABLE", ""),
            "db_driver": get("MSSQL", "DRIVER", "SQL Server"),
            "db_trusted": get_bool("MSSQL", "TRUSTED_CONNECTION", False),
            "db_user": get("MSSQL", "USER", ""),
            "db_password": get("MSSQL", "PASSWORD", ""),
            "db_trust_cert": get_bool("MSSQL", "TRUST_SERVER_CERTIFICATE", True),
            "db_timeout": int(float(get("MSSQL", "TIMEOUT_SEC", "5"))),

            "server_id": get("FILTER", "SERVER_ID", ""),
            "flow_type": get("FILTER", "FLOW_TYPE", "2"),
            "port_id": get("FILTER", "PORT_ID", "P1"),

            "plc_ip": get("PLC", "IP"),
            "plc_port": int(get("PLC", "PORT")),
            "plc_timeout": float(get("PLC", "TIMEOUT_SEC", "2")),
            "port_carrier_device": get("PLC", "PORT_CARRIER_DEVICE", "R6500").upper(),
            "carrier_list_device": get("PLC", "CARRIER_LIST_DEVICE", "R6600").upper(),
            "carrier_count_device": get("PLC", "CARRIER_COUNT_DEVICE", "R6599").upper(),
            "carrier_list_max": int(get("PLC", "CARRIER_LIST_MAX", "20")),
            "carrier_words": int(get("PLC", "CARRIER_WORDS", "4")),
            "plc_refresh_sec": float(get("PLC", "PLC_REFRESH_SEC", "60")),

            "plc_poll_sec": float(get("RUN", "PLC_POLL_SEC", "0.2")),
            "db_poll_sec": float(get("RUN", "DB_POLL_SEC", get("RUN", "POLL_SEC", "5.0"))),
            "reconnect_sec": float(get("RUN", "RECONNECT_SEC", "5.0")),
            "log_dir": get("RUN", "LOG_DIR", ""),
            "max_log_files": int(get("RUN", "MAX_LOG_FILES", "30")),
        }
    except ValueError as e:
        raise ConfigError(f"설정 값 형식 오류: {e}")

    if not cfg["db_enable"]:
        return cfg

    if not cfg["db_server"]:
        raise ConfigError("설정 누락: [MSSQL] DB_IP")

    for key, name in (("db_name", "DATABASE"), ("db_table", "TABLE")):
        if not cfg[key]:
            raise ConfigError(f"설정 누락: [MSSQL] {name}")

    if not cfg["server_id"]:
        raise ConfigError("설정 누락: [FILTER] SERVER_ID")

    if not cfg["db_trusted"] and not cfg["db_user"]:
        raise ConfigError("SQL 인증(TRUSTED_CONNECTION = no) 인데 USER 가 비어 있습니다")

    # 테이블명은 SQL 파라미터로 넘길 수 없어서 문자열로 넣으므로 형식 검사
    # 허용: 영문/숫자/_ 로 된 이름, 점(.)으로 스키마 구분 (예: dbo.CARRIER_INFO)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}",
                        cfg["db_table"]):
        raise ConfigError(f"TABLE 이름 형식 오류: {cfg['db_table']}")

    for key in ("port_carrier_device", "carrier_list_device", "carrier_count_device"):
        if not re.fullmatch(r"[A-Z]+[0-9]+", cfg[key]):
            raise ConfigError(f"PLC 주소 형식 오류: {cfg[key]}")

    # 전송 주소끼리 겹치면 서로 덮어쓰므로 시작 전에 확인
    areas = [
        ("PORT_CARRIER_DEVICE", cfg["port_carrier_device"], cfg["carrier_words"]),
        ("CARRIER_LIST_DEVICE", cfg["carrier_list_device"],
         cfg["carrier_words"] * cfg["carrier_list_max"]),
        ("CARRIER_COUNT_DEVICE", cfg["carrier_count_device"], 1),
    ]
    for i in range(len(areas)):
        for j in range(i + 1, len(areas)):
            name1, head1, size1 = areas[i]
            name2, head2, size2 = areas[j]
            dev1, num1 = split_device(head1)
            dev2, num2 = split_device(head2)
            if dev1 == dev2 and num1 < num2 + size2 and num2 < num1 + size1:
                raise ConfigError(
                    f"PLC 주소 겹침: {name1}={device_range_text(head1, size1)} / "
                    f"{name2}={device_range_text(head2, size2)}"
                )

    return cfg


def make_db_server(ip, port):
    """DB_IP + DB_PORT -> ODBC SERVER 값 (예: 192.168.0.10,1433)"""
    ip = ip.strip()
    port = port.strip()

    # 예전 형식(SERVER = IP,포트) 또는 DB_IP 에 포트까지 쓴 경우 그대로 사용
    if not ip or not port or "," in ip:
        return ip

    if not port.isdigit():
        raise ConfigError(f"DB_PORT 는 숫자여야 합니다: {port}")

    return f"{ip},{port}"


def quote_table(table_name):
    # dbo.CARRIER_INFO -> [dbo].[CARRIER_INFO]
    return ".".join(f"[{part}]" for part in table_name.split("."))



# ==================================================
# MSSQL 연결 / 조회
# ==================================================
def connect_db(cfg):
    # MSSQL 을 사용할 때만 필요하므로 여기서 import
    import pyodbc

    parts = [
        f"DRIVER={{{cfg['db_driver']}}}",
        f"SERVER={cfg['db_server']}",
        f"DATABASE={cfg['db_name']}",
    ]

    if cfg["db_trusted"]:
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={cfg['db_user']}")
        # 비밀번호에 ; 나 } 가 있어도 되도록 {} 로 감싸고 } 는 }} 로 이스케이프
        parts.append("PWD={" + cfg["db_password"].replace("}", "}}") + "}")

    if cfg["db_trust_cert"]:
        parts.append("TrustServerCertificate=yes")

    conn = pyodbc.connect(
        ";".join(parts),
        timeout=cfg["db_timeout"],
        autocommit=True,
    )
    # 쿼리 응답 대기 시간
    conn.timeout = cfg["db_timeout"]

    return conn


def close_db(conn):
    if conn is None:
        return

    try:
        conn.close()
    except Exception:
        pass


FLOW_TYPE_NAMES = {
    "1": "IN(정물류)",
    "2": "OUT(역물류)",
}


def flow_type_text(value):
    name = FLOW_TYPE_NAMES.get(str(value).strip())
    return f"{value}({name.split('(')[0]})" if name else str(value)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def read_rows(conn, cfg):
    """SERVER_ID 가 일치하는 모든 행을 한 번에 읽음 (주기당 DB 조회 1회)"""
    sql = (
        "SELECT CARRIER_ID, FLOW_TYPE, CURRENT_PORT_ID, "
        "CURRENT_VEHICLE_ID, TARGET_PORT_ID "
        f"FROM {quote_table(cfg['db_table'])} "
        "WHERE SERVER_ID = ? "
        "ORDER BY CARRIER_ID"
    )

    cursor = conn.cursor()
    try:
        cursor.execute(sql, cfg["server_id"])
        rows = cursor.fetchall()
    finally:
        cursor.close()

    result = []
    for row in rows:
        result.append({
            "CARRIER_ID": clean_text(row[0]),
            "FLOW_TYPE": clean_text(row[1]),
            "CURRENT_PORT_ID": clean_text(row[2]),
            "CURRENT_VEHICLE_ID": clean_text(row[3]),
            "TARGET_PORT_ID": clean_text(row[4]),
        })

    return result


def same_flow_type(db_value, cfg_value):
    """FLOW_TYPE 비교 (DB 컬럼이 숫자/문자 어느 형식이어도 2 == '2' == 2.0)"""
    if db_value == "":
        return False

    try:
        return float(db_value) == float(cfg_value)
    except ValueError:
        return db_value == cfg_value


def find_port_carrier(rows, cfg):
    """FLOW_TYPE = 설정값 이고 CURRENT_PORT_ID = 설정값 인 CARRIER_ID 1개
    (여러 개면 CARRIER_ID 순으로 첫 번째)"""
    for row in rows:
        if (
            row["CARRIER_ID"]
            and same_flow_type(row["FLOW_TYPE"], cfg["flow_type"])
            and row["CURRENT_PORT_ID"] == cfg["port_id"]
        ):
            return row["CARRIER_ID"]

    return ""


def find_carrier_list(rows):
    """CURRENT_PORT_ID / CURRENT_VEHICLE_ID / TARGET_PORT_ID 중 하나라도 값이 있는
    CARRIER_ID 목록 (NULL, 빈 문자열, 공백은 '값 없음')"""
    result = []

    for row in rows:
        carrier_id = row["CARRIER_ID"]

        if not carrier_id or carrier_id in result:
            continue

        if (
            row["CURRENT_PORT_ID"]
            or row["CURRENT_VEHICLE_ID"]
            or row["TARGET_PORT_ID"]
        ):
            result.append(carrier_id)

    return result



# ==================================================
# 문자열 -> PLC WORD
# ==================================================
def string_to_words(text, word_length):
    """문자열 -> WORD 리스트 (1워드 = 하위바이트 1번째 문자, 상위바이트 2번째 문자)
    AutoCommand.py 의 words_to_string 과 같은 순서"""
    data = text.encode("ascii", errors="replace")[:word_length * 2]
    data = data.ljust(word_length * 2, b"\x00")

    return [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]


def device_range_text(head_device, word_count):
    dev, num = split_device(head_device)
    return f"{dev}{num}~{dev}{num + word_count - 1}"


def write_words(mc, head_device, values):
    mc.batchwrite_wordunits(headdevice=head_device, values=values)



# ==================================================
# DB 조회 스레드
# DB 응답이 느려도 PLC 로그(0.2초 주기)가 멈추지 않도록 분리
# ==================================================
class DbWorker(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        self.rows = None        # 마지막으로 읽은 행
        self.seq = 0            # 새로 읽을 때마다 +1
        self.taken_seq = 0      # 메인 루프가 가져간 seq

    def take_new(self, force=False):
        """새로 읽은 결과가 있으면 반환 (force=True 면 마지막 결과라도 반환)"""
        with self.lock:
            if self.rows is None:
                return None
            if not force and self.seq == self.taken_seq:
                return None
            self.taken_seq = self.seq
            return self.rows

    def stop(self):
        self.stop_event.set()

    def run(self):
        cfg = self.cfg
        conn = None
        down_at = None
        last_err = None

        while not self.stop_event.is_set():
            try:
                new_conn = conn is None
                if new_conn:
                    conn = connect_db(cfg)

                rows = read_rows(conn, cfg)

                # 접속 + 조회까지 성공해야 연결 성공으로 기록
                if new_conn:
                    if down_at is None:
                        write_plc_log(
                            f"[DB CONNECT] 연결 성공 | {cfg['db_server']}/{cfg['db_name']}"
                        )
                    else:
                        write_plc_log(
                            f"[DB RECONNECT] 재접속 성공 | "
                            f"끊김 {time.monotonic() - down_at:.1f}초"
                        )
                    down_at = None
                    last_err = None

                with self.lock:
                    self.rows = rows
                    self.seq += 1

                wait = cfg["db_poll_sec"]

            except Exception as e:
                close_db(conn)
                conn = None

                if down_at is None:
                    down_at = time.monotonic()

                # 같은 오류가 반복되면 로그는 1번만
                err_text = str(e)
                if err_text != last_err:
                    write_plc_log(f"[DB ERR] {err_text}")
                    last_err = err_text

                wait = cfg["reconnect_sec"]

            self.stop_event.wait(wait)

        close_db(conn)


# ==================================================
# DB 결과 -> PLC 전송 + 로그
# ==================================================
def process_db_rows(mc, rows, cfg, state):
    words = cfg["carrier_words"]
    max_chars = words * 2

    # ------------------------------
    # 1) R6500 : 포트 캐리어 1개
    # ------------------------------
    # PLC 쓰기는 값이 바뀌었을 때 + PLC_REFRESH_SEC 마다 1번 (PLC 쪽 값이 지워진 경우 대비)
    now = time.monotonic()
    last_write = state.get("last_write_time")
    refresh = last_write is None or now - last_write >= cfg["plc_refresh_sec"]

    port_carrier = find_port_carrier(rows, cfg)

    if refresh or port_carrier != state.get("port_carrier"):
        write_words(mc, cfg["port_carrier_device"],
                    string_to_words(port_carrier, words))

    if port_carrier != state.get("port_carrier"):
        dev_text = device_range_text(cfg["port_carrier_device"], words)

        write_plc_log(
            f"[PORT CARRIER] FLOW_TYPE={flow_type_text(cfg['flow_type'])} | "
            f"CURRENT_PORT_ID={cfg['port_id']} | "
            f"{state.get('port_carrier') or '-'} -> {port_carrier or '-'} | "
            f"{dev_text} 전송"
        )

        if len(port_carrier) > max_chars:
            write_plc_log(
                f"[WARN] CARRIER_ID 길이 초과 ({len(port_carrier)}자 > {max_chars}자), "
                f"앞 {max_chars}자만 전송: {port_carrier}"
            )

        state["port_carrier"] = port_carrier

    # ------------------------------
    # 2) R6600~ : 캐리어 목록
    # ------------------------------
    carrier_list = find_carrier_list(rows)
    max_count = cfg["carrier_list_max"]
    send_list = carrier_list[:max_count]

    # 조건에 맞는 CARRIER_ID 총 수 (목록 최대 개수를 넘어도 전체 개수)
    total_count = len(carrier_list)

    if refresh or carrier_list != state.get("carrier_list"):
        values = []
        for i in range(max_count):
            carrier_id = send_list[i] if i < len(send_list) else ""
            values.extend(string_to_words(carrier_id, words))

        write_words(mc, cfg["carrier_list_device"], values)

        # 3) 총 수 -> R6599 (1워드)
        write_words(mc, cfg["carrier_count_device"], [min(total_count, 0xFFFF)])

    if carrier_list != state.get("carrier_list"):
        dev, base = split_device(cfg["carrier_list_device"])
        dev_text = device_range_text(cfg["carrier_list_device"], words * max_count)

        lines = []
        for i, carrier_id in enumerate(send_list):
            addr = f"{dev}{base + i * words}"
            lines.append(f"#{i + 1:02d} | {addr} | {carrier_id}")

        if not send_list:
            lines.append("(없음)")

        if len(carrier_list) > max_count:
            lines.append(
                f"[WARN] 목록 {len(carrier_list)}개 중 {max_count}개만 전송 "
                f"(누락: {', '.join(carrier_list[max_count:])})"
            )

        too_long = [c for c in send_list if len(c) > max_chars]
        if too_long:
            lines.append(
                f"[WARN] {max_chars}자 초과 CARRIER_ID 는 앞 {max_chars}자만 전송: "
                f"{', '.join(too_long)}"
            )

        write_carrier_block(
            f"[CARRIER LIST] SERVER_ID={cfg['server_id']} | "
            f"총 {total_count}개 -> {cfg['carrier_count_device']} | "
            f"{dev_text} 전송",
            lines
        )

        state["carrier_list"] = carrier_list

    if refresh:
        state["last_write_time"] = now



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
# 중복 실행 방지 (Windows)
# 백그라운드 실행 중에 exe 를 한 번 더 실행해도 로그가 중복 기록되지 않도록 함
# ==================================================
_instance_mutex = None


def is_already_running():
    global _instance_mutex

    if os.name != "nt":
        return False

    try:
        import ctypes

        ERROR_ACCESS_DENIED = 5      # 다른 계정(예: SYSTEM)으로 이미 실행 중
        ERROR_ALREADY_EXISTS = 183

        kernel32 = ctypes.windll.kernel32
        _instance_mutex = kernel32.CreateMutexW(
            None, False, "Global\\AirlockPlcLogger"
        )

        return kernel32.GetLastError() in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED)
    except Exception:
        return False


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
    print(f"CONFIG        : {CONFIG_PATH}")
    print("==========================================")


# ==================================================
# config.txt 값을 전역 설정에 적용
# ==================================================
def apply_config(cfg):
    global PLC_IP, PLC_PORT, POLL_SEC, RECONNECT_SEC, TIMEOUT_SEC
    global LOG_DIR, MAX_LOG_FILES

    PLC_IP = cfg["plc_ip"]
    PLC_PORT = cfg["plc_port"]
    TIMEOUT_SEC = cfg["plc_timeout"]
    POLL_SEC = cfg["plc_poll_sec"]
    RECONNECT_SEC = cfg["reconnect_sec"]

    if cfg["log_dir"]:
        LOG_DIR = Path(cfg["log_dir"])
    MAX_LOG_FILES = cfg["max_log_files"]


# ==================================================
# 메인 함수
# ==================================================
def main():
    if is_already_running():
        print("[INFO] 이미 실행 중입니다. 종료합니다.")
        return

    disable_quick_edit()

    try:
        cfg = load_config()
    except Exception as e:
        # 설정 오류는 exe 폴더\log 에 남기고 종료
        write_plc_log(f"[CONFIG ERR] {e}")
        return

    apply_config(cfg)

    print_start_info()

    write_plc_log(f"[START] 프로그램 시작 | PID={os.getpid()} | CONFIG={CONFIG_PATH}")

    # MSSQL -> PLC 전송 (DB 조회 스레드)
    db_worker = None
    db_state = {}

    if cfg["db_enable"]:
        write_plc_log(
            f"[CONFIG] DB={cfg['db_server']}/{cfg['db_name']} | "
            f"TABLE={cfg['db_table']} | SERVER_ID={cfg['server_id']} | "
            f"DB_POLL={cfg['db_poll_sec']}초"
        )
        db_worker = DbWorker(cfg)
        db_worker.start()
    else:
        write_plc_log("[CONFIG] MSSQL 전송 사용 안 함 (ENABLE = no)")

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

    # 연결 끊김 시각 / 재접속 시도 횟수 (재접속 로그용)
    disconnected_at = None
    retry_count = 0

    while True:
        mc = None

        try:
            mc = connect_plc()

            if disconnected_at is None:
                write_plc_log(f"[CONNECT] PLC 연결 성공 | {PLC_IP}:{PLC_PORT}")
            else:
                down_sec = time.monotonic() - disconnected_at
                write_plc_log(
                    f"[RECONNECT] PLC 재접속 성공 | {PLC_IP}:{PLC_PORT} | "
                    f"재시도 {retry_count}회 | 끊김 {down_sec:.1f}초"
                )

            disconnected_at = None
            retry_count = 0

            # PLC 재접속 후에는 DB 마지막 결과를 바로 다시 씀
            db_state.clear()
            db_force = True

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

                # MSSQL 조회 결과가 새로 들어왔으면 PLC 로 전송
                if db_worker is not None:
                    rows = db_worker.take_new(force=db_force)
                    if rows is not None:
                        process_db_rows(mc, rows, cfg, db_state)
                        db_force = False

                time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            print()
            write_plc_log("[STOP] 사용자 종료")
            close_plc(mc)
            break

        except Exception as e:
            if disconnected_at is None:
                # 처음 끊긴 시점: 연결 끊김 기록
                disconnected_at = time.monotonic()
                write_plc_log(f"[DISCONNECT] PLC 통신 오류 | {e}")
            else:
                # 재접속 실패: 매번 기록
                write_plc_log(
                    f"[RETRY FAIL] PLC 재접속 실패 {retry_count}회 | {e}"
                )

            retry_count += 1
            print(f"[INFO] {RECONNECT_SEC}초 후 재접속 시도 ({retry_count}회)", flush=True)

            close_plc(mc)

            try:
                time.sleep(RECONNECT_SEC)
            except KeyboardInterrupt:
                print()
                write_plc_log("[STOP] 사용자 종료")
                break

    if db_worker is not None:
        db_worker.stop()


if __name__ == "__main__":
    main()