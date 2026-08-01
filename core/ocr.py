"""
OCR Module

Uses RapidOCR (ONNX Runtime) for fast local OCR.

Responsibilities
----------------
- Initialize OCR engine
- Extract text from images
- Return plain text
"""

from __future__ import annotations

import cv2
import numpy as np

from rapidocr_onnxruntime import RapidOCR


class OCR:
    """
    Thin wrapper around RapidOCR.
    """

    def __init__(self, settings: dict, logger):

        self.settings = settings
        self.logger = logger

        self.engine = RapidOCR()

        self.logger.info("RapidOCR initialized.")

    # --------------------------------------------------

    def extract(self, image) -> str:
        """
        Extract text from a screenshot.

        Parameters
        ----------
        image
            numpy.ndarray
            BGR image.

        Returns
        -------
        str
        """

        image = self._prepare(image)

        result, _ = self.engine(image)

        if result is None:
            return ""

        lines = []

        for item in result:

            # RapidOCR returns:
            #
            # [box, text, confidence]
            #

            text = item[1].strip()

            if text:
                lines.append(text)

        return "\n".join(lines)

    # --------------------------------------------------

    def extract_with_metadata(self, image):
        """
        Returns OCR results with confidence and boxes.

        Returns
        -------
        list
        """

        image = self._prepare(image)

        result, _ = self.engine(image)

        if result is None:
            return []

        output = []

        for item in result:

            output.append(
                {
                    "box": item[0],
                    "text": item[1],
                    "confidence": float(item[2]),
                }
            )

        return output

    # --------------------------------------------------

    def extract_blocks(
        self,
        image,
        min_confidence: float = 0.50,
    ):
        """
        Return filtered OCR blocks.
        """

        blocks = self.extract_with_metadata(image)

        filtered = []

        for block in blocks:

            if block["confidence"] >= min_confidence:
                filtered.append(block)

        return filtered

    # --------------------------------------------------

    def _prepare(self, image):
        """
        Basic preprocessing.

        Input:
            BGR numpy image

        Output:
            RGB image
        """

        if image is None:
            raise RuntimeError("OCR received empty image.")

        if len(image.shape) == 2:
            return image

        return cv2.cvtColor(
            image,
            cv2.COLOR_BGR2RGB,
        )