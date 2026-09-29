"""Owned browser driver (P2), ported from the vendored browser package (0.1.5+aa6, MIT; see LICENSE).

CDP transport + tab management + the aa2–aa6 observer + a guarded op executor, with the never-submit
rule enforced in the press path. Only `assistant.browser` imports this package.
"""

from .cdp import (
    Cdp,
    CdpError,
    ChromeLaunchError,
    DriverError,
    DriverTimeout,
    attach_chrome,
)
from .observe import Element, Observation
from .session import (
    HELPER_VERSION,
    BrowserManager,
    ClickRefused,
    PageStale,
    Session,
    Settings,
)

__all__ = [
    "Cdp", "CdpError", "ChromeLaunchError", "DriverError", "DriverTimeout", "attach_chrome",
    "Element", "Observation",
    "BrowserManager", "Session", "Settings", "PageStale", "ClickRefused", "HELPER_VERSION",
]
