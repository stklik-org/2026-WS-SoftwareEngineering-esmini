import pytest

from app.core.errors import ApiError
from app.core.files import InputFile, check_name, validate_input_files

SCENARIO = b'<?xml version="1.0"?><OpenSCENARIO><FileHeader/></OpenSCENARIO>'
ROAD = b'<?xml version="1.0"?><OpenDRIVE><header/></OpenDRIVE>'


def code_of(files: list[InputFile], scenario: str = "s.xosc", limit: int = 1_000_000) -> tuple[int, str]:
    with pytest.raises(ApiError) as info:
        validate_input_files(files, scenario, limit)
    return info.value.status_code, info.value.code


@pytest.mark.parametrize("name", ["cut-in.xosc", "Cut_In 2 (copy).xosc", "a+b.xosc", "0.xosc", "_x.xosc"])
def test_accepted_names(name: str) -> None:
    check_name(name)


@pytest.mark.parametrize(
    "name",
    ["", "../x.xosc", "a/b.xosc", "a\\b.xosc", ".x.xosc", "..", "x.xosc ", "x.", "x;rm.xosc", "ä.xosc", "a" * 129],
)
def test_rejected_names(name: str) -> None:
    with pytest.raises(ApiError) as info:
        check_name(name)
    assert info.value.code == "invalid_file_name"


def test_valid_set_passes() -> None:
    validate_input_files([InputFile("s.xosc", SCENARIO), InputFile("r.xodr", ROAD)], "s.xosc", 1000)


def test_at_least_one_file_is_required() -> None:
    assert code_of([]) == (422, "validation_error")


def test_scenario_must_be_among_the_files() -> None:
    assert code_of([InputFile("s.xosc", SCENARIO)], scenario="other.xosc") == (422, "scenario_not_found")
    assert code_of([InputFile("s.xosc", SCENARIO)], scenario="  ") == (422, "validation_error")


def test_scenario_must_be_an_xosc_file() -> None:
    files = [InputFile("s.xosc", SCENARIO), InputFile("r.xodr", ROAD)]
    assert code_of(files, scenario="r.xodr") == (415, "unsupported_file_type")


def test_xodr_must_be_opendrive() -> None:
    assert code_of([InputFile("s.xosc", SCENARIO), InputFile("r.xodr", SCENARIO)]) == (
        422,
        "unexpected_root_element",
    )


def test_scenario_root_may_carry_a_namespace() -> None:
    namespaced = b'<OpenSCENARIO xmlns="http://example.org/osc"><FileHeader/></OpenSCENARIO>'
    validate_input_files([InputFile("s.xosc", namespaced)], "s.xosc", 1000)


def test_entity_expansion_is_forbidden() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><OpenSCENARIO>&a;</OpenSCENARIO>'
    assert code_of([InputFile("s.xosc", bomb)]) == (422, "forbidden_xml")


def test_external_entity_is_forbidden() -> None:
    xxe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><OpenSCENARIO>&e;</OpenSCENARIO>'
    assert code_of([InputFile("s.xosc", xxe)]) == (422, "forbidden_xml")


def test_empty_file() -> None:
    assert code_of([InputFile("s.xosc", b"  \n")]) == (422, "empty_file")


def test_any_file_type_is_accepted() -> None:
    """Models, textures and other data: esmini only reads them, so no extension allow-list."""
    files = [
        InputFile("s.xosc", SCENARIO),
        InputFile("car_white.osgb", b"\x00\x01binary model"),
        InputFile("asphalt.jpg", b"\xff\xd8\xff\xe0"),
        InputFile("guide-post.png.001.dds", b"DDS "),
        InputFile("README", b"no extension"),
    ]
    validate_input_files(files, "s.xosc", 1000)


def test_sumo_configuration_is_checked_as_xml() -> None:
    assert code_of([InputFile("s.xosc", SCENARIO), InputFile("x.sumocfg", b"<configuration>")]) == (422, "invalid_xml")


def test_extensions_are_case_insensitive() -> None:
    validate_input_files([InputFile("s.xosc", SCENARIO), InputFile("R.XODR", ROAD)], "s.xosc", 1000)


def test_size_limit() -> None:
    assert code_of([InputFile("s.xosc", SCENARIO)], limit=10) == (413, "payload_too_large")
