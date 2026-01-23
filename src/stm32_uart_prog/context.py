"""Context-related functions for stm32-uart-prog"""

import struct
import time

from stm32_uart_prog.loggers import Loggers, logger
from stm32_uart_prog.serial_port import SerialPort, serial


def getCrc8(buffer):
    """CRC-8/GSM-A"""

    crc = 0

    for cell in buffer:
        crc ^= cell

        for i in range(8):
            crc = ((crc << 1) ^ 0x1D if crc & 0x80 else crc << 1) & 0xFF
    return crc


def be_quiet(sp: SerialPort, bl_baudrate: int):
    """Send mute command to all devices

    Args:
        sp (SerialPort): The serial port to use
        bl_baudrate (int): Original bootloader baudrate
    """

    dev_id = 0xFFFF  # Broadcast ID
    orig_baudrate = bl_baudrate
    orig_parity = serial.PARITY_EVEN

    try:
        time.sleep(7)  # Wait while previous device enters main app
        logger.info("sending mute command")

        # Cache original settings
        orig_baudrate = sp.baudrate
        orig_parity = sp.parity

        # Force non bootloader (application) settings
        sp.baudrate = 115200
        sp.parity = serial.PARITY_NONE

        # Frame without CRC
        frame = struct.pack(
            "<BBHBBBBB",  # < = little-endian, B = uint8_t, H = uint16_t
            0xAA,  # Preamble
            1,  # Frame length // 10
            dev_id,  # Device ID (uint16)
            0x03,  # Command type
            0xDA,  # Command: mute device
            0,  # Reserved
            0,  # Reserved
            0,  # Reserved
        )
        frame += bytes([getCrc8(frame)])

        time.sleep(0.5)
        for _ in range(5):
            sp.send_data(frame)
            time.sleep(0.5)
    except Exception as e:
        logger.exception(f"error sending mute command: {e}")
        raise
    finally:
        # Restore original settings
        sp.baudrate = orig_baudrate
        sp.parity = orig_parity
        sp.reset_input()
        time.sleep(0.5)


def enter_bootloader(sp: SerialPort, dev_id: int, bl_baudrate: int):
    """Send enter bootloader command to a specific device

    Args:
        sp (SerialPort): The serial port to use
        dev_id (int): The device ID to send the command to
        bl_baudrate (int): Original bootloader baudrate

    """

    orig_baudrate = bl_baudrate
    orig_parity = serial.PARITY_EVEN

    try:
        logger.info(f"target ID{dev_id}: sending enter bootloader command")

        if dev_id < 0 or dev_id > 0xFFFF:
            raise ValueError(f"invalid device ID: {dev_id}, must be 0-65535")

        # Cache original settings
        orig_baudrate = sp.baudrate
        orig_parity = sp.parity

        # Force non bootloader (application) settings
        sp.baudrate = 115200
        sp.parity = serial.PARITY_NONE

        # Frame without CRC
        frame = struct.pack(
            "<BBHBBBBB",  # < = little-endian, B = uint8_t, H = uint16_t
            0xAA,  # Preamble
            1,  # Frame length // 10
            dev_id,  # Device ID (uint16)
            0x03,  # Command type
            0xDF,  # Command: enter bootloader
            0,  # Reserved
            0,  # Reserved
            0,  # Reserved
        )
        frame += bytes([getCrc8(frame)])

        time.sleep(0.5)
        for _ in range(5):
            sp.send_data(frame)
            time.sleep(0.2)
    except serial.SerialException as se:
        logger.exception(f"serial error sending enter bootloader command: {se}")
        sp.reconnect(se)
        raise
    except Exception as e:
        logger.exception(f"error sending enter bootloader command: {e}")
        raise
    finally:
        # Restore original settings
        sp.baudrate = orig_baudrate
        sp.parity = orig_parity
        sp.reset_input()
        time.sleep(7)  # Wait for device to enter bootloader
