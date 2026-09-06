"""Consistent administrative context for cases, village assets and summaries."""


def normalized(value):
    return " ".join(str(value or "").split()).casefold()


def state_code(value):
    name = normalized(value)
    return "TN" if name in {"tn", "tamil nadu"} else name.upper()


def matches(value, expected):
    return not normalized(expected) or normalized(value) == normalized(expected)


def location_matches(location, *, district=None, block=None, village=None, state=None):
    return (not state or state_code(location.get("state")) == state_code(state)) and all(
        matches(location.get(key), value)
        for key, value in (("district", district), ("block", block), ("village", village)))


def claim_location(claim):
    if claim.village is not None:
        return village_location(claim.village)
    sabha = claim.gram_sabha or (claim.rights_holder.gram_sabha if claim.rights_holder else None)
    if sabha is not None:
        return {"state": sabha.state, "district": sabha.district, "block": sabha.block, "village": sabha.village}
    if claim.parcel is not None:
        return parcel_location(claim.parcel)
    return {"state": None, "district": None, "block": None, "village": None}


def parcel_location(parcel):
    return {"state": parcel.state, "district": parcel.district, "block": parcel.taluk, "village": parcel.village}


def village_location(village):
    return {"state": village.state_code, "district": village.district_name,
            "block": village.block_name, "village": village.village_name}


def asset_location(asset):
    if asset.claim is not None:
        return claim_location(asset.claim)
    return village_location(asset.village) if asset.village is not None else {}


def archive_location(record):
    return {"state": record.state_code, "district": record.district, "block": record.block, "village": record.village}
