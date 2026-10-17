"""The memory check run before an analysis starts."""

import pytest

from varda.common.memory import (
    MemoryFit,
    assessMemory,
    formatBytes,
    memoryWarning,
)

GIB = 2**30


@pytest.mark.parametrize(
    ("estimate", "expected"),
    [
        (1 * GIB, MemoryFit.FITS),
        (7 * GIB, MemoryFit.TIGHT),  # over 80% of the 8 GiB free
        (12 * GIB, MemoryFit.TIGHT),  # more than is free, less than installed
        (17 * GIB, MemoryFit.EXCEEDS_TOTAL),  # more than the machine has
    ],
)
def test_an_estimate_is_judged_against_free_and_installed_memory(estimate, expected):
    assert assessMemory(estimate, available=8 * GIB, total=16 * GIB) is expected


def test_bytes_read_as_people_say_them():
    assert formatBytes(1536 * 2**20) == "1.5 GB"
    assert formatBytes(300 * 2**20) == "300 MB"


def test_the_warning_names_the_numbers_and_what_to_do():
    title, text = memoryWarning(
        "FATT Detection Map", 12 * GIB, available=8 * GIB, total=16 * GIB
    )
    assert "FATT Detection Map" in title
    assert "12 GB" in text and "8 GB" in text
    assert "crop" in text.lower() or "subset" in text.lower()


def test_the_warning_for_more_than_installed_says_it_may_crash():
    _, text = memoryWarning("Big Thing", 20 * GIB, available=8 * GIB, total=16 * GIB)
    assert "16 GB" in text and "crash" in text.lower()
