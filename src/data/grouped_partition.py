"""Label-blind three-way assignment over indivisible repository components.

The protected-only default preserves the historical SWE-chat partition. An
optional train target fills additional training components before the same
seeded subset-sum allocation of the downstream test components.
"""
import hashlib


def _closest_subset(groups, ordered, target, *, reserve_groups, allow_empty):
    choices = {0: ()}
    for group in ordered:
        for size, subset in list(choices.items()):
            if len(subset) < len(ordered) - reserve_groups:
                choices.setdefault(size + len(groups[group]), (*subset, group))
    candidates = [n for n in choices if allow_empty or n > 0]
    if not candidates:
        raise ValueError("Insufficient components for nonempty validation and test")
    size = min(candidates, key=lambda n: (abs(n - target), n))
    return set(choices[size])


def partition(assignments, exposed_ids, *, seed=42, test_target=24, train_target=None):
    by_id = {a["case_id"]: a for a in assignments}
    if len(by_id) != len(assignments) or not set(exposed_ids) <= set(by_id):
        raise ValueError("Invalid assignment IDs or exposure IDs")
    if type(test_target) is not int or test_target < 1:
        raise ValueError("test_target must be positive")
    if train_target is not None and (type(train_target) is not int or train_target < 1):
        raise ValueError("train_target must be positive")
    groups = {}
    for assignment in assignments:
        group = assignment.get("dedup_group")
        if not group:
            raise ValueError("Missing frozen repository component")
        groups.setdefault(group, []).append(assignment["case_id"])
    protected = {by_id[i]["dedup_group"] for i in exposed_ids}
    ordered = sorted(set(groups) - protected,
        key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    if len(ordered) < 2:
        raise ValueError("Insufficient components for nonempty validation and test")
    if train_target is not None:
        extra = _closest_subset(groups, ordered,
            max(0, train_target - sum(len(groups[g]) for g in protected)),
            reserve_groups=2, allow_empty=True)
        protected |= extra
        ordered = [g for g in ordered if g not in extra]
    test = _closest_subset(groups, ordered, test_target, reserve_groups=1, allow_empty=False)
    return [dict(a, split=("train" if a["dedup_group"] in protected else
                          "test" if a["dedup_group"] in test else "validation"))
            for a in sorted(assignments, key=lambda a: a["case_id"])]
