import os
from datetime import timedelta

from tqdm import tqdm

from stm32_uart_prog.arg_parser import args_get
from stm32_uart_prog.bootloader import *
from stm32_uart_prog.context import be_quiet, enter_bootloader


def proposal_to_continue(proposal: str, interrupted: str, continued: str = " "):
    try:
        while True:
            response = input(f"\n{proposal}\n").lower()
            if response in ("yes", "no"):
                break
        if response != "yes":
            print(f"{interrupted}")
            return 0
        else:
            if continued:
                print(continued)
            return 1
    except KeyboardInterrupt:
        print(f"{RED}KeyboardInterrupt detected{RESET}")
        print(interrupted)
        return 0


def format_duration(seconds: float):
    td = timedelta(seconds=seconds)
    ms = int(td.microseconds / 1000)
    d, rem = divmod(td.total_seconds(), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []

    if d:
        parts.append(f"{int(d)}d")
    if h or parts:
        parts.append(f"{int(h)}h")
    if m or parts:
        parts.append(f"{int(m)}min")
    if s or parts:
        parts.append(f"{int(s)}s")
    parts.append(f"{ms}ms")
    return "-".join(parts)


def program_hex(bl: STM32BL, target_id: int, total_bar: tqdm):
    """Program hex into target MCU with separate program/verify attempts and conditional sector erase"""
    if not bl:
        raise ValueError("No bootloader instance provided")

    warn_detected = False

    for sector in bl.used_sectors:
        total_bar.set_postfix(id=target_id, sector=f"{sector+1}/{len(bl.used_sectors)}")
        start, size = bl.FLASH_SECTORS[sector]
        chunks = size // bl.CHUNK

        for erase_attempt in range(bl.attempts_erase):
            time.sleep(0.1)
            bl.ser.reset_input()
            bl.ser.reset_input_buffer()
            bl.ser.reset_output_buffer()
            if not bl.erase_sector(sector):
                warn_detected = True
                logger.warning(f"sector {sector}: erase attempt {erase_attempt + 1} failed")
                total_bar.write(f"\tRetry sector {sector}, erase attempt {erase_attempt + 1}/{bl.attempts_erase}")
                continue

            all_ok = True
            credited = 0  # Chunks credited to total_bar in THIS attempt
            for i in range(chunks):
                offset = i * bl.CHUNK
                chunk_start = start + offset
                chunk = bytes(bl.data[chunk_start - bl.min_addr : chunk_start - bl.min_addr + bl.CHUNK])
                if all(b == 0xFF for b in chunk):
                    total_bar.update(1)
                    credited += 1
                    continue

                # Program
                for attempt in range(bl.attempts_cmd):
                    if bl.write_mem(chunk_start, chunk):
                        break
                    logger.warning(
                        f"sector {sector}: write failed ({attempt + 1}/{bl.attempts_cmd}) at 0x{chunk_start:08X}"
                    )
                    if not bl.probe_bootloader():
                        bl.ser.send_data(bl.ACTIVATE.to_bytes())
                        bl._read_ack()
                    time.sleep(0.1)
                else:
                    total_bar.write(f"\t{YELLOW}Sector {sector}: write failed at 0x{chunk_start:08X}{RESET}")
                    warn_detected = True
                    all_ok = False
                    break

                # Verify
                for attempt in range(bl.attempts_cmd):
                    if bl.read_mem(chunk_start, len(chunk)) == chunk:
                        total_bar.update(1)
                        credited += 1
                        break
                    logger.warning(
                        f"sector {sector}: verify failed ({attempt + 1}/{bl.attempts_cmd}) at 0x{chunk_start:08X}"
                    )
                    time.sleep(0.1)
                else:
                    logger.warning(f"sector {sector}: verify failed at 0x{chunk_start:08X}")
                    total_bar.write(f"\t{YELLOW}Sector {sector}: verify failed at 0x{chunk_start:08X}{RESET}")
                    warn_detected = True
                    all_ok = False
                    break

            if all_ok:
                total_bar.write(f"\tSector {BLUE}{sector}{RESET} (0x{start:08X}) {GREEN}verified{RESET}")
                break
            else:
                # Rollback total progress from this failed attempt
                total_bar.update(-credited)
                logger.error(f"sector {sector}: attempt {erase_attempt + 1} failed")
                total_bar.write(
                    f"\t{YELLOW}Retry sector {sector}, attempt {erase_attempt + 1}/{bl.attempts_erase}{RESET}"
                )
        else:
            logger.error(f"sector {sector}: failed permanently after {bl.attempts_erase} erase attempts")
            return "Fail"

    # Start application
    for attempt in range(bl.attempts_cmd):
        if bl.start_application(bl.start_address):
            logger.info(f"target {target_id}: application started at 0x{bl.start_address:08X}")
            break
        time.sleep(0.5)
    else:
        logger.error(f"target {target_id}: failed to start application")
        return "Fail"

    return "Success" if not warn_detected else "Warning"


def retry(fn, *, attempts=20, delay=0.5):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            logger.warning(f"Attempt {i+1} failed: {e}")
            time.sleep(delay)


def main():
    start_time = 0.0
    args = args_get()
    Loggers.set_level(logger, args.loglvl)
    hexfile = os.path.abspath(args.hexfile)
    targets = tuple(args.targets)
    prog_status = {id: "Undefined" for id in targets}
    STM32BL.attempts_erase = args.attempts_erase
    STM32BL.attempts_cmd = args.attempts

    try:
        # Baudrate check
        if args.baudrate not in STM32BL.BAUDRATES:
            if not proposal_to_continue(
                f"{YELLOW}Baudrate {args.baudrate} does not fit into {STM32BL.BAUDRATES}\nContinue? (yes/no){RESET}",
                "Provide proper baudrate",
            ):
                raise InterruptedError
            f"baudrate {args.baudrate} is not supported, try one of these {STM32BL.BAUDRATES}"
        STM32BL.initial_baudrate = args.baudrate

        # Basic hexfile check
        if not hexfile.endswith(".hex"):
            raise RuntimeError(f"only .hex files are supported")
        if not os.path.isfile(hexfile):
            raise RuntimeError(f"hexfile '{hexfile}' not found")

        # Set the connection up
        ports = SerialPort.get_ports()
        if not ports:
            print("No ports found")
            exit(1)
        print("Available ports:")
        for i, (port, desc) in enumerate(ports):
            print(f"\t[{i}] - {port}: {desc}")

        while True:
            user_input = input("Which port to use? ").strip()
            if not user_input.isdigit():
                print("Invalid input: enter a non-negative integer")
                continue
            index = int(user_input)
            if index >= len(ports):
                print(f"Invalid selection: enter a number between 0 and {len(ports) - 1}")
                continue
            port, desc = ports[index]
            break
        sp = SerialPort(port, STM32BL.initial_baudrate, timeout=0.1)  # Open serial port

        bl = STM32BL(
            sp,
            hexfile=hexfile,
        )
        if bl.min_addr != args.address:
            if not proposal_to_continue(
                f"{YELLOW}Non-default application start address detected: from hexfile - {hex(bl.min_addr)}, from args - {hex(args.address)}.\nContinue? (yes/no){RESET}",
                "Check addresses match",
            ):
                raise InterruptedError
        bl.start_address = args.address
        chunks_per_target = sum(bl.FLASH_SECTORS[s][1] // bl.CHUNK for s in bl.used_sectors)
        total_chunks = chunks_per_target * len(targets)
        start_time = time.time()

        with tqdm(
            desc="Tot", total=total_chunks, leave=False, unit="chunk", dynamic_ncols=True, smoothing=0.8, position=1
        ) as total_bar:
            for target_id in targets:
                time.sleep(0.5)
                try:
                    if bl.failed_once:
                        if not proposal_to_continue(
                            f"\n{YELLOW}At least one target programming failed.\nContinue programming target ID {target_id}? (yes/no){RESET}",
                            f"{RED}Programming aborted by user{RESET}",
                        ):
                            raise InterruptedError
                    total_bar.write(f"\nProgramming target ID {BLUE}{target_id}{RESET}")
                    # Send activate bootloader command first, even if not in bootloader mode
                    # This helps to ensure that target will calculate proper baudrate/parity later
                    # If not in bootloader mode, target wont respond
                    if bl.sync(total_bar, target_id, skip_tune=True):
                        total_bar.write("Bootloader already activated")
                    else:
                        # Mute all devices before starting, so they won't interfere with each other
                        retry(lambda: be_quiet(sp, bl.baudrate))
                        total_bar.refresh()

                        # Put target into bootloader mode
                        retry(lambda: enter_bootloader(sp, target_id, bl.baudrate))
                    total_bar.refresh()
                    bl.sync(
                        total_bar,
                        target_id,
                        skip_tune=args.no_tune,
                        success_threshold=args.tune_threshold,
                    )
                    if not args.no_tune:
                        bl.baud_tune(total_bar, 1000, args.tune_threshold)
                    pid = bl.get_pid()
                    if not pid:
                        raise RuntimeError("could not get product id")
                    if int(pid, base=16) not in bl.SUPPORTED_DEVICE_ID:
                        raise NotImplementedError(f"no such device support with PID {pid}")
                    total_bar.refresh()
                    time.sleep(0.1)

                    # Check supported commands
                    sup_commands = bl.get_commands()
                    for cmd_name, cmd in bl.COMMAND_SET.items():
                        if cmd not in sup_commands:
                            raise RuntimeError(f"required bootloader command not supported: {cmd_name} ({hex(cmd)})")
                    logger.info(f"target {target_id}, supported commands: {sup_commands.hex(sep=' ').upper()}")
                    time.sleep(0.1)

                    # Program the target
                    prog_status[target_id] = program_hex(bl, target_id, total_bar)
                    if prog_status[target_id] == "Success":
                        total_bar.write(f"{GREEN}Programming completed successfully{RESET}")
                    elif prog_status[target_id] == "Warning":
                        total_bar.write(f"{YELLOW}Programming completed with warnings{RESET}")
                    else:
                        bl.failed_once = True
                        total_bar.write(f"{RED}Programming failed{RESET}")
                except InterruptedError:
                    raise
                except Exception as e:
                    bl.failed_once = True
                    total_bar.write(f"{RED}Programming failed ({e}){RESET}")
                    prog_status[target_id] = "Fail"
                    logger.exception(f"target ID{target_id}: error during programming - {e}")
    except (KeyboardInterrupt, InterruptedError):
        print(f"\n{YELLOW}Operation cancelled by user{RESET}")
    except serial.SerialException as se:
        print(f"{RED}Could not open serial port: {se}{RESET}")
        logger.exception(f"{se}")
    except IntelHexError as ihe:
        print(f"{RED}Error while parsing hexfile: {ihe}{RESET}")
        logger.exception(f"{ihe}")
    except RuntimeError as re:
        print(f"{RED}Runtime error: {re}{RESET}")
        logger.exception(f"{re}")
    except Exception as e:
        print(f"{RED}An unhandled exception has occurred: {e}{RESET}")
        logger.exception(f"{e}")
    finally:
        if start_time:
            print("\nProgramming summary:")
            for id, status in prog_status.items():
                color = GREEN if status == "Success" else YELLOW if status == "Warning" else RED
                print(f"\tDevice ID {BLUE}{id}{RESET}: {color}{status.lower()}{RESET}")
            duration_str = format_duration(time.time() - start_time) if start_time else "N/A"
            print(f"\tProcess duration: {duration_str}")
            logger.info("programming session ended, time taken: " + duration_str)
            logger.info(f"final statuses: {prog_status}")


if __name__ == "__main__":
    main()
