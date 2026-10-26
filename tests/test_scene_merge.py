from datetime import UTC, datetime, timedelta

from function.scene import TOMBSTONE_TTL, merge_elements
from tests.helpers import element

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


def ids(elements: list[dict]) -> list[str]:
    return [element["id"] for element in elements]


def by_id(elements: list[dict], id: str) -> dict:
    return next(element for element in elements if element["id"] == id)


def test_adds_new_elements_and_keeps_ones_not_sent():
    stored = [element("a", index="a0")]
    incoming = [element("b", index="a1")]

    merged = merge_elements(stored, incoming, NOW)

    assert ids(merged) == ["a", "b"]


def test_higher_version_wins_in_both_directions():
    stored = [element("a", version=3, color="stored"), element("b", version=1)]
    incoming = [
        element("a", version=2, color="stale"),
        element("b", version=2, color="newer"),
    ]

    merged = merge_elements(stored, incoming, NOW)

    assert by_id(merged, "a")["color"] == "stored"
    assert by_id(merged, "b")["color"] == "newer"


def test_version_tie_goes_to_lower_nonce():
    stored = [
        element("a", nonce=5, side="stored"),
        element("b", nonce=1, side="stored"),
    ]
    incoming = [
        element("a", nonce=2, side="incoming"),
        element("b", nonce=9, side="incoming"),
    ]

    merged = merge_elements(stored, incoming, NOW)

    assert by_id(merged, "a")["side"] == "incoming"
    assert by_id(merged, "b")["side"] == "stored"


def test_deletion_beats_stale_copy():
    stored = [element("a", version=4, deleted=True, updated=NOW_MS)]
    incoming = [element("a", version=3)]

    merged = merge_elements(stored, incoming, NOW)

    assert by_id(merged, "a")["isDeleted"] is True


def test_drops_tombstones_older_than_ttl():
    expired_ms = int((NOW - TOMBSTONE_TTL - timedelta(minutes=1)).timestamp() * 1000)
    stored = [
        element("old", deleted=True, updated=expired_ms),
        element("recent", deleted=True, updated=NOW_MS),
        element("alive", updated=expired_ms),
    ]

    merged = merge_elements(stored, [], NOW)

    assert ids(merged) == ["alive", "recent"]


def test_orders_by_fractional_index_then_id():
    stored = [element("c", index="a2"), element("b", index="a1")]
    incoming = [element("a", index="a1"), element("d", index="Zz")]

    merged = merge_elements(stored, incoming, NOW)

    assert ids(merged) == ["d", "a", "b", "c"]
