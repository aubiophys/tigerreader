"""FLx800 status / error codes, worded as in the operator's manual.

Texts are verbatim from Appendix C ("Computer Control Status String Format"), which lists the status codes sent over
the serial link; codes or details that only Appendix B ("Troubleshooting & Error Codes") lists use Appendix B's
wording. Look a code up in Appendix B for its probable causes.
"""
from __future__ import annotations

EXACT: dict[str, str] = {
    # ---- Appendix C: fatal errors
    "A100": "task control block not available",
    "A200": "read already in progress",
    "A300": "motor device not available",
    "A301": "logic timer device not available",
    "A302": "flash memory device not available",
    "A303": "display device not available",
    "A304": "printer device not available",
    "A400": "failed code checksum test on power-up",
    "A500": "system power supply failure",
    "A501": "5 volt logic supply voltage level failure detected",
    "A502": "24 volt system supply failure detected",
    "A600": "data flash write timed out",
    "A700": "data flash readback didn't match write",
    "A701": "low byte data flash program error",
    "A702": "high byte data flash program error",
    "A710": "data flash copy error",
    "A800": "code flash write timed out",
    "A900": "memory allocation heap corrupted",
    "AA00": "device never saw ready transition",
    "AA02": "fluorescence measurement A/D not operational",
    "AA03": "incubator thermistor measurement A/D not operational",
    # ---- Appendix C: non-fatal errors
    "0000": "no errors detected",
    "0100": "read function aborted",
    "0101": "Abort error (soft abort)",                                              # Appendix B
    "0200": "X-axis motor position sensor error",
    "0201": "Y-axis motor position sensor error",
    "0202": "Excitation filter wheel motor position sensor error",
    "0203": "Emissions filter wheel motor position sensor error",
    "0204": "Probe switching motor position sensor error",
    "0205": "Syringe motor position sensor error",
    "0300": "X-axis motor autocalibration calibration light beam sensor error",
    "0301": "Y-axis motor autocalibration calibration light beam sensor error",
    "0400": "X-axis motor position verify error",
    "0401": "Y-axis motor position verify error",
    "0402": "Excitation filter wheel motor position verify error",
    "0403": "Emissions filter wheel motor position verify error",
    "0404": "Probe changer failed positional verify",                                # Appendix B
    "0405": "Syringe 0 failed position verify (also known as the first syringe drive)",  # Appendix B
    "0500": "A/D signal saturated error occurred during autocalibration",
    "0501": "A/D signal saturated error occurred while performing high-voltage PMT self-test",
    "0502": "A/D signal saturated error occurred while performing low-voltage PMT self-test",
    "0505": "A/D signal saturated error occurred while measuring microplate well",
    "0508": "PMT measurement saturation (too much light)",                          # Appendix B
    "0600": "Autocalibration light beam signal too low to properly saturate measurement",
    "0601": "Excitation filter position 1 light signal too low",
    "0602": "Excitation filter position 2 light signal too low",
    "0603": "Excitation filter position 3 light signal too low",
    "0604": "Excitation filter position 4 light signal too low",
    "0700": "reader failed noise test",
    "0800": "reader failed offset test",
    "0C00": "printer timed out",
    "0D00": "failed calibration checksum test",
    "0E00": "wavelength not found in table",
    "1000": "necessary configuration data missing",
    "1100": "failed configuration checksum test",
    "1101": "Failed configuration checksum test",                                   # Appendix B
    "1200": "necessary calibration data missing",
    "1300": "X-axis motor not properly homed to sensor",
    "1301": "Y-axis motor not properly homed to sensor",
    "1302": "Excitation filter wheel motor not properly homed to sensor",
    "1303": "Emissions filter wheel motor not properly homed to sensor",
    "1304": "Probe changer not homed successfully",                                 # Appendix B
    "1305": "Syringe 1 not homed successfully",                                     # Appendix B
    "1400": "assay expects incubation when not supported by instrument",
    "1500": "incubator failure",
    "1530": "door open causing zone 3 thermistor error",
    "1540": "Incubator Configuration data failure",                                 # Appendix B
    "1600": "computer control assay definition error",
    "1700": "interval too short for selected options",
    "1800": "too many kinetic intervals",
    "1900": "malloc failed",
    "1A00": "store curve failure",
    "1B00": "Get curve failure",                                                    # Appendix B
    "1C00": "A/D calib STBY transition not detected",
    "1C01": "A/D calibration STBY line went low but never transitioned to a high",  # Appendix B
    "1D00": "results data checksum error",
    "1E00": "error reading time/date clock device",
    "1F00": "bandpass overlap for selected Excitation and Emissions filters",
    "2100": "invalid parameter value selected",
    "2200": "PMT test signal too high",
    "2201": "750v PMT test exceeded defined max limit",
    "2202": "PMT signal too low",                                                   # Appendix B
    "2203": "PMT 750v to 500 v ratio test failed defined min limit",
    "2204": "PMT noise level too high",                                             # Appendix B
    "2205": "PMT saturation at well during read",                                   # Appendix B
    "2300": "lamp control failure",
    "2301": "lamp voltage failure",
    "2302": "lamp current failure",
    "2303": "lamp is on when it should be off",
    "2600": "X-Y Limit error",                                                      # Appendix B
    "2700": "method doesn't match first panel assay",
    "2800": "timer not available",
    "2801": "Y-axis motor device request while motor is in use",
    "2802": "Excitation filter wheel motor device request while motor is in use",
    "2803": "Emissions filter wheel motor device request while motor is in use",
    "2804": "probe changer motor device request while motor is in use",
    "2805": "syringe motor device request while motor is in use",
    "2900": "voltage reference failed",
    "2A00": "didn't see middle sensor",
    "2B00": "syringe runtime errors",
    "2B01": "syringe off sensor when it should be on it",
    "2B02": "syringe on sensor when it should be off it",
    "2B03": "sensor clear count out of range",
    "2B04": "clear count measured during aspirate operation changed from initial value more than allowed range",
    "2B05": "number of steps requested for move too large",
    "2B06": "syringe logical position is not at home",
    "2B07": "invalid position passed to position syringe function",
    "2B08": "invalid syringe operation selected",
    "2B09": "motor profile selected has too many steps for move",
    "2C00": "disp_cnfg_data error",
    "2C01": "disp_cnfg data not set",
    "2C02": "disp_cnfg data checksum failed",
    "2C03": "syringe µl/step factor not within allowed limits",
    "2C04": "invalid injector position or position not set",
    "2C05": "volume cal data is invalid",
    "2C06": "pull back volume too large",
    "2C07": "indicates that default cal volume data set should be updated via the serial host with the cal data "
            "values from the attached syringe drive",
    "2D00": "disp cnfg data error",
    "2D01": "more than two filters selected for disp/read method",
    "2D02": "filter bandpass overlap condition exists in the intermediate switching position for multi filterset reads",
    "2D03": "ex or em filters not adjacent for multi filterset reads",
    "2D04": "filter plug(s) not positioned next to ex filter(s) for light shutter feature",
    "2D05": "invalid dispense/read method received",
    "2D06": "DispReadDef parameters not set or not valid",
    "2D07": "dispense only operation valid for well mode only",
    "2D08": "errcode set when disp/read feature not enabled by dip switch",
    "2D0E": "During a dispense read, the reader missed the start of the plate read",  # Appendix B
    "2D10": "number of sampling windows defined invalid",
    "2D11": "sampling window lag time out of range",
    "2D12": "number of samples out of range",
    "2D13": "sampling interval out of range",
    "2D14": "number of plate mode kinetic intervals out of range",
    "2D15": "plate mode kinetic interval time too short",
    "2D16": "invalid read type receive",
    "2D20": "number of dispensers defined invalid",
    "2D21": "dispense events defined invalid",
    "2D22": "tip prime volume out of range",
    "2D23": "volume requested out of range",
    "2D24": "dispense rate requested out of range",
    "2D25": "dispense start time/kinetic interval out of range",
    "2D26": "dispense kinetic interval out of range",
    "2D27": "time of dispense event overlaps with previous dispense event",
    "2D28": "dispenser module not attached",
    "2D29": "dispenser has not been initialized",
    "2D2A": "dispenser has not been primed",
    "2D2B": "invalid dispenser operation type requested",
    "2F00": "no ACK received for data handshaking",
    "3100": "Plate read took longer than kinetic interval",                          # Appendix B
    "3202": "No fluorescence A/D ready transition",                                  # Appendix B
    "3203": "No incubation A/D ready transition",                                    # Appendix B
    "3204": "No voltage reference channel A/D ready transition",                     # Appendix B
}

# Appendix B: incubator zone encoding (last digit) "Zone 1 = 1, 2 = 2, and 3 = 4"
_ZONES = {"1": "Zone 1", "2": "Zone 2", "3": "Zones 1 and 2", "4": "Zone 3", "5": "Zones 1 and 3",
          "6": "Zones 2 and 3", "7": "Zones 1, 2, and 3"}
_INCUBATOR = {"0": "Incubator zone failed temperature range", "1": "Incubator thermistor failed",
              "2": "Incubator A to D failed"}                                        # Appendix B, 150x/151x/152x


def describe_status(code: str) -> str:
    """The manual's description of a 4-character status code."""
    c = code.upper()
    if c in EXACT:
        return EXACT[c]
    if len(c) != 4:
        return "unknown status"
    head = c[:2]
    if head == "0B":
        return (f"invalid assay selected. Either assay does not exist or number of assays has exceeded limit "
                f"(assay 0x{c[2:]})")
    if head == "0E":
        return f"filter specified for Filterset {c[3]} not available in instrument's filter table"
    if head == "15" and c[2] in _INCUBATOR and c[3] in _ZONES:
        return f"{_INCUBATOR[c[2]]}: {_ZONES[c[3]]}"
    if head == "27":
        return f"method doesn't match first panel assay (assay 0x{c[2:]})"
    if c[0] == "4":
        return f"PMT overload error at [well code] {c[1:]}"
    if c[0] == "A":
        return "fatal error: turn off the instrument (Appendix B, Fatal Errors)"
    return "unknown status"
