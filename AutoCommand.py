import time
from datetime import datetime
from pathlib import Path
from pymcprotocol import Type3E


class PLCLogger:
    WORD_DEVS = {"R", "D", "W"}
    BIT_DEVS = {"M", "X", "Y", "B"}

    def __init__(
        self,
        ip: str,
        port: int = 7777,
        poll_sec: float = 0.2,
        log_dir: str = "logs",
        max_log_files: int = 30,
        timeout_sec: float = 2.0,
        reconnect_sec: float = 2.0,
        read_ranges: list[tuple[str, int]] | None = None,
        watch_comments: dict | None = None,      # 주소 코멘트
        value_comments: dict | None = None,      # 값 코멘트(ENUM)
        string_devices: dict | None = None,      # 문자열 장치 {"D2300": ("CarrierID", 4)}
    ):
        self.ip = ip
        self.port = port
        self.poll_sec = poll_sec
        self.timeout_sec = timeout_sec
        self.reconnect_sec = reconnect_sec

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.max_log_files = max_log_files

        self.read_ranges = read_ranges or []
        self.watch_comments = watch_comments or {}
        self.watch_set = set(self.watch_comments.keys())

        self.value_comments = value_comments or {}
        self.string_devices = string_devices or {}

        self.mc = Type3E()
        self.prev = {}           # 일반 값 이전값
        self.prev_strings = {}   # 문자열 이전값
        self._last_cleanup_day = None

    def _today_log_path(self) -> Path:
        day = datetime.now().strftime("%Y%m%d")
        return self.log_dir / f"plc_log_{day}.txt"

    def _cleanup_old_logs_if_needed(self):
        files = sorted(self.log_dir.glob("plc_log_*.txt"), key=lambda p: p.stat().st_mtime)
        if len(files) <= self.max_log_files:
            return

        for p in files[: len(files) - self.max_log_files]:
            try:
                p.unlink()
            except Exception:
                pass

    def _write_line(self, line: str):
        print(line)
        with open(self._today_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def _split_dev(head: str):
        dev = "".join([c for c in head if c.isalpha()]).upper()
        num = int("".join([c for c in head if c.isdigit()]) or "0")
        return dev, num

    def connect(self):
        self.mc.setaccessopt(commtype="binary")
        try:
            self.mc.settimeout(self.timeout_sec)
        except Exception:
            pass
        self.mc.connect(self.ip, self.port)

    def close(self):
        try:
            self.mc.close()
        except Exception:
            pass

    def _read_range_to_dict(self, head: str, size: int) -> dict:
        dev, base_num = self._split_dev(head)

        if dev in self.WORD_DEVS:
            vals = self.mc.batchread_wordunits(headdevice=head, readsize=size)
        elif dev in self.BIT_DEVS:
            vals = self.mc.batchread_bitunits(headdevice=head, readsize=size)
        else:
            return {}

        out = {}
        for i, v in enumerate(vals):
            out[f"{dev}{base_num + i}"] = v
        return out

    def read_all(self) -> dict:
        data = {}
        for head, size in self.read_ranges:
            data.update(self._read_range_to_dict(head, size))
        return data

    def _format_value_comment(self, device: str, value: int) -> str:
        mapping = self.value_comments.get(device)
        if not mapping:
            return ""
        return mapping.get(value, "")

    def _decode_string(self, data: dict, start_dev: str, word_length: int) -> str:
        """
        D2300~D2303 같은 WORD 데이터를 문자열로 변환
        word_length=4 이면 8문자까지 가능
        """
        dev, base_num = self._split_dev(start_dev)
        chars = []

        for i in range(word_length):
            word = data.get(f"{dev}{base_num + i}", 0)

            low = word & 0xFF
            high = (word >> 8) & 0xFF

            if low != 0:
                chars.append(chr(low))
            if high != 0:
                chars.append(chr(high))

        return "".join(chars).strip()

    def _process_normal_devices(self, data: dict, ts: str):
        for device in self.watch_set:
            if device not in data:
                continue

            new = data[device]
            old = self.prev.get(device, None)

            if old is None:
                self.prev[device] = new
                continue

            if old != new:
                dev_comment = self.watch_comments.get(device, "")
                val_comment = self._format_value_comment(device, new)

                parts = [ts, device]
                if dev_comment:
                    parts.append(dev_comment)
                if val_comment:
                    parts.append(val_comment)
                parts.append(f"{old} -> {new}")

                self._write_line(" | ".join(parts))
                self.prev[device] = new

    def _process_string_devices(self, data: dict, ts: str):
        for start_dev, info in self.string_devices.items():
            label, word_length = info

            new_str = self._decode_string(data, start_dev, word_length)
            old_str = self.prev_strings.get(start_dev, None)

            if old_str is None:
                self.prev_strings[start_dev] = new_str
                continue

            if old_str != new_str:
                self._write_line(f"{ts} | {start_dev} | {label} | {old_str} -> {new_str}")
                self.prev_strings[start_dev] = new_str

    def process_once(self):
        today = datetime.now().date()
        if self._last_cleanup_day != today:
            self._cleanup_old_logs_if_needed()
            self._last_cleanup_day = today

        data = self.read_all()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        self._process_normal_devices(data, ts)
        self._process_string_devices(data, ts)

    def run_forever(self):
        while True:
            try:
                self.connect()
                print(f"[OK] Connected: {self.ip}:{self.port}")

                while True:
                    self.process_once()
                    time.sleep(self.poll_sec)

            except KeyboardInterrupt:
                print("\n[STOP] user interrupt")
                self.close()
                break

            except Exception as e:
                print(f"[ERR] {e} -> reconnect in {self.reconnect_sec}s")
                self.close()
                time.sleep(self.reconnect_sec)


if __name__ == "__main__":
    read_ranges = [
        ("D1000", 350),
        ("D2000", 350),
        ("D100", 100),
        ("M0", 64),
        ("R0", 200),
        ("X0", 10),
    ]

    # 로그 남길 주소등록
    watch_comments = {
        "D1100": "CarrierMoveToAirlockCompleteEvent",
        "D1101": "CarrierMoveToInnerCompleteEvent",
        "D1201": "CarrierInstallReply",
        "D1202": "CarrierRemoveReply",
        #"D1000": "PcHandshaking",
        #"D2000": "PlcHandshaking",
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
        # "M510": "CarrierUpDetect",
        # "M511": "CarrierDownDetect",
        "M1999": "ALARM",
        # "X0": "NONE",
        # "X1": "NONE",
        
    }

    # 값에 따른 코멘트(ENUM)
    value_comments = {
        "M0": {
            0: "OFF",
            1: "ON",
        }
    }

    # 문자열 장치
    # D2300~D2303 = 4워드 = 최대 8문자
    string_devices = {
        "D2300": ("InstallCarrierID", 4),
        "D2304": ("RemoveCarrierID", 4),
        "D120": ("WaitCarrierData", 4),
        
    }

    logger = PLCLogger(
        ip="192.168.1.20",
        port=7777,
        poll_sec=0.2,
        log_dir=r"C:\ca\AirLock\log",
        max_log_files=30,
        read_ranges=read_ranges,
        watch_comments=watch_comments,
        value_comments=value_comments,
        string_devices=string_devices,
    )
    logger.run_forever()