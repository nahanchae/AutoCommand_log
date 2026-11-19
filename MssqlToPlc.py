import configparser
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pyodbc
from pymcprotocol import Type3E


# ==================================================
# 기준 폴더
# exe(PyInstaller)로 실행하면 exe 가 있는 폴더, 스크립트면 .py 가 있는 폴더
# (작업 스케줄러/시작프로그램으로 실행하면 현재 폴더가 System32 등이 될 수 있음)
# ==================================================
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.txt"

# 설정 읽기 전 / 설정 오류 시 사용할 로그 폴더
LOG_DIR = BASE_DIR / "log"
MAX_LOG_FILES = 30


# ==================================================
# 시간 문자열
# ==================================================
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# ==================================================
# 로그 (하루 1개 파일)
# ==================================================
def get_log_path():
    day = datetime.now().strftime("%Y%m%d")
    return LOG_DIR / f"mssql_plc_log_{day}.txt"


def append_log(text):
    # 파일 기록 실패로 메인 루프가 끊기지 않도록 보호
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(text + "\n")
            f.flush()
    except Exception as e:
        print(f"[LOG ERR] 로그 파일 기록 실패: {e}", flush=True)


def write_log(message):
    line = f"{now_text()} | {message}"

    # 파일에 먼저 기록 (콘솔 출력이 멈춰도 로그는 남도록)
    append_log(line)

    print(line, flush=True)


def write_block(title, body_lines):
    separator = "-" * 63

    lines = [
        separator,
        f"{now_text()} | {title} | ",
        *body_lines,
        separator,
    ]
    text = "\n".join(lines)

    append_log(text)

    print(text, flush=True)


def cleanup_old_logs():
    files = sorted(
        LOG_DIR.glob("mssql_plc_log_*.txt"),
        key=lambda p: p.stat().st_mtime
    )

    if len(files) <= MAX_LOG_FILES:
        return

    for file_path in files[:len(files) - MAX_LOG_FILES]:
        try:
            file_path.unlink()
            write_log(f"[CLEANUP] 오래된 로그 삭제: {file_path.name}")
        except Exception as e:
            write_log(f"[CLEANUP ERR] {file_path.name} 삭제 실패: {e}")


# ==================================================
# 설정 파일 읽기
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
            "db_server": get("MSSQL", "SERVER"),
            "db_name": get("MSSQL", "DATABASE"),
            "db_table": get("MSSQL", "TABLE"),
            "db_driver": get("MSSQL", "DRIVER", "SQL Server"),
            "db_trusted": get_bool("MSSQL", "TRUSTED_CONNECTION", False),
            "db_user": get("MSSQL", "USER", ""),
            "db_password": get("MSSQL", "PASSWORD", ""),
            "db_trust_cert": get_bool("MSSQL", "TRUST_SERVER_CERTIFICATE", True),
            "db_timeout": int(float(get("MSSQL", "TIMEOUT_SEC", "5"))),

            "server_id": get("FILTER", "SERVER_ID"),
            "flow_type": get("FILTER", "FLOW_TYPE", "2"),
            "port_id": get("FILTER", "PORT_ID", "P1"),

            "plc_ip": get("PLC", "IP"),
            "plc_port": int(get("PLC", "PORT")),
            "plc_timeout": float(get("PLC", "TIMEOUT_SEC", "2")),
            "port_carrier_device": get("PLC", "PORT_CARRIER_DEVICE", "R6500").upper(),
            "carrier_list_device": get("PLC", "CARRIER_LIST_DEVICE", "R6600").upper(),
            "carrier_list_max": int(get("PLC", "CARRIER_LIST_MAX", "20")),
            "carrier_words": int(get("PLC", "CARRIER_WORDS", "4")),

            "poll_sec": float(get("RUN", "POLL_SEC", "1.0")),
            "reconnect_sec": float(get("RUN", "RECONNECT_SEC", "5.0")),
            "log_dir": get("RUN", "LOG_DIR", ""),
            "max_log_files": int(get("RUN", "MAX_LOG_FILES", "30")),
        }
    except ValueError as e:
        raise ConfigError(f"설정 값 형식 오류: {e}")

    if not cfg["db_trusted"] and not cfg["db_user"]:
        raise ConfigError("SQL 인증(TRUSTED_CONNECTION = no) 인데 USER 가 비어 있습니다")

    # 테이블명은 SQL 파라미터로 넘길 수 없어서 문자열로 넣으므로 형식 검사
    # 허용: 영문/숫자/_ 로 된 이름, 점(.)으로 스키마 구분 (예: dbo.CARRIER_INFO)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}",
                        cfg["db_table"]):
        raise ConfigError(f"TABLE 이름 형식 오류: {cfg['db_table']}")

    for key in ("port_carrier_device", "carrier_list_device"):
        if not re.fullmatch(r"[A-Z]+[0-9]+", cfg[key]):
            raise ConfigError(f"PLC 주소 형식 오류: {cfg[key]}")

    return cfg


def quote_table(table_name):
    # dbo.CARRIER_INFO -> [dbo].[CARRIER_INFO]
    return ".".join(f"[{part}]" for part in table_name.split("."))


# ==================================================
# MSSQL
# ==================================================
def connect_db(cfg):
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


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def read_port_carrier(conn, cfg):
    """SERVER_ID 일치 + FLOW_TYPE + CURRENT_PORT_ID 조건의 CARRIER_ID 1개"""
    sql = (
        f"SELECT TOP 1 CARRIER_ID FROM {quote_table(cfg['db_table'])} "
        "WHERE SERVER_ID = ? AND FLOW_TYPE = ? AND CURRENT_PORT_ID = ? "
        "AND NULLIF(LTRIM(RTRIM(CARRIER_ID)), '') IS NOT NULL "
        "ORDER BY CARRIER_ID"
    )

    cursor = conn.cursor()
    try:
        cursor.execute(sql, cfg["server_id"], cfg["flow_type"], cfg["port_id"])
        row = cursor.fetchone()
    finally:
        cursor.close()

    return clean_text(row[0]) if row else ""


def read_carrier_list(conn, cfg):
    """SERVER_ID 일치 + 포트/차량/목적지 중 하나라도 값이 있는 CARRIER_ID 목록"""
    # NULL 뿐 아니라 빈 문자열/공백도 '값 없음' 으로 취급
    sql = (
        f"SELECT CARRIER_ID FROM {quote_table(cfg['db_table'])} "
        "WHERE SERVER_ID = ? "
        "AND NULLIF(LTRIM(RTRIM(CARRIER_ID)), '') IS NOT NULL "
        "AND ("
        "   NULLIF(LTRIM(RTRIM(CURRENT_PORT_ID)), '') IS NOT NULL"
        "   OR NULLIF(LTRIM(RTRIM(CURRENT_VEHICLE_ID)), '') IS NOT NULL"
        "   OR NULLIF(LTRIM(RTRIM(TARGET_PORT_ID)), '') IS NOT NULL"
        ") "
        "ORDER BY CARRIER_ID"
    )

    cursor = conn.cursor()
    try:
        cursor.execute(sql, cfg["server_id"])
        rows = cursor.fetchall()
    finally:
        cursor.close()

    # 같은 CARRIER_ID 가 여러 행이면 1번만
    result = []
    for row in rows:
        carrier_id = clean_text(row[0])
        if carrier_id and carrier_id not in result:
            result.append(carrier_id)

    return result


# ==================================================
# PLC
# ==================================================
def connect_plc(cfg):
    mc = Type3E()
    mc.setaccessopt(commtype="binary")

    try:
        mc.settimeout(cfg["plc_timeout"])
    except Exception:
        pass

    mc.connect(cfg["plc_ip"], cfg["plc_port"])

    return mc


def close_plc(mc):
    if mc is None:
        return

    try:
        mc.close()
    except Exception:
        pass


def split_device(device_name):
    match = re.fullmatch(r"([A-Za-z]+)([0-9]+)", device_name)
    return match.group(1).upper(), int(match.group(2))


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
# 한 주기 처리
# ==================================================
def process_once(conn, mc, cfg, state):
    words = cfg["carrier_words"]
    max_chars = words * 2

    # ------------------------------
    # 1) R6500 : 포트 캐리어 1개
    # ------------------------------
    port_carrier = read_port_carrier(conn, cfg)

    write_words(mc, cfg["port_carrier_device"],
                string_to_words(port_carrier, words))

    if port_carrier != state.get("port_carrier"):
        dev_text = device_range_text(cfg["port_carrier_device"], words)

        write_log(
            f"[PORT CARRIER] FLOW_TYPE={cfg['flow_type']} | "
            f"CURRENT_PORT_ID={cfg['port_id']} | "
            f"{state.get('port_carrier') or '-'} -> {port_carrier or '-'} | "
            f"{dev_text} 전송"
        )

        if len(port_carrier) > max_chars:
            write_log(
                f"[WARN] CARRIER_ID 길이 초과 ({len(port_carrier)}자 > {max_chars}자), "
                f"앞 {max_chars}자만 전송: {port_carrier}"
            )

        state["port_carrier"] = port_carrier

    # ------------------------------
    # 2) R6600~ : 캐리어 목록
    # ------------------------------
    carrier_list = read_carrier_list(conn, cfg)
    max_count = cfg["carrier_list_max"]
    send_list = carrier_list[:max_count]

    values = []
    for i in range(max_count):
        carrier_id = send_list[i] if i < len(send_list) else ""
        values.extend(string_to_words(carrier_id, words))

    write_words(mc, cfg["carrier_list_device"], values)

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

        write_block(
            f"[CARRIER LIST] SERVER_ID={cfg['server_id']} | "
            f"{len(send_list)}개 | {dev_text} 전송",
            lines
        )

        state["carrier_list"] = carrier_list


# ==================================================
# Windows CMD 빠른 편집 모드(QuickEdit) 끄기
# 콘솔 창을 클릭하면 print() 에서 프로그램이 멈추는 현상 방지
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
            None, False, "Global\\MssqlToPlc"
        )

        return kernel32.GetLastError() in (ERROR_ALREADY_EXISTS, ERROR_ACCESS_DENIED)
    except Exception:
        return False


# ==================================================
# 메인
# ==================================================
def main():
    global LOG_DIR, MAX_LOG_FILES

    if is_already_running():
        print("[INFO] 이미 실행 중입니다. 종료합니다.")
        return

    disable_quick_edit()

    try:
        cfg = load_config()
    except Exception as e:
        # 설정 오류는 exe 폴더\log 에 남기고 종료
        write_log(f"[CONFIG ERR] {e}")
        return

    if cfg["log_dir"]:
        LOG_DIR = Path(cfg["log_dir"])
    MAX_LOG_FILES = cfg["max_log_files"]

    write_log(f"[START] 프로그램 시작 | PID={os.getpid()} | CONFIG={CONFIG_PATH}")
    write_log(
        f"[CONFIG] DB={cfg['db_server']}/{cfg['db_name']} | "
        f"TABLE={cfg['db_table']} | SERVER_ID={cfg['server_id']} | "
        f"PLC={cfg['plc_ip']}:{cfg['plc_port']} | LOG_DIR={LOG_DIR}"
    )

    state = {}
    conn = None
    mc = None

    # 끊김 상태 (재접속 로그용)
    db_down_at = None
    plc_down_at = None
    last_db_err = None
    last_plc_err = None

    last_cleanup_day = None

    while True:
        try:
            today = datetime.now().date()
            if last_cleanup_day != today:
                cleanup_old_logs()
                last_cleanup_day = today

            # ------------------------------
            # DB 연결
            # ------------------------------
            if conn is None:
                try:
                    conn = connect_db(cfg)
                except Exception as e:
                    raise DbError(e)

                if db_down_at is None:
                    write_log(f"[DB CONNECT] 연결 성공 | {cfg['db_server']}/{cfg['db_name']}")
                else:
                    write_log(
                        f"[DB RECONNECT] 재접속 성공 | "
                        f"끊김 {time.monotonic() - db_down_at:.1f}초"
                    )
                db_down_at = None
                last_db_err = None

            # ------------------------------
            # PLC 연결
            # ------------------------------
            if mc is None:
                try:
                    mc = connect_plc(cfg)
                except Exception as e:
                    raise PlcError(e)

                if plc_down_at is None:
                    write_log(f"[PLC CONNECT] 연결 성공 | {cfg['plc_ip']}:{cfg['plc_port']}")
                else:
                    write_log(
                        f"[PLC RECONNECT] 재접속 성공 | "
                        f"끊김 {time.monotonic() - plc_down_at:.1f}초"
                    )
                plc_down_at = None
                last_plc_err = None

                # 재접속 후에는 변화가 없어도 다음 전송 결과를 로그로 남김
                state.clear()

            process_once(conn, mc, cfg, state)

            time.sleep(cfg["poll_sec"])

        except KeyboardInterrupt:
            print()
            write_log("[STOP] 사용자 종료")
            break

        except Exception as e:
            err_db = isinstance(e, DbError) or isinstance(e, pyodbc.Error)
            err_text = str(e.args[0] if isinstance(e, (DbError, PlcError)) else e)

            if err_db:
                close_db(conn)
                conn = None
                if db_down_at is None:
                    db_down_at = time.monotonic()
                # 같은 오류가 반복되면 로그는 1번만
                if err_text != last_db_err:
                    write_log(f"[DB ERR] {err_text}")
                    last_db_err = err_text
            else:
                close_plc(mc)
                mc = None
                if plc_down_at is None:
                    plc_down_at = time.monotonic()
                if err_text != last_plc_err:
                    write_log(f"[PLC ERR] {err_text}")
                    last_plc_err = err_text

            print(f"[INFO] {cfg['reconnect_sec']}초 후 재시도", flush=True)

            try:
                time.sleep(cfg["reconnect_sec"])
            except KeyboardInterrupt:
                print()
                write_log("[STOP] 사용자 종료")
                break

    close_db(conn)
    close_plc(mc)


class DbError(Exception):
    pass


class PlcError(Exception):
    pass


if __name__ == "__main__":
    main()
