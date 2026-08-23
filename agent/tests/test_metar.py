from waterline.metar import decode_metar


def test_fractional_visibility_is_parsed_without_eval() -> None:
    decoded = decode_metar("METAR CYYZ 231600Z 25008KT 1 1/2SM BKN020 18/12 A2992")

    assert decoded["vis_sm"] == 1.5


def test_zero_denominator_does_not_execute_or_crash() -> None:
    decoded = decode_metar("METAR CYYZ 231600Z 25008KT 1 1/0SM BKN020 18/12 A2992")

    assert decoded["vis_sm"] == 1
