import math
import os
import struct
import time

from intelhex import IntelHex, IntelHexError
from tqdm import tqdm

from stm32_uart_prog.colors import *
from stm32_uart_prog.loggers import Loggers, logger
from stm32_uart_prog.serial_port import SerialPort, serial


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

    def __init__(self, sp: SerialPort, hexfile: str = ""):
        if not sp:
            raise ValueError("no SerialPort instance provided")
        if not hexfile:
            raise ValueError("hexfile path is required")
        if not os.path.exists(hexfile):
            raise FileNotFoundError(f"hexfile not found: {hexfile}")

        self.ser = sp
        self.baudrate = self.initial_baudrate = sp.baudrate
        self.ih = IntelHex(hexfile)
        if not self.ih:
            raise AttributeError("could not parse hexfile")
        self.ih.padding = 0xFF
        self.data = self.ih.tobinarray()
        self.data_len = len(self.data)
        if self.data_len == 0:
            raise ValueError(f"hexfile is empty or invalid: {hexfile}")
        addresses = list(self.ih.addresses())
        if not addresses:
            raise ValueError("hexfile contains no data")
        self.min_addr: int = min(addresses)
        self.max_addr: int = max(addresses)
        self.used_sectors = sorted({s for addr in addresses if (s := self.sector_for_address(addr)) is not None})
        if not self.used_sectors:
            raise ValueError("hexfile doesn't map to any flash sectors")
        if self.max_addr > self.FLASH_SECTORS[-1][0] + self.FLASH_SECTORS[-1][1] - 1:
            raise RuntimeError("hexfile content is out of target's ROM boundaries")

        print(
            f"Firmware {MAGENTA}{hexfile}{RESET} ({BLUE}{self.data_len}{RESET} bytes) parsed and occupies sectors {BLUE}{self.used_sectors[0]}-{self.used_sectors[-1]}{RESET}"
        )
        logger.info(f"firmware: {hexfile} ({self.data_len} bytes)")
        logger.info(f"used sectors: {self.used_sectors}")

    def _checksum(self, data):
        c = 0
        for b in data:
            c ^= b
        return c

    def _read_ack(self):
        r = self.ser.recv_data(1)
        if not r or r[0] != self.ACK:
            self.probe_bootloader(0.5)  # Reconstruct byte order
            time.sleep(0.05)
            self.ser.reset_input()  # Clear buffer
            return False
        return True

    def sync(
        self,
        total_bar: tqdm,
        dev_id: int,
        skip_tune: bool = False,
        tune_requests: int = 1000,
        success_threshold: float = 0.7,
    ):
        self.__target_id = dev_id
        span, step = 0.2, 0.005
        steps = int(span / step)

        if skip_tune:
            activated = False
            for _ in range(5):
                self.ser.send_data(self.ACTIVATE.to_bytes())
                time.sleep(0.1)
                r = self.ser.recv_all()
                if r:
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
                time.sleep(max(byte_time * 2, 0.001))
                self.ser.send_data(b"\x7f")
                time.sleep(max(byte_time * 4, 0.001))
                r = self.ser.recv_all()
                if not r or r[0] not in (self.ACK, self.NACK):
                    break
                response_count += 1
            return response_count / tune_requests

        # Build baud list
        baud_candidates = [self.initial_baudrate] * 50  # Try initial baud more times
        baud_candidates += sorted(
            {
                int(self.initial_baudrate * (1 + i * step))
                for i in range(-steps, steps + 1)
                if int(self.initial_baudrate * (1 + i * step)) > 0
                and int(self.initial_baudrate * (1 + i * step)) != self.initial_baudrate
            }
        )
        other_bases = [b for b in self.BAUDRATES if b > 0 and b != self.initial_baudrate]
        baud_candidates += [
            b
            for b in sorted(
                {
                    int(base * (1 + i * step))
                    for base in other_bases
                    for i in range(-steps, steps + 1)
                    if int(base * (1 + i * step)) > 0
                }
            )
            if b not in baud_candidates
        ]

        best_baud = None
        best_rate = 0.0
        for baud in baud_candidates:
            with tqdm(total=tune_requests, desc=f"Sync baud {baud}", leave=False) as bar:
                rate = sync_rate(baud, bar)

            logger.debug(f"target ID{self.__target_id}: baud={baud}, success_rate={rate:.2f}")
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
        self.baudrate = self.ser.baudrate = best_baud
        total_bar.write(f"Sync at baudrate {best_baud} ({best_rate:.1%})")
        if not math.isclose(best_baud, self.initial_baudrate, rel_tol=0.01):
            total_bar.write(
                f"{YELLOW}Baudrate after sync differs from initial: {best_baud}/{self.initial_baudrate}{RESET}"
            )
        self.ser.reset_input()
        return True

    def baud_tune(self, total_bar: tqdm, tune_requests=500, success_threshold=0.7):
        """
        Autodetect target baudrate using STM32 GET command.

        Accepts baud if:
        - 100% GET success -> immediate lock
        - otherwise picks best baud >= `success_threshold`
        """

        required_cmds = set(self.COMMAND_SET.values())

        orig_cmd_attempts = self.attempts_cmd
        orig_baud = self.ser.baudrate

        self.attempts_cmd = 1
        self.ser.timeout = (11 * 30 / orig_baud) * 1.3

        span, step = 0.1, 0.002
        steps = int(span / step)

        # Build baud list
        baud_candidates = [orig_baud] * 5  # Try initial baud more times
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
                    logger.warning(f"failed to set baudrate {baud}: {e}")
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
                        if not cmds or not required_cmds.issubset(cmds):
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
                logger.warning(f"no 100% baud found, using {best_baud} ({best_rate:.1%} success)")
                return best_baud

            raise RuntimeError("baudrate autodetection failed")
        finally:
            self.attempts_cmd = orig_cmd_attempts
            self.ser.timeout = (11 * 256 / self.baudrate) * 1.3  # Timeout to read one mem page based on new baudrate

            if best_rate:
                if self.baudrate != orig_baud:
                    total_bar.write(f"Baudrate tuned to {self.baudrate} ({best_rate:.1%})")
            else:
                total_bar.write(f"{RED}No baudrate candidate found{RESET}")

    def get_commands(self):
        cmds = bytes()

        if not self.cmd(self.COMMAND_SET["get"]):
            return bytes()

        cmds_len = self.ser.recv_data(1)
        if not cmds_len:
            return bytes()

        cmds = self.ser.recv_data(cmds_len[0] + 1)
        if len(cmds) != cmds_len[0] + 1:
            return bytes()

        self._read_ack()
        return cmds

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
        self.ser.send_data(a + bytes([self._checksum(a)]))
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
        self.ser.send_data(a + bytes([self._checksum(a)]))
        if not self._read_ack():
            return False

        data_len = len(data)
        self.ser.send_data(bytes([data_len - 1]))
        self.ser.send_data(bytes(data))
        self.ser.send_data(bytes([self._checksum(bytes([data_len - 1]) + data)]))
        return self._read_ack()

    def erase_sector(self, sector: int):
        if not self.cmd(self.COMMAND_SET["extended_erase"]):
            return False

        # Erase ONE sector
        payload = bytearray()
        payload += (0).to_bytes(2, "big")  # N = 0x00 (one sector)
        payload += sector.to_bytes(2, "big")  # Sector number

        chk = self._checksum(payload)
        self.ser.send_data(payload + bytes([chk]))

        time.sleep(1)  # Erase can take time
        return self._read_ack()

    def start_application(self, addr: int):
        if not self.cmd(self.COMMAND_SET["go"]):
            return False

        a = struct.pack(">I", addr)  # > = big-endian, I = uint32_t
        self.ser.send_data(a + bytes([self._checksum(a)]))
        return self._read_ack()

    def cmd(self, cmd: int):
        for attempt in range(3):
            self.ser.send_data(bytes([cmd, cmd ^ 0xFF]))
            if self._read_ack():
                return True
            else:
                logger.warning(f"target ID{self.__target_id}: command {hex(cmd)} failed ({attempt + 1}/3) ")
                continue
        logger.error(f"target ID{self.__target_id}: command {hex(cmd)} NACK")
        return False

    def probe_bootloader(self, timeout=1.0, interval=0.01):
        """
        Continuously send `0xFF` until any response is received
        or timeout expires.

        Returns:
            bytes: first received byte, or b'' if timeout or error
        """
        end = time.monotonic() + timeout
        timeout_orig = None
        logger.warning(f"target ID{self.__target_id}: resync requested")

        try:
            if not self.ser or not self.ser.is_open:
                raise serial.SerialException(
                    f"target ID{self.__target_id}: serial port not open during probe_bootloader"
                )

            timeout_orig = self.ser.timeout
            self.ser.timeout = interval

            while time.monotonic() < end:
                self.ser.send_data(b"\xff")
                resp = self.ser.recv_data(1)
                if resp:
                    return True
            return False
        except serial.SerialException as se:
            logger.exception(se)
            self.ser.reconnect(se)
            return False
        finally:
            if timeout_orig is not None:
                try:
                    time.sleep(0.1)
                    self.ser.timeout = timeout_orig
                except Exception:
                    pass

    @classmethod
    def sector_for_address(cls, addr):
        for i, (start, size) in enumerate(cls.FLASH_SECTORS):
            if start <= addr < start + size:
                return i
        return None
