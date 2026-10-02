#!/usr/bin/env python3
"""Find out how a LeCroy scope answers VICP, independent of the iyzee driver.

    python scripts/vicp_probe.py 10.0.0.5

Tries each header framing / send style against a fresh connection and prints
how long the first reply byte took (or that nothing came). Standard library
only, so it runs anywhere that can reach port 1861.
"""

from __future__ import annotations

import socket
import struct
import sys
import time

PORT = 1861
WAIT = 20.0  # generous: we want the latency, not a verdict
PAUSE = 2.0  # let the scope drop the previous session


def attempt(ip: str, command: bytes, flags: int, seq: int, split: bool) -> str:
    header = struct.pack("!4BI", flags, 1, seq, 0, len(command))
    with socket.create_connection((ip, PORT), timeout=5) as sock:
        sock.settimeout(WAIT)
        start = time.monotonic()
        if split:  # what the original driver did: header and body as two writes
            sock.sendall(header)
            sock.sendall(command)
        else:  # what VICPTransport does today: one write
            sock.sendall(header + command)
        try:
            reply = sock.recv(8)
        except TimeoutError:
            return f"no reply in {WAIT:g}s"
        return f"{time.monotonic() - start:.2f}s  header={reply.hex()}"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    ip = sys.argv[1]
    cases = [
        ("current driver framing, one write", 0x81, 0, False),
        ("original driver framing, two writes", 0x81, 0, True),
        ("seq=1", 0x81, 1, False),
        ("seq=1 + REMOTE flag", 0xC1, 1, False),
    ]
    for label, flags, seq, split in cases:
        for command in (b"*IDN?", b"TRIG_MODE?"):
            try:
                result = attempt(ip, command, flags, seq, split)
            except OSError as exc:
                result = f"connection failed: {exc}"
            print(f"{label:38} {command.decode():11} -> {result}")
            time.sleep(PAUSE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
