import argparse
from typing import Dict, List, Union, cast

from .loggers import logger


def args_get() -> argparse.Namespace:
    """Parse command line arguments."""
    default_address = 0x08000000

    try:
        parser = argparse.ArgumentParser(description="STM32 UART Mass Programmer Launch Tool", allow_abbrev=False)
        parser.add_argument("--hexfile", type=str, required=True, help="Hex file to program")
        parser.add_argument(
            "--targets",
            nargs="?",
            type=expand_numbers,
            required=True,
            default=[],
            help="Target IDs to program. Can be single IDs (e.g., 1) and/or ranges (e.g., 1-10). Example: --targets 1-10 7-12 52 19 8-20",
        )
        parser.add_argument(
            "--erase",
            nargs="?",
            type=expand_numbers,
            default=[],
            help="Pages/Sectors to erase. Can be single numbers (e.g., 0) and/or ranges (e.g., 0-11). Example: --erase 0-3 4 5-11",
        )
        parser.add_argument(
            "--attempts-erase",
            type=int,
            default=10,
            help="Number of retries for sector erase command. If ran out of erase attempts, programming will be considered failed. Default %(default)s",
        )
        parser.add_argument(
            "--attempts",
            type=int,
            default=10,
            help="Number of retries for any command. If ran out of write/read attempts, sector will be erased and programming retried. Default %(default)s",
        )
        parser.add_argument(
            "--address",
            type=int,
            default=default_address,
            help=f"The address from which the downloaded application will be executed. Default 0x{default_address:08X}",
        )
        parser.add_argument(
            "--crc-range",
            type=lambda x: int(x, 0),
            default=0,
            help="Calculate STM32-like CRC32 for flash range up to this address (in bytes). "
            "If firmware is smaller, remaining bytes are treated as 0xFF. "
            "Defaults to firmware size aligned to 4-byte boundary",
        )
        parser.add_argument(
            "--baudrate",
            type=int,
            default=57600,
            help="UART baudrate. Default %(default)s",
        )
        parser.add_argument(
            "--no-tune",
            action="store_true",
            help="Disable baudrate autotune",
        )
        parser.add_argument(
            "--tune-threshold",
            type=float,
            default=0.8,
            help="Baudrate autotune success threshold (0.0-1.0). Default %(default)s",
        )
        parser.add_argument(
            "--loglvl",
            type=str,
            choices=["NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            default="ERROR",
            help="Logger threshold level. Default %(default)s",
        )

        args = parser.parse_args()
        return args
    except Exception as e:
        logger.exception(f"error parsing arguments: {e}")
        return argparse.Namespace()


def expand_numbers(cells_string: str) -> list[int]:
    expanded = set()
    entries = cells_string.lower().split()

    for entry in entries:
        if "-" in entry:  # Range
            parts = entry.split("-")
            if len(parts) != 2:
                raise ValueError
            start, end = int(parts[0]), int(parts[1])
            if start < 0 or end < 0 or start > end:
                raise ValueError
            expanded.update(range(start, end + 1))
        else:  # Standalone
            val = int(entry)
            if val < 0:
                raise ValueError
            expanded.add(val)
    return sorted(list(expanded))
