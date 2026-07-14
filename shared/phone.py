"""Phone-number normalization shared by call routing and FreeSWITCH config generation.

Carriers deliver the same DID inconsistently — E.164 (+918065953160), national
(08065953160), or bare (8065953160). Everything that compares numbers must agree
on one canonical form: the trailing SIGNIFICANT_DIGITS digits (10 covers Indian
national numbers). Both the DB routing lookup (conversation_call_bridge) and the
generated dialplan regex (shared.freeswitch_config) derive from here so they can
never drift apart.
"""

SIGNIFICANT_DIGITS = 10


def digits_only(number: str | None) -> str:
    """Strip a phone number down to its digits ("+91 80659-53160" -> "918065953160")."""
    return "".join(ch for ch in (number or "") if ch.isdigit())


def significant_digits(number: str | None, count: int = SIGNIFICANT_DIGITS) -> str:
    """Canonical routing key: the last `count` digits of the number.

    Returns fewer digits if the number is shorter (e.g. test extensions), or ""
    for empty/non-numeric input.
    """
    return digits_only(number)[-count:]


def dialplan_expression(number: str | None) -> str:
    """FreeSWITCH destination_number regex tolerant of country-code/prefix noise.

    Matches the significant digits with any leading +/country-code/zero prefix:
    ^(\\+?\\d*?)?8065953160$ matches +918065953160, 08065953160, 8065953160.
    """
    sig = significant_digits(number)
    if not sig:
        raise ValueError(f"no digits in phone number: {number!r}")
    return rf"^(\+?\d*?)?{sig}$"
