import re


def phone_parts(value):
    """Return sanitized phone components without guessing non-US semantics."""
    phone = value if isinstance(value, str) else ""
    phone = re.sub(r"[\x00-\x1f\x7f]+", " ", phone)
    phone = re.sub(r"\s+", " ", phone).strip()
    extension = False
    extension_match = re.search(
        r"(?i)\s*(?:ext(?:ension)?\.?|x)\s*[:#.-]?\s*(\d{1,10})\s*$",
        phone,
    )
    base_phone = phone
    if extension_match:
        extension = extension_match.group(1)
        base_phone = phone[:extension_match.start()].strip()
    digits = "".join(re.findall(r"\d", base_phone))
    us_digits = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    explicit_non_us_country = base_phone.startswith("+") and not (
        len(digits) == 11 and digits.startswith("1")
    )
    return phone, base_phone, digits, us_digits, extension, explicit_non_us_country


def format_phone(value):
    """Format recognizable US numbers and preserve international/legacy values."""
    phone, _base, _digits, us_digits, extension, non_us = phone_parts(value)
    if not phone:
        return False
    if len(us_digits) == 10 and not non_us:
        formatted = "(%s) %s-%s" % (
            us_digits[:3], us_digits[3:6], us_digits[6:],
        )
        if extension:
            formatted += " ext. %s" % extension
        return formatted
    return phone


def normalize_phone(value, *, required=False, max_length=64):
    """Normalize safe US storage while preserving usable non-US/legacy input."""
    phone, base_phone, digits, us_digits, extension, non_us = phone_parts(value)
    if required and not phone:
        raise ValueError("required")
    if not phone:
        return False
    if len(phone) > max_length:
        raise ValueError("too_long")
    if len(us_digits) == 10 and not non_us:
        normalized = us_digits
        if extension:
            normalized += " ext %s" % extension
        return normalized
    if len(digits) < 7:
        raise ValueError("unusable")
    normalized = base_phone
    if extension:
        normalized += " ext %s" % extension
    return normalized
