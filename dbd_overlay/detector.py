from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
from queue import Empty, Queue
import threading
import time
from typing import Callable

from PIL import Image, ImageChops, ImageStat

from .config import AppConfig
from .focus import FocusGate
from .maps import MapAsset, MapLibrary
from .ocr_region import active_ocr_region

try:
    import mss
except Exception:  # pragma: no cover - optional dependency
    mss = None

try:
    import pytesseract
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None


@dataclass
class DetectionResult:
    map_name: str
    confidence: float
    source: str
    raw_text: str = ""


def _clean(value: str) -> str:
    value = value.replace("|", "I")
    return " ".join("".join(ch.lower() for ch in value if ch.isalnum() or ch.isspace()).split())


class DetectionWorker:
    def __init__(
        self,
        config: AppConfig,
        library: MapLibrary,
        focus_gate: FocusGate,
        logger: logging.Logger,
        on_detected: Callable[[DetectionResult], None],
    ) -> None:
        self.config = config
        self.library = library
        self.focus_gate = focus_gate
        self.logger = logger
        self.on_detected = on_detected
        self.results: Queue[DetectionResult] = Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_name = ""
        self._missing_ocr_warned = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="OCRDetectionWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def poll(self) -> DetectionResult | None:
        try:
            return self.results.get_nowait()
        except Empty:
            return None

    def test_once(self) -> DetectionResult | None:
        if not mss:
            self.logger.warning("mss is not installed; OCR test unavailable")
            return None
        screenshot = self._capture_region()
        if screenshot is None:
            return None
        return self._detect_from_image(screenshot)

    def _run(self) -> None:
        if not mss:
            self.logger.warning("mss is not installed; automatic detection disabled")
            return
        self.logger.info("Detection worker started")
        while not self._stop.is_set():
            interval = max(100, self.config.detection.scan_interval_ms) / 1000
            if not self.config.detection.enabled:
                time.sleep(interval)
                continue
            if not self.focus_gate.is_game_focused():
                time.sleep(max(interval, 0.5))
                continue
            screenshot = self._capture_region()
            if screenshot:
                result = self._detect_from_image(screenshot)
                if result and result.map_name != self._last_name:
                    self._last_name = result.map_name
                    self.results.put(result)
            time.sleep(interval if not self.config.detection.performance_mode else max(interval, 0.35))
        self.logger.info("Detection worker stopped")

    def _capture_region(self) -> Image.Image | None:
        left, top, width, height = active_ocr_region(self.config)
        try:
            with mss.mss() as sct:
                raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception as exc:
            self.logger.debug("Screen capture failed: %s", exc)
            return None

    def _detect_from_image(self, image: Image.Image) -> DetectionResult | None:
        result = self._ocr_match(image)
        if result:
            return result
        if self.config.detection.fallback_template_matching:
            return self._template_match(image)
        return None

    def _ocr_match(self, image: Image.Image) -> DetectionResult | None:
        if not pytesseract:
            if not self._missing_ocr_warned:
                self.logger.warning("pytesseract is not installed; OCR unavailable")
                self._missing_ocr_warned = True
            return None
        if self.config.detection.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.detection.tesseract_cmd

        try:
            prepared = image.convert("L")
            raw_text = pytesseract.image_to_string(prepared, config="--psm 6").strip()
        except Exception as exc:
            self.logger.debug("OCR failed: %s", exc)
            return None

        cleaned = _clean(raw_text)
        if not cleaned:
            return None

        best_name = ""
        best_score = 0.0
        for asset in self.library.all_assets():
            candidates = asset.aliases or [asset.name]
            for candidate in candidates:
                score = SequenceMatcher(None, cleaned, _clean(candidate)).ratio()
                if _clean(candidate) in cleaned:
                    score = max(score, 0.95)
                if score > best_score:
                    best_name = asset.name
                    best_score = score

        if best_score >= self.config.detection.confidence_threshold:
            self.logger.info("OCR detected %s (%.0f%%) from '%s'", best_name, best_score * 100, raw_text)
            return DetectionResult(best_name, best_score, "ocr", raw_text)
        self.logger.debug("OCR text below threshold: '%s' (%.0f%%)", raw_text, best_score * 100)
        return None

    def _template_match(self, image: Image.Image) -> DetectionResult | None:
        best_asset: MapAsset | None = None
        best_score = 0.0
        probe = image.resize((96, 96)).convert("L")
        for asset in self.library.all_assets():
            try:
                candidate = Image.open(asset.path).resize((96, 96)).convert("L")
                diff = ImageChops.difference(probe, candidate)
                stat = ImageStat.Stat(diff)
                score = 1.0 - (stat.mean[0] / 255.0)
            except Exception:
                continue
            if score > best_score:
                best_asset = asset
                best_score = score

        if best_asset and best_score >= self.config.detection.template_threshold:
            self.logger.info("Template matched %s (%.0f%%)", best_asset.name, best_score * 100)
            return DetectionResult(best_asset.name, best_score, "template")
        return None
