from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FailureClassification:
    error_class: str
    repairable: bool
    agent_quota: bool = False
    reason: str = ""


REPAIRABLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("shell_syntax", re.compile(r"syntax error|unbound variable|bad substitution", re.I)),
    ("ulhpc_submit_args", re.compile(r"unknown option|unrecognized arguments|requires a value", re.I)),
    ("module_python", re.compile(r"python3: command not found|module.*not.*found|unknown module", re.I)),
    ("dataset_staging", re.compile(r"dataset snapshot missing|link-as|stage-data|manifest\.json", re.I)),
    ("job_id_parse", re.compile(r"could not find.*job.*id|job_id", re.I)),
    ("prepare_sifs_cli", re.compile(r"prepare_apptainer_sifs\.py|invalid choice|No such file or directory", re.I)),
]

BLOCKING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "secret_related",
        re.compile(r"api[_-]?key|auth[_-]?token|access[_-]?token|secret|credential", re.I),
    ),
    ("ssh_auth", re.compile(r"Permission denied|Host key verification failed|Authentication failed", re.I)),
    ("slurm_qos", re.compile(r"QOS|AssocMax|Invalid account|permission denied", re.I)),
    ("disk_full", re.compile(r"No space left on device|Disk quota exceeded", re.I)),
    ("registry_network", re.compile(r"TLS handshake timeout|temporary failure|connection timed out|503|502|504", re.I)),
    ("core_code_error", re.compile(r"src/optimization|src/environment|Traceback.*src/", re.I | re.S)),
]

AGENT_QUOTA_PATTERN = re.compile(
    r"rate limit|usage limit|quota|subscription|too many requests|temporarily unavailable",
    re.I,
)


def classify_failure(text: str) -> FailureClassification:
    if AGENT_QUOTA_PATTERN.search(text):
        return FailureClassification("agent_quota", repairable=True, agent_quota=True)
    for error_class, pattern in BLOCKING_PATTERNS:
        if pattern.search(text):
            return FailureClassification(error_class, repairable=False)
    for error_class, pattern in REPAIRABLE_PATTERNS:
        if pattern.search(text):
            return FailureClassification(error_class, repairable=True)
    return FailureClassification("unknown", repairable=False)
