from base import CH, IP, BaseDevice


class PSU(BaseDevice):
    """
    This class represents the ROHDE&SCHWARZ power supply HMP4040.

    Manual can be downloaded here:
    https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/pdm/cl_manuals/user_manual/1178_6833_01/HMPSeries_UserManual_en_05.pdf
    """

    def open(self):
        self.port: int = 5025
        constr = f"TCPIP::{self.ip}::{self.port}::SOCKET"
        print(constr)

        return self.rm.open_resource(constr)

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
        self.instrument.write("OUTP:GEN 1")


class ShutterControl:
    def __init__(self, chan=CH.THREE, ip=IP.POWER_SUPPLY):
        psu = PSU(ip=ip)

        # shutter needs at least ~2V to trigger
        psu.set_voltage(1.7, chan)
        psu.set_current(0.01, chan)
        self.psu = psu
        self.chan = chan

    def open(self):
        self.psu.enable_output(self.chan)

    def close(self):
        self.psu.disable_output(self.chan)
