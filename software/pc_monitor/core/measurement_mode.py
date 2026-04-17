from enum import Enum


class MeasurementMode(Enum):
    CALIBRATION_LN = "cal_ln"
    MEASURE_DELTA  = "delta"
    MEASURE_WYE    = "wye"
    UNKNOWN        = "unknown"

    @staticmethod
    def from_str(s: str) -> "MeasurementMode":
        for m in MeasurementMode:
            if m.value == s:
                return m
        return MeasurementMode.MEASURE_DELTA   # backward-compat default
