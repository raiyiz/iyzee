from iyzee import CH, IP, BaseDevice


class PSU(BaseDevice):
    """Rohde & Schwarz HMP4040 power-supply controller."""

    def open(self):
        port = 5025
        return self.rm.open_resource(f"TCPIP::{self.ip}::{port}::SOCKET")

    def set_voltage(self, voltage: float, channel: CH):
        self.instrument.write(f"INST:NSEL {channel}")
        self.instrument.write(f"VOLT {voltage}")

    def enable_output(self, channel: CH):
        self.instrument.write(f"INST OUT{channel}")
        self.instrument.write("OUTP:SEL 1")

    def disable_output(self, channel: CH):
        self.instrument.write(f"INST OUT{channel}")
        self.instrument.write("OUTP:SEL 0")

    def set_current(self, current: float, channel: CH):
        self.instrument.write(f"INST:NSEL {channel}")
        self.instrument.write(f"CURR {current}")

    def enable_global_output(self):
        self.instrument.write("OUTP:GEN 1")

    def disable_global_output(self):
        self.instrument.write("OUTP:GEN 0")


class ShutterControl:
    """Control the optical shutter connected to a PSU channel."""

    def __init__(self, chan: CH = CH.THREE, ip: IP = IP.POWER_SUPPLY):
        self.psu = PSU(ip=ip)
        self.chan = chan

        # Shutter trigger voltage is 1.7 V.
        self.psu.set_voltage(1.7, chan)
        self.psu.set_current(0.01, chan)

    def __enter__(self):
        """Return the shutter controller for use in a managed context."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the shutter and release the underlying PSU connection."""
        try:
            self.close()
        finally:
            self.psu.close()
        return False

    def open(self):
        self.psu.enable_output(self.chan)

    def close(self):
        self.psu.disable_output(self.chan)
