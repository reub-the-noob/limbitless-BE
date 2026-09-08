"""Default rehab pathway templates (requirements Section 5.3).

A template is an ordered list of :class:`~app.models.MilestoneType` for a
:class:`~app.models.CarePathway`. Applying a template creates the
milestone rows for a patient in order (see the pathway endpoint). Making
the templates themselves editable per practice is a later refinement;
for now they live here.
"""

from app.models import CarePathway, MilestoneType

LOWER_LIMB_PATHWAY: list[MilestoneType] = [
    MilestoneType.pre_prosthetic_assessment,
    MilestoneType.cast_socket_fabrication,
    MilestoneType.initial_fitting_delivery,
    MilestoneType.wear_schedule_desensitization,
    MilestoneType.gait_functional_training,
    MilestoneType.independent_ambulation_adl,
    MilestoneType.community_reintegration_followup,
]

UPPER_LIMB_PATHWAY: list[MilestoneType] = [
    MilestoneType.pre_prosthetic_assessment,
    MilestoneType.cast_socket_fabrication,
    MilestoneType.initial_fitting_delivery,
    MilestoneType.prosthetic_use_training,
    MilestoneType.myoelectric_training,
    MilestoneType.independent_ambulation_adl,
    MilestoneType.community_reintegration_followup,
]

# For patients whose involvement is an orthotic need rather than an
# amputation: no residual-limb / casting-of-a-socket steps, but the
# fitting, wear-in and functional-training arc is the same shape.
ORTHOTIC_PATHWAY: list[MilestoneType] = [
    MilestoneType.orthotic_assessment,
    MilestoneType.orthosis_casting,
    MilestoneType.initial_fitting_delivery,
    MilestoneType.wear_schedule_desensitization,
    MilestoneType.gait_functional_training,
    MilestoneType.independent_ambulation_adl,
    MilestoneType.community_reintegration_followup,
]

TEMPLATES: dict[CarePathway, list[MilestoneType]] = {
    CarePathway.lower_limb: LOWER_LIMB_PATHWAY,
    CarePathway.upper_limb: UPPER_LIMB_PATHWAY,
    CarePathway.orthotic: ORTHOTIC_PATHWAY,
}


def template_for(care_pathway: CarePathway) -> list[MilestoneType] | None:
    return TEMPLATES.get(care_pathway)
