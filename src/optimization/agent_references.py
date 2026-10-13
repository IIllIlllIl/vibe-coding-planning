"""Exact, auditable identity transport; scientific strings are never rewritten."""
import copy


class ReferenceMap:
    def __init__(self, originals, prefix):
        originals = list(originals)
        if len(set(originals)) != len(originals):
            raise ValueError("reference authority contains duplicate IDs")
        self.decode_map = {f"{prefix}-{i:02d}": value for i, value in enumerate(originals, 1)}
        self.encode_map = {value: key for key, value in self.decode_map.items()}

    def decode(self, value, path):
        if not isinstance(value, str) or value not in self.decode_map:
            raise ValueError(f"{path}: unknown reference {value!r}; allowed references: {list(self.decode_map)}")
        return self.decode_map[value]


def project_identities(value, cases, items):
    """Only explicit identity fields are projected; unknown historical IDs remain."""
    if isinstance(value, list):
        return [project_identities(item, cases, items) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        mapping = cases if key == "instance_id" else items if key in {"id", "bullet_id"} else None
        result[key] = (mapping.encode_map.get(item, item)
                       if mapping is not None and isinstance(item, str)
                       else project_identities(item, cases, items))
    return result


def phase_references(role, payload):
    records = payload.get("records", [payload["record"]] if "record" in payload else [])
    artifact = payload.get("parent", payload.get("draft", {}))
    cases = ReferenceMap((record["instance_id"] for record in records), "case")
    items = ReferenceMap((item["id"] for item in artifact.get("checklist", [])), "item")
    projected = project_identities({key: value for key, value in payload.items()
                                    if key != "history"}, cases, items)
    if "history" in payload:
        # Historical branch-local IDs can name different semantic versions.
        # Do not present them as targets in the current operation namespace.
        projected["history"] = copy.deepcopy(payload["history"])
    return projected, cases, items


def decode_phase_output(role, output, cases, items):
    result = copy.deepcopy(output)
    if role == "reflector":
        result["instance_id"] = cases.decode(result.get("instance_id"), "instance_id")
        for index, tag in enumerate(result.get("bullet_tags", []), 1):
            tag["id"] = items.decode(tag.get("id"), f"bullet_tags[{index}].id")
    elif role == "curator":
        for index, operation in enumerate(result.get("operations", []), 1):
            path = f"operation {index}"
            refs = operation.get("supporting_instance_ids")
            if not isinstance(refs, list):
                raise ValueError(f"{path}: supporting_instance_ids must be an array")
            operation["supporting_instance_ids"] = [cases.decode(ref, path) for ref in refs]
            if "target_id" in operation:
                operation["target_id"] = items.decode(operation["target_id"], path + ".target_id")
            if "target_ids" in operation:
                if not isinstance(operation["target_ids"], list):
                    raise ValueError(f"{path}: target_ids must be an array")
                operation["target_ids"] = [items.decode(ref, path + ".target_ids") for ref in operation["target_ids"]]
    elif role == "refiner":
        for index, item in enumerate(result.get("checklist", []), 1):
            item["id"] = items.decode(item.get("id"), f"checklist[{index}].id")
    else:
        raise ValueError(f"unsupported reference role: {role}")
    return result
