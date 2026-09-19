from datetime import date

import pytest

from sgcorpus import urn as u


@pytest.mark.parametrize(
    "value",
    [
        "urn:sg:act:PC1871",
        "urn:sg:act:PC1871:s300",
        "urn:sg:act:CA1967:s157A@2019-04-01",
        "urn:sg:sl:CA1967:S329-2015",
        "urn:sg:judgment:2024_SGCA_15",
        "urn:sg:judgment:2024_SGCA_15:para42",
        "urn:sg:pdpc:2024_SGPDPC_3",
        "urn:sg:hansard:2024-02-06:s3:sp12",
        "urn:sg:bill:2012:24",
    ],
)
def test_round_trip(value: str) -> None:
    assert str(u.parse(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        "urn:sg:statute:PC1871",          # unknown corpus
        "urn:sg:act:",                     # empty work
        "urn:sg:act:PC 1871",              # whitespace in a segment
        "urn:sg:judgment:2024_SGCA_15@2024-01-01",  # as-of is legislation-only
        "PC1871",
    ],
)
def test_rejects_malformed(value: str) -> None:
    assert not u.is_valid(value)


def test_neutral_citation_minting() -> None:
    assert str(u.from_neutral_citation("[2024] SGCA 15")) == "urn:sg:judgment:2024_SGCA_15"
    # Parentheses are stripped: SGHC(I) and SGHCI cannot collide.
    assert str(u.from_neutral_citation("[2019] SGHC(I) 3")) == "urn:sg:judgment:2019_SGHCI_3"
    assert (
        str(u.from_neutral_citation("[2021] SGPDPC 4 (NFA)", corpus="pdpc"))
        == "urn:sg:pdpc:2021_SGPDPC_4_NFA"
    )


def test_provision_minting_flattens_subprovisions() -> None:
    assert str(u.provision("PC1871", "300")) == "urn:sg:act:PC1871:s300"
    assert str(u.provision("PC1871", "300(1)(a)")) == "urn:sg:act:PC1871:s300-1-a"
    assert (
        str(u.provision("CA1967", "157A", as_of=date(2019, 4, 1)))
        == "urn:sg:act:CA1967:s157A@2019-04-01"
    )


def test_provisional_pdpc_urn_is_visibly_provisional() -> None:
    minted = u.provisional_pdpc("Breach of Protection Obligation by Acme Pte Ltd")
    assert str(minted).startswith("urn:sg:pdpc:x-")


def test_as_of_rejected_for_non_legislation() -> None:
    judgment = u.from_neutral_citation("[2024] SGCA 15")
    with pytest.raises(u.UrnError):
        judgment.at(date(2024, 1, 1))


def test_citation_display_forms() -> None:
    assert u.to_citation("urn:sg:judgment:2024_SGCA_15") == "[2024] SGCA 15"
    assert u.to_citation("urn:sg:judgment:2024_SGCA_15:para42") == "[2024] SGCA 15 at [42]"
    assert u.to_citation("urn:sg:act:PC1871:s300-1-a") == "s 300(1)(a) of PC1871"
    assert u.to_citation("urn:sg:hansard:2024-02-06") == "Sing. Parl. Deb., 06 Feb 2024"


def test_provision_display_round_trips_through_urn() -> None:
    minted = u.provision("CA1967", "157A(3)(b)")
    assert u.to_citation(minted) == "s 157A(3)(b) of CA1967"
