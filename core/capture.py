"""
capture.py

Screen capture module.

Responsibilities
----------------
- Capture entire monitor
- Crop configured regions
- Return NumPy image (BGR)
- DPI-aware cropping
"""

from __future__ import annotations

import ctypes

import cv2
import mss
import numpy as np


class ScreenCapture:

    def __init__(self, settings: dict, logger):

        self.settings = settings
        self.logger = logger

        self.monitor_index = settings.capture.monitor

        self.crop_top_cm = (
            settings.capture.crop.top_cm
        )

        self.crop_bottom_cm = (
            settings.capture.crop.bottom_cm
        )

        self.sct = mss.mss()

    # ---------------------------------------------------------

    def capture(self):
        """
        Capture configured monitor.

        Returns
        -------
        numpy.ndarray (BGR)
        """

        monitor = self.sct.monitors[self.monitor_index]

        raw = self.sct.grab(monitor)

        image = np.array(raw)

        image = cv2.cvtColor(
            image,
            cv2.COLOR_BGRA2BGR,
        )

        image = self._crop(image)

        # save the image in the current folder, to see how much is getting  cropped.
        # self.save_debug(image, "debug_cropped.png")
        
        return image

    # ---------------------------------------------------------

    def capture_region(self, left: int, top: int, width: int, height: int):
        """
        Capture a specific screen rectangle.

        Returns
        -------
        numpy.ndarray (BGR)
        """

        rect = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }

        raw = self.sct.grab(rect)

        image = np.array(raw)

        image = cv2.cvtColor(
            image,
            cv2.COLOR_BGRA2BGR,
        )

        return image

    # ---------------------------------------------------------

    def _crop(self, image):

        dpi = self.get_monitor_dpi()

        top_pixels = self.cm_to_pixels(
            self.crop_top_cm,
            dpi,
        )

        bottom_pixels = self.cm_to_pixels(
            self.crop_bottom_cm,
            dpi,
        )

        height = image.shape[0]

        start = top_pixels
        end = height - bottom_pixels

        if end <= start:
            return image

        return image[start:end]

    # ---------------------------------------------------------

    @staticmethod
    def cm_to_pixels(
        cm: float,
        dpi: float,
    ) -> int:

        return int((cm / 2.54) * dpi)

    # ---------------------------------------------------------

    @staticmethod
    def get_monitor_dpi() -> float:
        """
        Returns effective monitor DPI.

        Falls back to 96.
        """

        try:

            user32 = ctypes.windll.user32

            user32.SetProcessDPIAware()

            dc = user32.GetDC(0)

            gdi32 = ctypes.windll.gdi32

            LOGPIXELSX = 88

            dpi = gdi32.GetDeviceCaps(
                dc,
                LOGPIXELSX,
            )

            return float(dpi)

        except Exception:

            return 96.0

    # ---------------------------------------------------------

    def monitor_size(self):

        monitor = self.sct.monitors[self.monitor_index]

        return (
            monitor["width"],
            monitor["height"],
        )

    # ---------------------------------------------------------

    def save_debug(
        self,
        image,
        filename="debug_capture.png",
    ):

        cv2.imwrite(filename, image)

        self.logger.info(
            "Saved debug capture -> %s",
            filename,
        )