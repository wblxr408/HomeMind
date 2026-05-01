"""Security module: autonomy manager, injection detector."""

from core.sec.autonomy_manager import AutonomyManager, DeviceAutonomyLevel
from core.sec.injection_detector import InjectionDetector, InjectionCheckResult

__all__ = [
    "AutonomyManager",
    "DeviceAutonomyLevel",
    "InjectionDetector",
    "InjectionCheckResult",
]

