import argparse
import re


def expand_numbers(cells_string: str) -> list[int]:
    expanded = set()
    for entry in cells_string.lower().split():
        if "-" in entry:
            parts = entry.split("-")
            if len(parts) != 2:
                raise ValueError
            start, end = int(parts[0]), int(parts[1])
            if start < 0 or end < 0 or start > end:
                raise ValueError
            expanded.update(range(start, end + 1))
        else:
            val = int(entry)
            if val < 0:
                raise ValueError
            expanded.add(val)
    return sorted(list(expanded))


def parse_address_range(value: str) -> dict:
    parts = re.split(r"[-:,]", value.strip())
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Invalid range format: '{value}'. Expected: 'start-end' (e.g., 0x08000000-0x08040000)")
    try:
        start_addr = int(parts[0].strip(), 0)
        end_addr = int(parts[1].strip(), 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid address bounds in range '{value}'.")
    if start_addr > end_addr:
        raise argparse.ArgumentTypeError(f"Invalid range: start (0x{start_addr:X}) > end (0x{end_addr:X}).")
    return {"start": start_addr, "end": end_addr}


def args_get() -> argparse.Namespace:
    default_address = 0x08000000

    # Common parent parser
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--loglvl", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logger threshold level")

    parser = argparse.ArgumentParser(
        description="STM32 UART Mass Programmer Launch Tool",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Stat subcommand
    stat = subparsers.add_parser("stat", parents=[common], help="Print firmware hex statistics and exit")
    stat.add_argument("hexfile", type=str, help="Hex file to inspect")
    stat.add_argument(
        "--flash-size",
        type=lambda x: int(x, 0),
        default=0,
        help="Target MCU total flash size in bytes (for usage %% display)",
    )
    stat.add_argument(
        "--crc-range",
        type=parse_address_range,
        default={"start": 0, "end": 0},
        help="STM32 CRC32 range. Format: start-end (e.g., 0x08000000-0x080E0000). " "Defaults to hex file boundaries aligned to 4-byte words.",
    )

    # Prog subcommand
    prog = subparsers.add_parser("prog", parents=[common], help="Program firmware to target(s)")
    prog.add_argument("hexfile", type=str, help="Hex file to program")
    prog.add_argument(
        "--targets",
        type=expand_numbers,
        required=True,
        default=[],
        help="Target IDs to program. Single and/or ranges (e.g., '1-10 12 25').",
    )
    prog.add_argument(
        "--erase",
        type=expand_numbers,
        default=[],
        help="Pages/Sectors to erase before programming.",
    )
    prog.add_argument(
        "--attempts-erase",
        type=int,
        default=10,
        help="Number of retries for sector erase. Default %(default)s",
    )
    prog.add_argument(
        "--attempts",
        type=int,
        default=10,
        help="Number of retries for any command. Default %(default)s",
    )
    prog.add_argument(
        "--address",
        type=int,
        default=default_address,
        help=f"Application start address. Default 0x{default_address:08X}",
    )
    prog.add_argument(
        "--flash-size",
        type=lambda x: int(x, 0),
        default=0,
        help="Target MCU total flash size in bytes (for usage %% display)",
    )
    prog.add_argument(
        "--crc-range",
        type=parse_address_range,
        default={"start": 0, "end": 0},
        help="STM32 CRC32 range. Format: start-end (e.g., 0x08000000-0x080E0000). " "Defaults to hex file boundaries aligned to 4-byte words.",
    )
    prog.add_argument(
        "--baudrate",
        type=int,
        default=57600,
        help="UART baudrate. Default %(default)s",
    )
    prog.add_argument(
        "--no-tune",
        action="store_true",
        help="Disable baudrate autotune",
    )
    prog.add_argument(
        "--tune-threshold",
        type=float,
        default=0.8,
        help="Baudrate autotune success threshold (0.0-1.0). Default %(default)s",
    )
    return parser.parse_args()


args = args_get()
