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
    """Map declared learning-envelope references; raw scientific fields are opaque.

    A tool event, repository JSON or evaluator report may contain its own `id`
    or `instance_id`. Matching a key name anywhere in that evidence does not
    authorize rewriting it. History also retains its original namespace.
    """
    result = copy.deepcopy(value)
    if not isinstance(result, dict):
        return result

    def mapped(row, field, references):
        if isinstance(row, dict) and isinstance(row.get(field), str):
            row[field] = references.encode_map.get(row[field], row[field])

    records = result.get("records", [result["record"]] if "record" in result else [])
    for record in records:
        mapped(record, "instance_id", cases)
    reviews = list(result.get("reviews", []))
    if "bullet_tags" in result:
        reviews.append(result)  # A standalone validated Reflector review.
    for review in reviews:
        mapped(review, "instance_id", cases)
        for tag in review.get("bullet_tags", []):
            mapped(tag, "id", items)
    artifacts = [result[name] for name in ("parent", "draft") if name in result]
    if "checklist" in result:
        artifacts.append(result)  # A standalone checklist artifact.
    for artifact in artifacts:
        for item in [*artifact.get("checklist", []), *artifact.get("counters", [])]:
            mapped(item, "id", items)
    mapped(result.get("parent_counter"), "id", items)
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
