import os
import struct
import time

from intelhex import IntelHex
from tqdm import tqdm

from stm32_uart_prog.colors import *
from stm32_uart_prog.loggers import Loggers, logger
from stm32_uart_prog.serial_port import SerialPort, serial


class STM32BL:
    CHUNK = 256
    retries = 1
    start_address = 0

    SUPPORTED_DEVICE_ID = [0x0413]

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
        "activate": 0x7F,
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

    ACK = 0x79
    NACK = 0x1F
    failed_once = False
    __target_id = 0

    def __init__(self, sp: SerialPort, hexfile: str = ""):
        if not sp:
            raise ValueError("no SerialPort instance provided")
        if not hexfile:
            raise ValueError("hexfile path is required")
        if not os.path.exists(hexfile):
            raise FileNotFoundError(f"hexfile not found: {hexfile}")

        self.ser = sp
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
            f"firmware {MAGENTA}{hexfile}{RESET} ({BLUE}{self.data_len}{RESET} bytes) parsed and occupies sectors {BLUE}{self.used_sectors[0]}-{self.used_sectors[-1]}{RESET}"
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

    def init(self, dev_id: int, total_bar: tqdm):
        self.__target_id = dev_id
        for attempt in range(3):
            # Target assumed not in bootloader mode, try to enter it
            total_bar.refresh()
            time.sleep(0.1)
            self.ser.send_data(self.COMMAND_SET["activate"].to_bytes())
            if self._read_ack():
                break
        else:
            # Target assumed to be in bootloader mode already
            time.sleep(0.5)
            for attempt in range(3):
                total_bar.refresh()
                time.sleep(0.1)
                if self.get_commands():
                    break
            else:
                raise RuntimeError(f"bootloader sync failed")

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
        return hex(int.from_bytes(pid, signed=True))

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

        time.sleep(0.5)  # Erase can take time
        return self._read_ack()

    def start_application(self, addr: int):
        if not self.cmd(self.COMMAND_SET["go"]):
            return False

        a = struct.pack(">I", addr)  # > = big-endian, I = uint32_t
        self.ser.send_data(a + bytes([self._checksum(a)]))
        return self._read_ack()

    def cmd(self, cmd: int):
        for attempt in range(5):
            self.ser.send_data(bytes([cmd, cmd ^ 0xFF]))
            if self._read_ack():
                return True
            else:
                logger.warning(f"target ID{self.__target_id}: command {hex(cmd)} attempt {attempt + 1} failed")
                continue
        logger.error(f"target ID{self.__target_id}: command {hex(cmd)} NACK")
        return False

    def probe_bootloader(self, timeout=10.0, interval=0.01):
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
                time.sleep(interval)
            return False
        except serial.SerialException as se:
            logger.exception(se)
            self.ser.reconnect(se)
            return False
        finally:
            if timeout_orig is not None:
                try:
                    self.ser.timeout = timeout_orig
                except Exception:
                    pass

    @classmethod
    def sector_for_address(cls, addr):
        for i, (start, size) in enumerate(cls.FLASH_SECTORS):
            if start <= addr < start + size:
                return i
        return None
