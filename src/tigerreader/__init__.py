"""TigerReader — data acquisition for microplate readers: BioTek FLx800 (fluorescence/luminescence) and
Bio-Rad Model 680 (absorbance).
"""
__version__ = "0.7.2"

from .driver.flx800 import FLx800, InstrumentInfo, ReadResult  # noqa: E402
from .protocol.assay import (GEOMETRIES, AssayDefinition, AssayType, Filter, FilterSet,  # noqa: E402
                             FilterTable, Optics, ReadMode, ReadSeries, SensitivityMode, ShakeMode,
                             ShakeSpeed, WellRect)

__all__ = ["__version__", "FLx800", "InstrumentInfo", "ReadResult", "GEOMETRIES", "AssayDefinition",
           "AssayType", "Filter", "FilterSet", "FilterTable", "Optics", "ReadMode", "ReadSeries",
           "SensitivityMode", "ShakeMode", "ShakeSpeed", "WellRect"]
