from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Callable
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


CALLOUTS_URL = "https://hens333.com/callouts"
IMAGE_BASE = "https://hens333.com/img/dbd/callouts/"
USER_AGENT = "DBDCompanionOverlay/0.1 (+local map importer)"
MAP_NAME_CORRECTIONS = {
    "rancid abbatioar": "Rancid Abattoir",
}


@dataclass(frozen=True)
class HensMap:
    realm: str
    names: list[str]
    image: str

    @property
    def display_name(self) -> str:
        return self.names[0]


@dataclass(frozen=True)
class HensImportSummary:
    downloaded: int = 0
    skipped: int = 0
    total: int = 0


def _fetch_text(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_bytes(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "map"


def _name_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _correct_map_names(names: list[str]) -> list[str]:
    corrected: list[str] = []
    for name in names:
        fixed = MAP_NAME_CORRECTIONS.get(_name_key(name), name)
        if fixed not in corrected:
            corrected.append(fixed)
        if name != fixed and name not in corrected:
            corrected.append(name)
    return corrected


def _parse_hens_maps(js: str) -> list[HensMap]:
    pattern = re.compile(r'\{realm:"(?P<realm>.*?)",names:\[(?P<names>.*?)\],image:"(?P<image>.*?)"\}')
    maps: list[HensMap] = []
    seen: set[str] = set()
    for match in pattern.finditer(js):
        image = match.group("image")
        if image in seen:
            continue
        seen.add(image)
        names = _correct_map_names(json.loads(f'[{match.group("names")}]'))
        maps.append(HensMap(realm=match.group("realm"), names=names, image=image))
    return maps


def fetch_manifest() -> list[HensMap]:
    page = _fetch_text(CALLOUTS_URL)
    script_match = re.search(r'href="\./(_app/immutable/nodes/4\.[^"]+\.js)"', page)
    if not script_match:
        script_match = re.search(r'import\("\./(_app/immutable/nodes/4\.[^"]+\.js)"\)', page)
    if not script_match:
        raise RuntimeError("Could not locate Hens callouts page bundle")
    script_url = urljoin(CALLOUTS_URL, script_match.group(1))
    maps = _parse_hens_maps(_fetch_text(script_url))
    if not maps:
        raise RuntimeError("Could not parse Hens callouts map manifest")
    return maps


def import_hens_callouts(
    maps_root: Path,
    logger: logging.Logger,
    progress: Callable[[str], None] | None = None,
    *,
    force: bool = False,
) -> HensImportSummary:
    manifest = fetch_manifest()
    target_root = maps_root / "Hens Callouts"
    target_root.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0
    for item in manifest:
        source_url = urljoin(IMAGE_BASE, quote(item.image, safe="/"))
        suffix = Path(item.image).suffix or ".webp"
        target_dir = target_root / _safe_filename(item.realm)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{_safe_filename(item.display_name)}{suffix}"
        metadata = target.with_name(f"{target.name}.json")
        cached = target.exists() and metadata.exists() and not force

        if cached:
            skipped += 1
            if progress:
                progress(f"Cached {item.display_name}")
        else:
            if progress:
                progress(f"Downloading {item.display_name}")
            data = _fetch_bytes(source_url)
            target.write_bytes(data)
            downloaded += 1
            logger.info("Downloaded Hens callout map: %s", item.display_name)

        metadata.write_text(
            json.dumps(
                {
                    "name": item.display_name,
                    "aliases": item.names,
                    "realm": item.realm,
                    "source": CALLOUTS_URL,
                    "source_image": item.image,
                    "source_url": source_url,
                    "credit": "Images made by Lethia; Hens callouts page modified by Zexov from the original build by Broosley and Evo.",
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    cache_path = target_root / "hens_manifest_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "source": CALLOUTS_URL,
                "image_base": IMAGE_BASE,
                "maps": [
                    {"realm": item.realm, "names": item.names, "image": item.image}
                    for item in manifest
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return HensImportSummary(downloaded=downloaded, skipped=skipped, total=len(manifest))


def main() -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger("hens_callouts_import")
    summary = import_hens_callouts(Path.cwd() / "Maps", logger, lambda message: logger.info(message))
    logger.info(
        "Hens callout cache update complete: %s downloaded, %s cached, %s total",
        summary.downloaded,
        summary.skipped,
        summary.total,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
