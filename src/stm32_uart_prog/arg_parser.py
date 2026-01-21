import argparse
from typing import Dict, List, Union, cast

from stm32_uart_prog.loggers import logger


def args_get() -> argparse.Namespace:
    """Parse command line arguments."""
    default_address = 0x08000000

    try:
        parser = argparse.ArgumentParser(description="STM32 UART Mass Programmer Launch Tool", allow_abbrev=False)
        parser.add_argument("--hexfile", type=str, help="Hex file to program", required=True)
        parser.add_argument(
            "--targets",
            type=parse_target_arg,
            nargs="+",
            help="Target IDs to program. Can be single IDs (e.g., 1) and/or ranges (e.g., 1-10). Example: --targets 1-10 7-12 52 19 8-20",
            required=True,
        )
        parser.add_argument(
            "--attempts-erase",
            type=int,
            help="Number of retries for sector erase command. If ran out of erase attempts, programming will be considered failed. Default %(default)s",
            default=10,
        )
        parser.add_argument(
            "--attempts",
            type=int,
            help="Number of retries for any command. If ran out of write/read attempts, sector will be erased and programming retried. Default %(default)s",
            default=10,
        )
        parser.add_argument(
            "--address",
            type=int,
            help=f"The address from which the downloaded application will be executed. Default 0x{default_address:08X}",
            default=default_address,
        )
        parser.add_argument(
            "--baudrate",
            type=int,
            help="UART baudrate. Default %(default)s",
            default=57600,
        )
        parser.add_argument(
            "--no-tune",
            action="store_true",
            help="Disable baudrate autotune",
        )
        parser.add_argument(
            "--tune-threshold",
            type=float,
            help="Baudrate autotune success threshold (0.0-1.0). Default %(default)s",
            default=0.8,
        )
        parser.add_argument(
            "--loglvl",
            type=str,
            choices=["NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            default="ERROR",
            help="Logger threshold level. Default %(default)s",
        )

        args = parser.parse_args()

        # Flatten list of lists
        flat_targets: List[Dict[str, Union[str, int]]] = [item for sublist in args.targets for item in sublist]
        args.targets = parse_targets(flat_targets)
        return args

    except Exception as e:
        logger.exception(f"error parsing arguments: {e}")
        return argparse.Namespace()


def parse_target_arg(s: str) -> List[Dict[str, Union[str, int]]]:
    """Parse target argument(s) which may contain space-separated values."""
    results: List[Dict[str, Union[str, int]]] = []

    for part in s.strip().split():
        try:
            if "-" in part:
                start, end = map(int, part.split("-"))
                if start < 0 or end < 0 or start > end:
                    raise ValueError
                results.append({"type": "range", "start": start, "end": end})
            else:
                single_id = int(part)
                if single_id < 0:
                    raise ValueError
                results.append({"type": "single", "id": single_id})
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid: '{part}', use integer or range like 1 or 1-10")
    return results


def parse_targets(target_args: List[Dict[str, Union[str, int]]]) -> List[int]:
    """Convert parsed target arguments into a sorted list of unique IDs."""
    all_ids: set[int] = set()

    for arg in target_args:
        if arg["type"] == "range":
            # Cast to tell type checker these are definitely ints
            start = cast(int, arg["start"])
            end = cast(int, arg["end"])
            all_ids.update(range(start, end + 1))
        else:  # "single"
            # Cast to tell type checker this is definitely int
            single_id = cast(int, arg["id"])
            all_ids.add(single_id)
    return sorted(all_ids)
