import array
import math
import os
import struct
import time

from intelhex import IntelHex, IntelHexError
from tqdm import tqdm

from .colors import *
from .loggers import log
from .serial_port import SerialPort, serial


class STM32BL:
    attempts_erase = 1
    attempts_cmd = 1
    start_address = 0
    initial_baudrate = 57600
    failed_once = False
    __target_id = 0

    ACTIVATE = 0x7F
    ACK = 0x79
    NACK = 0x1F
    CHUNK = 256
    SUPPORTED_DEVICE_ID = [0x0413]
    BAUDRATES = [19200, 38400, 56000, 57600, 74880, 76800, 115200, 230400]

    SECTOR_ERASE_TIME_MAX = float(4 + 1)  # [sec]
    FLASH_SECTORS = [
        (0x08000000, 16 * 1024),
        (0x08004000, 16 * 1024),
        (0x08008000, 16 * 1024),
        (0x0800C000, 16 * 1024),
        (0x08010000, 64 * 1024),
        (0x08020000, 128 * 1024),
        (0x08040000, 128 * 1024),
        (0x08060000, 128 * 1024),
        (0x08080000, 128 * 1024),
        (0x080A0000, 128 * 1024),
        (0x080C0000, 128 * 1024),
        (0x080E0000, 128 * 1024),
    ]

    COMMAND_SET = {
        "get": 0x00,
        # "get_version": 0x01,
        "get_id": 0x02,
        "read_memory": 0x11,
        "go": 0x21,
        "write_memory": 0x31,
        # "erase": 0x43,
        "extended_erase": 0x44,
        # "special": 0x50,
        # "extended_special": 0x51,
        # "write_protect": 0x63,
        # "write_unprotect": 0x73,
        # "readout_protect": 0x82,
        # "readout_unprotect": 0x92,
        # "get_checksum": 0xA1,
    }
    COMMAND_REQUIRED = {f"0x{b:02X}" for b in COMMAND_SET.values()}

    def __init__(self, hexfile: str = "", flash_size: int = 0, crc_range: dict[str, int] = {}):
        if not os.path.isfile(hexfile):
            raise RuntimeError(f"hexfile '{hexfile}' not found")
        if not hexfile.endswith(".hex"):
            raise RuntimeError(f"only .hex files are supported")

        self.ih = IntelHex(hexfile)
        self.ih.padding = 0xFF
        self.data = self.ih.tobinarray()
        self.data_len = len(self.data)

        if self.data_len == 0:
            raise ValueError(f"hexfile is empty or invalid: {hexfile}")

        self.min_addr: int = self.ih.minaddr() or 0
        self.max_addr: int = self.ih.maxaddr() or 0
        if not self.min_addr or not self.max_addr or self.data_len == 0:
            raise ValueError("hexfile contains no addressable data")

        self.used_sectors = sorted(
            {s for addr in range(self.min_addr, self.max_addr + 1, self.CHUNK) if (s := self.__sector_for_address(addr)) is not None}
        )
        if not self.used_sectors:
            raise ValueError("hexfile doesn't map to any flash sectors")
        if self.max_addr > self.FLASH_SECTORS[-1][0] + self.FLASH_SECTORS[-1][1] - 1:
            raise RuntimeError("hexfile content is out of target's ROM boundaries")

        # CRC range resolution
        req_start = crc_range.get("start")
        req_end = crc_range.get("end")
        calc_start = req_start if req_start is not None else self.min_addr
        calc_end = req_end if req_end is not None else (self.max_addr + 1)
        calc_start &= ~3
        calc_end = (calc_end + 3) & ~3
        expected_bytes = calc_end - calc_start
        if expected_bytes < 4:
            raise SystemExit(f"resolved CRC range too small: 0x{calc_start:X}-0x{calc_end:X} " f"({expected_bytes} byte(s), need at least 4)")

        start_idx = max(0, min(calc_start - self.min_addr, self.data_len))
        end_idx = max(0, min(calc_end - self.min_addr, self.data_len))
        sub_data = self.data[start_idx:end_idx]

        if len(sub_data) < expected_bytes:
            if len(sub_data) == 0:
                sub_data = array.array("B", b"\xff" * expected_bytes)
            else:
                if calc_start < self.min_addr:
                    front = self.min_addr - calc_start
                    sub_data = array.array("B", b"\xff" * front) + sub_data
                if len(sub_data) < expected_bytes:
                    sub_data = sub_data + array.array("B", b"\xff" * (expected_bytes - len(sub_data)))

        self.crc32 = self.__stm32_crc32(sub_data)

        # Print
        eip = self.ih.start_addr.get("EIP", 0) if self.ih.start_addr else 0
        usage = f"{self.data_len / flash_size * 100:.2f}%" if flash_size else "n/a"
        print(f"Firmware    : {MAGENTA}{hexfile}{RESET}")
        print(f"EIP         : {CYAN}0x{eip:08X}{RESET}")
        print(f"Start       : {CYAN}0x{self.min_addr:08X}{RESET}")
        print(f"End         : {CYAN}0x{self.max_addr:08X}{RESET}")
        print(f"Flash used  : {CYAN}{usage}{RESET} - {CYAN}{self.data_len}{RESET} bytes ({self.data_len / 1024:.2f} KB)")
        print(f"CRC Range   : {CYAN}0x{calc_start:08X} - 0x{calc_end:08X}{RESET} ({expected_bytes // 4} words)")
        print(f"CRC32       : {CYAN}0x{self.crc32:08X}{RESET}")

        log.info(f"firmware: {hexfile} ({self.data_len} bytes)")
        log.info(f"used sectors: {self.used_sectors}")
        log.info(f"CRC32: 0x{self.crc32:08X}")

    def __stm32_crc32(self, data, timeout: float = 30.0) -> int:
        """Calculates the standard STM32 hardware CRC32 over 32-bit words."""
        POLY = 0x04C11DB7
        crc = 0xFFFFFFFF

        if isinstance(data, array.array):
            data_len = data.tobytes()
        else:
            data_len = bytes(data)
        if len(data_len) % 4 != 0:
            raise ValueError("CRC data payload must be 32-bit word aligned.")

        deadline = time.monotonic() + timeout
        for i in range(0, len(data_len), 4):
            if time.monotonic() > deadline:
                raise TimeoutError(f"CRC32 calculation timed out after {timeout}s ({i} of {len(data_len)} bytes processed)\nWrong CRC range?")
            word = int.from_bytes(data_len[i : i + 4], "little")
            crc ^= word
            for _ in range(32):
                if crc & 0x80000000:
                    crc = (crc << 1) ^ POLY
                else:
                    crc <<= 1
                crc &= 0xFFFFFFFF
        return crc

    def _read_ack(self, timeout: float = 0):
        r = self.ser.recv_data(1, timeout)
        if not r or r[0] != self.ACK:
            self.probe_bootloader(0.5)  # Reconstruct byte order
            time.sleep(0.05)
            self.ser.reset_input()  # Clear buffer
            return False
        return True

    def sync(self, sp: SerialPort, total_bar: tqdm, dev_id: int, tune_requests=500, success_threshold=0.0):
        self.__target_id = dev_id
        span, step = 0.2, 0.005
        steps = int(span / step)
        self.ser = sp
        self.baudrate = self.initial_baudrate = sp.baudrate

        if not success_threshold:
            activated = False
            for _ in range(5):
                self.ser.send_data(self.ACTIVATE.to_bytes())
                time.sleep(0.1)
                resp = self.ser.recv_all()
                if resp or any(b in (self.ACK, self.NACK) for b in resp):
                    activated = True
                    break
            self.ser.reset_input()
            return activated

        def sync_rate(baud: int, bar: tqdm) -> float:
            try:
                self.ser.baudrate = baud
            except Exception:
                return 0.0

            byte_time = 11 / baud
            response_count = 0
            for _ in range(tune_requests):
                bar.update(1)
                self.ser.send_data(b"\x7f")
                time.sleep(max(byte_time * 2, 0.002))
                self.ser.send_data(b"\x7f")
                time.sleep(max(byte_time * 4, 0.005))
                resp = self.ser.recv_all()
                if not resp or not any(b in (self.ACK, self.NACK) for b in resp):
                    break
                response_count += 1
            return response_count / tune_requests

        # Build baud list
        baud_candidates = [self.initial_baudrate] * 20  # Try initial baud more times
        baud_candidates += sorted(
            {
                int(self.initial_baudrate * (1 + i * step))
                for i in range(-steps, steps + 1)
                if int(self.initial_baudrate * (1 + i * step)) > 0 and int(self.initial_baudrate * (1 + i * step)) != self.initial_baudrate
            }
        )
        other_bases = [b for b in self.BAUDRATES if b > 0 and b != self.initial_baudrate]
        baud_candidates += [
            b
            for b in sorted({int(base * (1 + i * step)) for base in other_bases for i in range(-steps, steps + 1) if int(base * (1 + i * step)) > 0})
            if b not in baud_candidates
        ]

        best_baud = None
        best_rate = 0.0
        for baud in baud_candidates:
            with tqdm(total=tune_requests, desc=f"Sync baud {baud}", leave=False) as bar:
                rate = sync_rate(baud, bar)

            log.debug(f"target ID{self.__target_id}: baud={baud}, success_rate={rate:.2f}")
            if rate > best_rate:
                best_rate = rate
                best_baud = baud

            # Perfect match: stop scanning
            if rate == 1.0:
                best_baud = baud
                best_rate = rate
                break
            total_bar.update(0)

        # Decide result
        if best_baud is None or best_rate < success_threshold:
            raise RuntimeError(f"target ID{self.__target_id} - could not sync baudrate")

        # Apply selected baud and finalize
        self.probe_bootloader(10, 0.01, verbose=False)
        time.sleep(0.1)
        self.ser.reset_input()
        self.baudrate = self.ser.baudrate = best_baud
        total_bar.write(f"Sync at baudrate {best_baud} ({best_rate:.1%})")
        if not math.isclose(best_baud, self.initial_baudrate, rel_tol=0.01):
            total_bar.write(f"{YELLOW}Baudrate after sync differs from initial: {best_baud}/{self.initial_baudrate}{RESET}")
        return True

    def baud_tune(self, total_bar: tqdm, tune_requests=500, success_threshold=0.0):
        """
        Autodetect target baudrate using STM32 GET command.

        Accepts baud if:
        - 100% GET success -> immediate lock
        - otherwise picks best baud >= `success_threshold`
        """

        orig_cmd_attempts = self.attempts_cmd
        orig_baud = self.ser.baudrate
        self.attempts_cmd = 2
        self.ser.timeout = 11 * 20 / orig_baud * 30
        span, step = 0.1, 0.002
        steps = int(span / step)

        if not success_threshold:
            return orig_baud

        # Build baud list
        baud_candidates = [orig_baud] * 10  # Try initial baud more times
        baud_candidates += sorted(
            {
                int(orig_baud * (1 + i * step))
                for i in range(-steps, steps + 1)
                if int(orig_baud * (1 + i * step)) > 0 and int(orig_baud * (1 + i * step)) != orig_baud
            }
        )

        best_baud = None
        best_rate = 0.0

        try:
            for baud in baud_candidates:
                try:
                    self.baudrate = self.ser.baudrate = baud
                except Exception as e:
                    log.warning(f"failed to set baudrate {baud}: {e}")
                    continue

                response_count = 0
                with tqdm(
                    total=tune_requests,
                    desc=f"Tune baud {baud}",
                    leave=False,
                    unit="pass",
                    dynamic_ncols=True,
                    position=0,
                ) as bar:
                    for _ in range(tune_requests):
                        bar.update(1)
                        cmds = self.get_commands()
                        if not cmds or not self.COMMAND_REQUIRED.issubset(cmds):
                            break
                        response_count += 1

                rate = response_count / tune_requests

                if rate == 1.0:
                    best_baud, best_rate = baud, rate
                    return baud

                if rate >= success_threshold and rate > best_rate:
                    best_baud, best_rate = baud, rate

            if best_baud:
                self.baudrate = self.ser.baudrate = best_baud
                log.warning(f"no 100% baud found, using {best_baud} ({best_rate:.1%} success)")
                return best_baud

            raise RuntimeError("baudrate autodetection failed")
        finally:
            self.attempts_cmd = orig_cmd_attempts
            self.ser.reset_input()
            self.ser.timeout = (11 * 256 / self.baudrate) * 1.3  # Timeout to read one mem page based on new baudrate
            time.sleep(0.1)

            if best_rate:
                if self.baudrate != orig_baud:
                    total_bar.write(f"Baudrate tuned to {self.baudrate} ({best_rate:.1%})")
            else:
                total_bar.write(f"{RED}No baudrate candidate found{RESET}")

    def get_commands(self) -> set[str]:
        if not self.cmd(self.COMMAND_SET["get"]):
            return set()

        cmds_len = self.ser.recv_data(1)
        if not cmds_len:
            return set()

        expected_len = cmds_len[0] + 1
        cmds = self.ser.recv_data(expected_len)

        if len(cmds) != expected_len:
            return set()

        self._read_ack()
        return {f"0x{b:02X}" for b in cmds}

    def get_pid(self):
        pid = bytes()

        if not self.cmd(self.COMMAND_SET["get_id"]):
            return str()

        pid_len = self.ser.recv_data(1)
        if not pid_len:
            return str()

        pid = self.ser.recv_data(pid_len[0] + 1)
        if len(pid) != pid_len[0] + 1:
            return str()

        self._read_ack()
        return hex(int.from_bytes(pid))

    def read_mem(self, addr, size):
        if not self.cmd(self.COMMAND_SET["read_memory"]):
            return bytes()

        a = struct.pack(">I", addr)
        self.ser.send_data(a + bytes([self.__checksum(a)]))
        if not self._read_ack():
            return bytes()

        self.ser.send_data(bytes([size - 1, (size - 1) ^ 0xFF]))
        if not self._read_ack():
            return bytes()
        return self.ser.recv_data(size)

    def write_mem(self, addr: int, data: bytes):
        if not self.cmd(self.COMMAND_SET["write_memory"]):
            return False

        a = struct.pack(">I", addr)
        self.ser.send_data(a + bytes([self.__checksum(a)]))
        if not self._read_ack():
            return False

        data_len = len(data)
        self.ser.send_data(bytes([data_len - 1]))
        self.ser.send_data(bytes(data))
        self.ser.send_data(bytes([self.__checksum(bytes([data_len - 1]) + data)]))
        return self._read_ack()

    def erase_sector(self, sector: int):
        if not self.cmd(self.COMMAND_SET["extended_erase"]):
            return False

        # Erase ONE sector
        payload = bytearray()
        payload += (0).to_bytes(2, "big")  # N = 0x00 (one sector)
        payload += sector.to_bytes(2, "big")  # Sector number

        chk = self.__checksum(payload)
        self.ser.send_data(bytes(payload + bytes([chk])))

        return self._read_ack(self.SECTOR_ERASE_TIME_MAX)

    def start_application(self, addr: int):
        if not self.cmd(self.COMMAND_SET["go"]):
            return False

        a = struct.pack(">I", addr)  # > = big-endian, I = uint32_t
        self.ser.send_data(a + bytes([self.__checksum(a)]))
        return self._read_ack()

    def cmd(self, cmd: int):
        for attempt in range(self.attempts_cmd):
            self.ser.send_data(bytes([cmd, cmd ^ 0xFF]))
            if self._read_ack():
                return True
            else:
                log.warning(f"target ID{self.__target_id}: command {hex(cmd)} failed ({attempt + 1}/3) ")
                continue
        log.error(f"target ID{self.__target_id}: command {hex(cmd)} NACK")
        return False

    def probe_bootloader(self, timeout=5.0, interval=0.005, verbose=True):
        """
        Continuously send `0xFF` until any response is received
        or timeout expires.

        Returns:
            bytes: first received byte, or b'' if timeout or error
        """
        end = time.monotonic() + timeout
        if verbose:
            log.warning(f"target ID{self.__target_id}: resync requested")

        try:
            if not self.ser or not self.ser.is_open:
                raise serial.SerialException(f"target ID{self.__target_id}: serial port not open during probe_bootloader")

            while time.monotonic() < end:
                self.ser.send_data(b"\xff")
                time.sleep(interval)
                resp = self.ser.recv_all()
                if resp:
                    return True
            return False
        except serial.SerialException as se:
            log.exception(se)
            self.ser.reconnect(se)
            return False

    @classmethod
    def __checksum(cls, data):
        c = 0
        for b in data:
            c ^= b
        return c

    @classmethod
    def __sector_for_address(cls, addr):
        for i, (start, size) in enumerate(cls.FLASH_SECTORS):
            if start <= addr < start + size:
                return i
        return None
