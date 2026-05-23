"""Shared ArUco detector configuration for calibration and pose runtimes."""

from __future__ import annotations

try:
    import cv2

    CV2_AVAILABLE = True
except Exception:
    cv2 = None
    CV2_AVAILABLE = False

if CV2_AVAILABLE:
    ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_50)
    ARUCO_PARAMS = cv2.aruco.DetectorParameters()
else:
    ARUCO_DICT = None
    ARUCO_PARAMS = None

# Historical name used in calibration_algorithms.
ARUCO_PARAM = ARUCO_PARAMS
