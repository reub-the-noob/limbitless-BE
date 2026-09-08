import pytest

from app.models import PromInstrument
from app.proms import evaluate, instruments_for


def test_high_residual_limb_pain_is_flagged() -> None:
    score, flagged, reason = evaluate(
        PromInstrument.pain_residual_limb, {"score": 8}
    )
    assert (score, flagged) == (8.0, True)
    assert reason and "pain" in reason.lower()


def test_moderate_pain_is_not_flagged() -> None:
    assert evaluate(PromInstrument.pain_phantom, {"score": 5}) == (5.0, False, None)


def test_low_socket_comfort_is_flagged() -> None:
    score, flagged, reason = evaluate(
        PromInstrument.socket_comfort_score, {"score": 3}
    )
    assert (score, flagged) == (3.0, True)
    assert reason


def test_good_socket_comfort_is_not_flagged() -> None:
    assert evaluate(PromInstrument.socket_comfort_score, {"score": 9})[1] is False


def test_low_lci_is_flagged() -> None:
    assert (
        evaluate(PromInstrument.locomotor_capabilities_index, {"score": 15})[1]
        is True
    )


def test_thresholds_are_inclusive() -> None:
    assert evaluate(PromInstrument.pain_residual_limb, {"score": 7})[1] is True
    assert evaluate(PromInstrument.pain_residual_limb, {"score": 6.9})[1] is False
    assert evaluate(PromInstrument.socket_comfort_score, {"score": 4})[1] is True
    assert evaluate(PromInstrument.socket_comfort_score, {"score": 4.1})[1] is False


def test_out_of_range_scores_raise() -> None:
    with pytest.raises(ValueError):
        evaluate(PromInstrument.pain_residual_limb, {"score": 12})
    with pytest.raises(ValueError):
        evaluate(PromInstrument.locomotor_capabilities_index, {"score": -1})


def test_missing_score_is_neutral() -> None:
    assert evaluate(PromInstrument.pain_phantom, {"note": "declined"}) == (
        None,
        False,
        None,
    )


def test_non_numeric_score_raises() -> None:
    with pytest.raises(ValueError):
        evaluate(PromInstrument.pain_phantom, {"score": "high"})


# --- orthotic instruments --------------------------------------------


def test_low_orthosis_comfort_is_flagged() -> None:
    score, flagged, reason = evaluate(
        PromInstrument.orthosis_comfort_score, {"score": 3}
    )
    assert (score, flagged) == (3.0, True)
    assert "Orthosis Comfort Score" in reason


def test_quest_satisfaction_range_and_flag() -> None:
    assert evaluate(PromInstrument.quest_satisfaction, {"score": 3})[1] is True
    assert evaluate(PromInstrument.quest_satisfaction, {"score": 4})[1] is False
    with pytest.raises(ValueError):
        evaluate(PromInstrument.quest_satisfaction, {"score": 0})
    with pytest.raises(ValueError):
        evaluate(PromInstrument.quest_satisfaction, {"score": 6})


def test_instruments_for_kind() -> None:
    # keys are InvolvementKind values, not the applies_to tag
    orthotic = instruments_for("orthotic_need")
    assert PromInstrument.orthosis_comfort_score in orthotic
    assert PromInstrument.socket_comfort_score not in orthotic
    assert PromInstrument.locomotor_capabilities_index in orthotic

    amputation = instruments_for("amputation")
    assert PromInstrument.pain_phantom in amputation
    assert PromInstrument.orthosis_comfort_score not in amputation

    # a congenital absence is scored like an amputation
    assert instruments_for("congenital_absence") == amputation
