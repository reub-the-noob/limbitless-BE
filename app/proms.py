"""PROM instrument scoring and clinical-threshold flagging (Section 5.4).

Each instrument declares where its primary numeric value sits in the
``responses`` blob, its valid range, and the threshold that flags a
result for clinician attention. ``evaluate`` returns the score, whether
it is flagged, and why - stored on the row so trends and the dashboard
can query them directly.
"""

from dataclasses import dataclass

from app.models import PromInstrument


@dataclass(frozen=True)
class InstrumentSpec:
    score_key: str
    min_score: float
    max_score: float
    flag_high: float | None = None
    flag_low: float | None = None
    flag_reason_high: str = ""
    flag_reason_low: str = ""
    # Which involvement kinds this instrument is meaningful for. The API
    # doesn't restrict what a clinician can record; the frontend uses it
    # to group / order the instrument picker.
    applies_to: tuple[str, ...] = ("amputation", "orthotic")


SPECS: dict[PromInstrument, InstrumentSpec] = {
    PromInstrument.pain_residual_limb: InstrumentSpec(
        "score", 0, 10, flag_high=7,
        flag_reason_high="Residual limb pain 7+/10",
        applies_to=("amputation",),
    ),
    PromInstrument.pain_phantom: InstrumentSpec(
        "score", 0, 10, flag_high=7, flag_reason_high="Phantom pain 7+/10",
        applies_to=("amputation",),
    ),
    PromInstrument.socket_comfort_score: InstrumentSpec(
        "score", 0, 10, flag_low=4,
        flag_reason_low="Socket Comfort Score 4 or below out of 10",
        applies_to=("amputation",),
    ),
    PromInstrument.locomotor_capabilities_index: InstrumentSpec(
        "score", 0, 56, flag_low=21,
        flag_reason_low="LCI-5 21 or below out of 56 (limited mobility)",
    ),
    PromInstrument.orthosis_comfort_score: InstrumentSpec(
        "score", 0, 10, flag_low=4,
        flag_reason_low="Orthosis Comfort Score 4 or below out of 10",
        applies_to=("orthotic",),
    ),
    PromInstrument.quest_satisfaction: InstrumentSpec(
        "score", 1, 5, flag_low=3,
        flag_reason_low="QUEST satisfaction 3 or below out of 5",
    ),
}


# Involvement kind (``InvolvementKind`` value) -> the ``applies_to`` tag.
_KIND_TAG = {
    "amputation": "amputation",
    "congenital_absence": "amputation",  # scored like an amputation
    "orthotic_need": "orthotic",
}


def instruments_for(kind: str) -> list[PromInstrument]:
    """Instruments meaningful for an involvement ``kind`` (an
    ``InvolvementKind`` value), in enum order."""
    tag = _KIND_TAG.get(kind, kind)
    return [
        instrument
        for instrument, spec in SPECS.items()
        if tag in spec.applies_to
    ]


def evaluate(
    instrument: PromInstrument, responses: dict
) -> tuple[float | None, bool, str | None]:
    """Return ``(score, flagged, flag_reason)`` for a set of responses.

    Raises ``ValueError`` if the score is present but out of range.
    """
    spec = SPECS[instrument]
    raw = responses.get(spec.score_key)
    if raw is None:
        return None, False, None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{spec.score_key}' must be a number")
    if not spec.min_score <= score <= spec.max_score:
        raise ValueError(
            f"'{spec.score_key}' must be between {spec.min_score:g} and "
            f"{spec.max_score:g}"
        )
    if spec.flag_high is not None and score >= spec.flag_high:
        return score, True, spec.flag_reason_high
    if spec.flag_low is not None and score <= spec.flag_low:
        return score, True, spec.flag_reason_low
    return score, False, None
