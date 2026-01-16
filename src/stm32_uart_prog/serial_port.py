import sys
import time

import serial
import serial.tools.list_ports

from stm32_uart_prog.loggers import logger


class SerialPort(serial.Serial):
    RECONNECT_INTERVAL = 2
    ports_valid: dict[str, str] = {}

    def __init__(self, port: str, baudrate: int, timeout: float | None = None):
        self.connected_time = time.perf_counter()

        if not SerialPort.ports_valid:
            SerialPort.__scan_ports()
        if sys.platform.startswith("win"):
            port = port.upper()
        if port not in SerialPort.ports_valid.keys():
            logger.error(f"{port}: could not find port")
            raise serial.SerialException(f"{port}: could not find port")

        super().__init__(port, baudrate, parity=serial.PARITY_EVEN, timeout=timeout, exclusive=True)
        logger.info(f"opened: {self}")

    def reconnect(self, se: serial.SerialException | Exception):
        if not self.port:
            return  # Prevent reconnecting if was not connected at least once
        if time.perf_counter() - self.connected_time < SerialPort.RECONNECT_INTERVAL:
            time.sleep(self.timeout or 0.1)
            return  # Prevent rapid reconnect attempts

        time.sleep(0.5)
        logger.error(f"{getattr(self, 'port', 'PORT:')}: {se}")
        try:
            self.close()
            SerialPort.ports_valid.clear()  # Clear the cached ports
            self.__init__(self.port, self.baudrate, self.timeout)
        except serial.SerialException as se:
            logger.exception(f"{se}")

    def send_data(self, data: bytes):
        try:
            if getattr(self, "is_open", False):
                written = self.write(data)
                if written != len(data):
                    raise serial.SerialException(f"incomplete write: {written}/{len(data)}")
                self.flush()
                time.sleep(0)
                logger.debug(f"{len(data)} bytes: {data.hex(sep=' ').upper()}")
            else:
                raise serial.SerialException("port is not open during send_data")
        except serial.SerialException as se:
            self.reconnect(se)

    def recv_data(self, size: int = 1, stall_timeout: float = 0):
        """Receive data from the serial port
        Args:
            size (int): The number of bytes to read
            stall_timeout (float): Timeout in seconds to wait for data changes before giving up
        Returns:
            bytes: The received data
        Notes:
            - If no `stall_timeout` provided, default serial timeout will be used
        """

        data = bytearray()

        try:
            if stall_timeout < 0:
                raise ValueError("stall_timeout must be a non-negative float")
            if (stall_timeout * 1000) % 10 != 0:
                raise ValueError("stall_timeout must be a multiple of 0.01 seconds (10 ms)")
            if not getattr(self, "is_open", False):
                raise serial.SerialException("port is not open during recv_data")

            if stall_timeout:
                start_time = time.perf_counter()
                while len(data) < size:
                    available = self.in_waiting
                    if available:
                        if available > size - len(data):
                            logger.warning(f"available bytes {available} exceed remaining {size - len(data)}")
                            available = size - len(data)
                        chunk = self.read(available)
                        if chunk:
                            data.extend(chunk)
                            start_time = time.perf_counter()
                        else:
                            logger.error("bytes available in buffer but could not read any data")
                    elif time.perf_counter() - start_time > stall_timeout:
                        logger.warning(
                            f"stall timeout after waiting for {size} bytes, got {len(data)} bytes {list(data)}"
                        )
                        return bytes(data)
                    else:
                        time.sleep(1e-2)
            else:
                data = self.read(size)  # Read with default timeout

            data_length = len(data)
            if data_length == size:
                logger.debug(f"{data_length} bytes: {list(data)}")
            elif data_length != 0:
                logger.debug(f"length {data_length} does not match requested size {size}: {list(data)}")
            else:
                logger.debug("no data received")
        except serial.SerialException as se:
            self.reconnect(se)
        except Exception as e:
            logger.exception(f"common error during data rcv: {e}")
        finally:
            return bytes(data)

    def recv_all(self):
        data = bytes()

        try:
            if not getattr(self, "is_open", False):
                raise serial.SerialException("port is not open during recv_all")
            data = self.read_all()
            if data:
                logger.debug(f"{len(data)} bytes: {list(data)}")
            else:
                logger.error("could not read data frame from buffer")
                pass
        except serial.SerialException as se:
            self.reconnect(se)
        except Exception as e:
            logger.exception(f"common error during data frame rcv: {e}")
        finally:
            return bytes() if data is None else data

    def reset_input(self):
        try:
            if getattr(self, "is_open", False):
                if self.in_waiting:
                    logger.debug(f"discarding input buffer with {self.in_waiting} bytes")
                self.read_all()
            else:
                raise serial.SerialException("port is not open during reset_input")
        except (serial.SerialException, Exception) as se_e:
            self.reconnect(se_e)

    @classmethod
    def __scan_ports(cls):
        ports = serial.tools.list_ports.comports()
        for i, (port, desc, hwid) in enumerate(ports):
            dev = ports[i]
            cls.ports_valid[port] = f"{desc}, manufacturer: {dev.manufacturer}, location: {dev.location}"
        logger.info(f"Available ports:\n{chr(10).join(f'{k}: {v}' for k, v in cls.ports_valid.items())}")

    @classmethod
    def get_ports(cls):
        dev_name = "COM" if sys.platform.startswith("win") else "/dev/ttyUSB"
        ports_valid: list[tuple[str, str]] = []

        try:
            cls.__scan_ports()
            for port, desc in cls.ports_valid.items():
                if port.startswith(dev_name):
                    ports_valid.append((port, desc))
        except serial.SerialException as e:
            logger.exception(f"error creating list of ports with name {dev_name} from {cls.ports_valid}: {e}")
        return ports_valid
