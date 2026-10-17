"""QRIS / EMVCo QR parser and CRC validator."""

from __future__ import annotations


def crc16_ccitt_false(data: bytes) -> int:
    """CRC16-CCITT-FALSE: poly 0x1021, init 0xFFFF, MSB-first, no final XOR."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _parse_tlv(payload: str, start: int = 0, end: int | None = None) -> tuple[dict, int, list]:
    """Parse a TLV string into a dict of tag -> value.

    Returns (fields, consumed_index, errors).
    """
    fields: dict = {}
    errors: list = []
    i = start
    if end is None:
        end = len(payload)

    while i + 4 <= end:
        tag = payload[i:i + 2]
        length_str = payload[i + 2:i + 4]
        try:
            length = int(length_str)
        except ValueError:
            errors.append(f"Invalid length '{length_str}' for tag {tag} at position {i}")
            break

        value_start = i + 4
        value_end = value_start + length
        if value_end > end:
            errors.append(
                f"Tag {tag} declares length {length} but only {end - value_start} "
                f"bytes remain at position {i}"
            )
            break

        fields[tag] = payload[value_start:value_end]
        i = value_end

    if i < end and not errors:
        errors.append(f"Unparsed trailing data from position {i} to {end}")

    return fields, i, errors


_QRIS_DOMAIN = "ID.CO.QRIS.WWW"


def parse_qris(payload: str) -> dict:
    """Parse an EMVCo QRIS payload and validate its CRC.

    Returns a dict with merchant_name, merchant_city, merchant_category, country,
    currency, amount, type, qris_domain_found, crc_valid, raw_fields, and errors.
    """
    result = {
        "merchant_name": None,
        "merchant_city": None,
        "merchant_category": None,
        "country": None,
        "currency": None,
        "amount": None,
        "type": None,
        "qris_domain_found": False,
        "crc_valid": False,
        "raw_fields": {},
        "errors": [],
    }

    if not isinstance(payload, str):
        result["errors"].append("Payload must be a string")
        return result

    payload = payload.strip()
    if not payload:
        result["errors"].append("Empty payload")
        return result

    raw_fields, consumed, parse_errors = _parse_tlv(payload)
    result["raw_fields"] = raw_fields
    result["errors"].extend(parse_errors)

    # CRC validation: tag 63 length 04, value is 4 hex chars; compute over prefix up to and including "6304".
    crc_valid = False
    if "63" in raw_fields:
        crc_value = raw_fields["63"]
        if len(crc_value) == 4:
            try:
                expected_crc = int(crc_value, 16)
                prefix = payload[:-4].encode("ascii")
                computed = crc16_ccitt_false(prefix)
                crc_valid = computed == expected_crc
            except (ValueError, UnicodeEncodeError):
                result["errors"].append("CRC value is not valid hex")
        else:
            result["errors"].append("CRC value must be 4 hex characters")
    else:
        result["errors"].append("Missing CRC tag 63")

    result["crc_valid"] = crc_valid

    # Point of initiation: 11 = static, 12 = dynamic.
    if "01" in raw_fields:
        poi = raw_fields["01"]
        if poi == "11":
            result["type"] = "static"
        elif poi == "12":
            result["type"] = "dynamic"
        else:
            result["errors"].append(f"Unknown point of initiation method: {poi}")

    result["merchant_category"] = raw_fields.get("52")
    result["currency"] = raw_fields.get("53")
    result["country"] = raw_fields.get("58")
    result["merchant_name"] = raw_fields.get("59")
    result["merchant_city"] = raw_fields.get("60")

    if "54" in raw_fields:
        result["amount"] = raw_fields["54"]

    # QRIS domain check: tags 26-51 contain nested TLV, look for the QRIS GUI.
    qris_found = False
    for tag_num in range(26, 52):
        tag = f"{tag_num:02d}"
        if tag not in raw_fields:
            continue

        value = raw_fields[tag]
        nested, _, nested_errors = _parse_tlv(value)

        # Search nested sub-tag values.
        for sub_value in nested.values():
            if _QRIS_DOMAIN in sub_value.upper():
                qris_found = True
                break

        # Fallback: the domain may appear as a plain substring in malformed samples.
        if not qris_found and _QRIS_DOMAIN in value.upper():
            qris_found = True

        if qris_found:
            break

    result["qris_domain_found"] = qris_found

    return result
