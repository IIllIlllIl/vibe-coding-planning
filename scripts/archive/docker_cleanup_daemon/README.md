# Archived Docker Cleanup Daemon

These scripts are retained only to document the temporary storage workaround
used during the June 2026 checker experiment. They are not part of the current
runtime path and must not be started for new runs.

Docker lifecycle limits, dangling-image cleanup, reference-aware tagged-image
eviction, and BuildKit cache maintenance are centralized in
`src/environment/docker_env.py::DockerCapacityWindow`.
