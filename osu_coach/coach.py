"""Ranks what to work on for one play, with two separate scores per category.

- map score: the category's share of what went wrong in this play (0-100)
- habits score: the category's share of the impact of the player's bad habits
  (0-100): where their recent plays go clearly worse than their own average.
  Only habits this play shows count: the map has their content (a stream habit
  is not brought up on a map without streams) and the play has mistakes of
  their category, on their patterns when the habit is about specific ones.
"""

from dataclasses import dataclass, field

from .advice import DENSE_SCREEN, HIGH_AR, STAMINA_HIGH_NPS, Insight, Sample
from .beatmap import SLIDER
from .explain import Episode
from .features import ALT, IRREGULAR, JUMP, STREAM_FAMILY

MIN_CONTENT_OBJECTS = 20     # fewer objects of a kind than this and the map "doesn't contain" it
MIN_CONTENT_SHARE = 0.05     # ...or less than this share of the map
MIN_IRREGULAR_OBJECTS = 10
MIN_STAMINA_OBJECTS = 120    # objects played at STAMINA_HIGH_NPS+ for stamina to matter
MIN_SPECIFIC_OBJECTS = 10    # objects a specific bad habit (e.g. "200+ BPM streams") needs in the map

CATEGORY_NAMES = {
    "streams": "Streams",
    "alt": "Alt",
    "jumps": "Jumps",
    "irregular": "Irregular rhythms",
    "sliders": "Sliders",
    "reading": "Reading",
    "accuracy": "Accuracy",
    "stamina": "Stamina",
    "dt": "DT / high AR",
    "aim": "Other aim",
    "setup": "Setup (area / sensitivity / keys)",
}

# (which objects belong to the category, what to call them, minimum count, needs a share of the map)
_CONTENT = {
    "streams": (lambda s: s.f.pattern in STREAM_FAMILY, "notes in doubles/triples/bursts/streams", MIN_CONTENT_OBJECTS, True),
    "alt": (lambda s: s.f.pattern == ALT, "alt notes", MIN_CONTENT_OBJECTS, True),
    "jumps": (lambda s: s.f.pattern == JUMP, "jumps", MIN_CONTENT_OBJECTS, True),
    "irregular": (lambda s: s.f.pattern == IRREGULAR, "notes on 1/3, 1/6 or odd snaps", MIN_IRREGULAR_OBJECTS, False),
    "sliders": (lambda s: s.r.obj.kind == SLIDER, "sliders", MIN_CONTENT_OBJECTS, True),
    "reading": (lambda s: s.f.visible >= DENSE_SCREEN, f"notes with {DENSE_SCREEN}+ others on screen", MIN_CONTENT_OBJECTS, False),
    "stamina": (lambda s: s.f.load_nps >= STAMINA_HIGH_NPS, f"notes in sustained {STAMINA_HIGH_NPS:g}+ notes/s sections", MIN_STAMINA_OBJECTS, False),
}


@dataclass
class Priority:
    category: str
    map_score: float                 # 0-100
    habits_score: float | None       # 0-100, None when the map lacks this content
    content: str = ""                # how much of this category the map has
    play_rate: float | None = None   # miss (or non-300) rate on this category in this play
    usual_rate: float | None = None  # ...and over recent plays
    rate_label: str = "missed"
    episodes: list[Episode] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)

    @property
    def name(self) -> str:
        return CATEGORY_NAMES.get(self.category, self.category.capitalize())


def _rate(category: str, samples: list[Sample]) -> tuple[float | None, str]:
    if category == "accuracy":
        acc = [s for s in samples if s.acc_eligible]
        return (sum(s.not_300 for s in acc) / len(acc) if acc else None), "100s/50s"
    if category == "sliders":
        held = [s for s in samples if s.r.obj.kind == SLIDER and not s.missed]
        return (sum(s.r.slider_break_kind is not None for s in held) / len(held) if held else None), "broken or dropped"
    if category == "dt":
        members = [s for s in samples if s.ar > HIGH_AR]
    elif category in _CONTENT:
        members = [s for s in samples if _CONTENT[category][0](s)]
    else:
        return None, "missed"
    return (sum(s.missed for s in members) / len(members) if members else None), "missed"


def _content(category: str, play: list[Sample]) -> tuple[bool, str]:
    """Does the map contain enough of this category for its bad habits to be relevant?"""
    if category == "dt":
        ar = play[0].ar if play else 0
        return ar > HIGH_AR, f"AR {ar:.1f}"
    if category not in _CONTENT:
        return True, ""  # accuracy, other aim: every map has them
    pred, label, minimum, needs_share = _CONTENT[category]
    n = sum(map(pred, play))
    enough = n >= minimum and (not needs_share or n >= MIN_CONTENT_SHARE * max(len(play), 1))
    return enough, f"{n} {label}"


def _mistake(s: Sample, kind: str) -> bool:
    """A mistake of the kind a bad habit is about: a 100 or 50 for accuracy habits, a miss or a combo break else."""
    if kind == "accuracy":
        return s.acc_eligible and s.not_300
    return s.missed or s.r.slider_break_kind in ("tick", "repeat")


def prioritize(episodes: list[Episode], insights: list[Insight],
               play: list[Sample], recent: list[Sample]) -> list[Priority]:
    categories = {e.category for e in episodes} | {i.category for i in insights if i.kind != "info"}
    relevant = {c: _content(c, play) for c in categories}
    with_mistakes = {e.category for e in episodes}

    def applies(i: Insight) -> bool:
        """The habit shows in this play: the map has its content, the play has mistakes of its category, and for a
        habit about specific patterns, some of those mistakes are on them."""
        if not relevant[i.category][0] or i.category not in with_mistakes:
            return False
        if i.applies_to is None:
            return True
        return (sum(map(i.applies_to, play)) >= MIN_SPECIFIC_OBJECTS
                and any(i.applies_to(s) and _mistake(s, i.kind) for s in play))

    habits = [i for i in insights if i.kind != "info" and i.impact > 0 and applies(i)]
    play_total = sum(e.cost for e in episodes)
    habits_total = sum(i.impact for i in habits)

    out = []
    for c in categories:
        eps = [e for e in episodes if e.category == c]
        ins = [i for i in habits if i.category == c]
        if not eps and not ins:
            continue  # only a bad habit, and the map doesn't have this content
        play_rate, label = _rate(c, play)
        out.append(Priority(
            category=c,
            map_score=100 * sum(e.cost for e in eps) / play_total if play_total else 0.0,
            habits_score=100 * sum(i.impact for i in ins) / habits_total if relevant[c][0] and habits_total else None,
            content=relevant[c][1],
            play_rate=play_rate,
            usual_rate=_rate(c, recent)[0] if recent else None,
            rate_label=label,
            episodes=sorted(eps, key=lambda e: (-e.cost, e.time)),
            insights=ins,
        ))
    return sorted(out, key=lambda p: (-p.map_score, -(p.habits_score or 0)))
