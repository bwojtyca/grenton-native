import pytest

from custom_components.grenton_native.native.mapping import propose


@pytest.mark.parametrize(
    ("grenton_id", "domain"),
    [
        ("CLU221011038->DOU1234", "light"),
        ("CLU221011038->DIM0001", "light"),
        ("CLU221011038->LED0001", "light"),
        ("CLU221011038->DAL0584", "light"),
        ("CLU221011038->ROL7324", "cover"),
        ("CLU221011038->DIN0001", "binary_sensor"),
        ("CLU221011038->SAT0001", "binary_sensor"),
        ("CLU221011038->THE0001", "climate"),
        ("CLU221011038->ONE0001", "sensor"),
        ("CLU221011038->TEM0001", "sensor"),
    ],
)
def test_propose_domain_by_prefix(grenton_id, domain):
    assert propose(grenton_id)["domain"] == domain


def test_propose_defaults_index_zero():
    assert propose("CLU1->DOU9999")["index"] == 0


def test_propose_unknown_prefix_is_sensor():
    result = propose("CLU1->XYZ0001")
    assert result["domain"] == "sensor"
    assert "nieznany" in result["note"]
