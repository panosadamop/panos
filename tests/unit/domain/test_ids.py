import pytest
from hypothesis import given
from hypothesis import strategies as st

from panos_core.domain.ids import PREFIXES, is_valid_id, new_id


@pytest.mark.parametrize("kind", sorted(PREFIXES))
def test_new_id_has_kind_prefix_and_is_valid(kind: str) -> None:
    value = new_id(kind)
    assert value.startswith(PREFIXES[kind] + "_")
    assert is_valid_id(value, kind)


def test_ids_are_unique() -> None:
    assert len({new_id("job") for _ in range(1000)}) == 1000


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="unknown entity kind"):
        new_id("widget")


def test_id_of_other_kind_is_not_valid_for_kind() -> None:
    assert not is_valid_id(new_id("job"), "project")


@given(st.text(max_size=50))
def test_arbitrary_text_is_not_mistaken_for_an_id(text: str) -> None:
    if is_valid_id(text):
        assert text[3] == "_" and len(text) == 36
