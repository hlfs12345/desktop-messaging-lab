import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from check_documentation import check, validate_text


def test_recovered_documentation():
    check()


@pytest.mark.parametrize(
    "text",
    [
        "instala??o",
        "vers?o",
        "texto\ufffd",
        "instala\u00c3\u00a7\u00c3\u00a3o",
        "\u00e2\u20ac\u201d",
    ],
)
def test_literal_corruption_rejected(text):
    with pytest.raises(ValueError):
        validate_text(text)
