from app.models import CarePathway, MilestoneType
from app.pathways import ORTHOTIC_PATHWAY, template_for


def test_every_non_other_pathway_has_a_template() -> None:
    for pathway in CarePathway:
        if pathway is CarePathway.other:
            assert template_for(pathway) is None
        else:
            assert template_for(pathway)


def test_orthotic_template_shape() -> None:
    template = template_for(CarePathway.orthotic)
    assert template is ORTHOTIC_PATHWAY
    assert template[0] is MilestoneType.orthotic_assessment
    assert template[1] is MilestoneType.orthosis_casting
    # no amputation-specific steps
    assert MilestoneType.pre_prosthetic_assessment not in template
    assert MilestoneType.cast_socket_fabrication not in template
    # but the shared fitting / function arc is there
    assert MilestoneType.initial_fitting_delivery in template
    assert MilestoneType.community_reintegration_followup == template[-1]
