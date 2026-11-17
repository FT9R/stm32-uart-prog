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
    return sorted(expanded)


def parse_address_range(value: str) -> dict:
    raw = value.strip()
    if raw == "-":
        return {"start": None, "end": None}

    parts = re.split(r"[-:,]", raw)
    if len(parts) == 1 and parts[0].strip():
        try:
            return {"start": None, "end": int(parts[0].strip(), 0)}
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid address '{value}'.")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Invalid range format: '{value}'. Expected: 'start-end' "
            f"(e.g., 0x08000000-0x080E0000), 'start-*', '*-end', '*', or a bare 'end' address."
        )

    try:
        start = None if parts[0].strip() in ("", "*") else int(parts[0].strip(), 0)
        end = None if parts[1].strip() in ("", "*") else int(parts[1].strip(), 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid address bounds in range '{value}'.")

    if start is not None and end is not None and end - start < 4:
        raise argparse.ArgumentTypeError(
            f"Invalid range: '{value}' spans {end - start} byte(s), need at least 4 " f"(start=0x{start:X}, end=0x{end:X})."
        )
    return {"start": start, "end": end}


CRC_RANGE_HELP = (
    "STM32 CRC32 range. Forms: 'start-end' (0x08000000-0x080E0000), "
    "'start-*' / '*-end' / '*' for hex-derived defaults, or a bare 'end' address. "
    "Must span at least 4 bytes when both bounds are given. "
    "Defaults to hex file boundaries aligned to 4-byte words"
)


def args_get() -> argparse.Namespace:
    default_address = 0x08000000

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--loglvl",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logger threshold level. Default disabled",
    )
    common.add_argument(
        "--flash-size",
        type=lambda x: int(x, 0),
        default=0,
        help="Target MCU total flash size in bytes (for usage %% display)",
    )
    common.add_argument(
        "--crc-range",
        type=parse_address_range,
        default={"start": None, "end": None},
        help=CRC_RANGE_HELP,
    )

    parser = argparse.ArgumentParser(
        description="STM32 UART Mass Programmer Launch Tool",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    stat = subparsers.add_parser("stat", parents=[common], help="Print firmware hex statistics and exit")
    stat.add_argument("hexfile", help="Hex file to inspect")

    prog = subparsers.add_parser("prog", parents=[common], help="Program firmware to target(s)")
    prog.add_argument("hexfile", help="Hex file to program")
    prog.add_argument(
        "--targets",
        type=expand_numbers,
        required=True,
        metavar="RANGE",
        help="Target IDs to program. Single and/or ranges (e.g., '1-10 12 25')",
    )
    prog.add_argument(
        "--erase",
        type=expand_numbers,
        default=[],
        metavar="RANGE",
        help="Pages/Sectors to erase before programming (e.g., '0-5 9')",
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
        type=lambda x: int(x, 0),
        default=default_address,
        help=f"Application start address. Default 0x{default_address:08X}",
    )
    prog.add_argument(
        "--baudrate",
        type=int,
        default=57600,
        help="UART baudrate. Default %(default)s",
    )
    prog.add_argument(
        "--tune-threshold",
        type=float,
        default=0.8,
        help="Baudrate autotune success threshold (0.0[disabled]-1.0). Default %(default)s",
    )
    return parser.parse_args()


args = args_get()
