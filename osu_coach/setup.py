"""The player's hardware setup: tablet area or mouse sensitivity, keyboard type.

Read from osu-coach's config.json, falling back to OpenTabletDriver's settings
for the tablet area. Used to turn aim/tapping findings into concrete setting
changes (e.g. "80x48.5mm -> 85x51.4mm").
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .locate import CACHE_DIR

CONFIG_PATH = CACHE_DIR / "config.json"
OTD_SETTINGS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OpenTabletDriver" / "settings.json"


@dataclass
class Setup:
    device: str | None = None        # "tablet" or "mouse"
    area_w: float | None = None      # tablet area, mm
    area_h: float | None = None
    area_rotation: float | None = None
    display_w: float | None = None   # screen pixels the area maps to
    display_h: float | None = None
    sens: float | None = None        # in-game mouse sensitivity
    dpi: int | None = None
    keyboard: str | None = None      # "rt" (rapid trigger) or "mechanical"
    rt_press: float | None = None    # rapid trigger press / release distance, mm
    rt_release: float | None = None
    actuation: float | None = None   # actuation point, mm (mechanical or rapid trigger)
    source: str = "config"

    def describe(self) -> str:
        parts = []
        if self.device == "tablet":
            area = f" {self.area_w:g}x{self.area_h:g}mm" if self.area_w and self.area_h else ""
            rot = f", rotation {self.area_rotation:g} deg" if self.area_rotation else ""
            parts.append(f"tablet{area}{rot}")
        elif self.device == "mouse":
            extra = ", ".join(x for x in (f"sens {self.sens:g}" if self.sens else "",
                                          f"{self.dpi} DPI" if self.dpi else "") if x)
            parts.append("mouse" + (f" ({extra})" if extra else ""))
        actuation = f"actuation {self.actuation:g}mm" if self.actuation else ""
        if self.keyboard == "rt":
            rt = ", ".join(x for x in (actuation, f"press {self.rt_press:g}mm" if self.rt_press else "",
                                       f"release {self.rt_release:g}mm" if self.rt_release else "") if x)
            parts.append("rapid trigger keyboard" + (f" ({rt})" if rt else ""))
        elif self.keyboard == "mechanical":
            parts.append("mechanical keyboard" + (f" ({actuation})" if actuation else ""))
        if not parts:
            return "unknown (set it with `config`)"
        return "; ".join(parts) + f"  [from {self.source}]"

    def mm_per_osu_px(self) -> float | None:
        """Tablet mm per osu!pixel, assuming the display area is the full game screen."""
        if not (self.area_w and self.display_w and self.display_h):
            return None
        osu_px_per_screen_px = 384 / (0.8 * self.display_h)  # the playfield is 80% of screen height
        return self.area_w / (self.display_w * osu_px_per_screen_px)


def _from_otd() -> Setup | None:
    try:
        data = json.loads(OTD_SETTINGS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    for profile in data.get("Profiles", []):
        abs_ = profile.get("AbsoluteModeSettings") or {}
        tablet, display = abs_.get("Tablet") or {}, abs_.get("Display") or {}
        if tablet.get("Width") and tablet.get("Height"):
            return Setup(device="tablet", area_w=tablet["Width"], area_h=tablet["Height"],
                         area_rotation=tablet.get("Rotation") or 0.0,
                         display_w=display.get("Width"), display_h=display.get("Height"),
                         source=f"OpenTabletDriver ({profile.get('Tablet', 'tablet')})")
    return None


def load_setup() -> Setup:
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    setup = Setup(**{k: v for k, v in saved.items() if k in Setup.__dataclass_fields__})
    # OpenTabletDriver's area is the one in use: it wins over an area saved here (which may be an old one); the
    # saved one is for other drivers
    if setup.device in (None, "tablet"):
        otd = _from_otd()
        if otd:
            for k in ("device", "area_w", "area_h", "area_rotation", "display_w", "display_h"):
                setattr(setup, k, getattr(otd, k))
            setup.source = otd.source + (" + config" if saved else "")
    return setup


def save_setup(setup: Setup):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in asdict(setup).items() if v is not None and k != "source"}
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
