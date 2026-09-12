"""ISO country/territory choices for search geography, not nationality inference."""
import pycountry

COUNTRIES = sorted(
    [{"code": c.alpha_2, "name": "United Kingdom (UK)" if c.alpha_2 == "GB"
      else getattr(c, "common_name", c.name)} for c in pycountry.countries],
    key=lambda c: c["name"],
)
COUNTRY_CODES = {c["code"] for c in COUNTRIES}


def country_code(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.casefold() in {"uk", "united kingdom (uk)"}:
        return "GB"
    try:
        return pycountry.countries.lookup(value).alpha_2
    except LookupError:
        return ""


def country_name(code: str) -> str:
    return next((c["name"] for c in COUNTRIES if c["code"] == code), code)
