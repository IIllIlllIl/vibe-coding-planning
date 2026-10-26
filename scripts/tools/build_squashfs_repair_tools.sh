#!/bin/bash
# A private, pinned build dependency for permission-preserving SIF conversion.
# This does not install packages or modify cluster module/system directories.
set -euo pipefail
test -n "${SLURM_JOB_ID:-}" || { echo 'Compute-node build required' >&2; exit 2; }
tools_dir="${1:?Explicit operation tools directory required}"
cd "$tools_dir"
printf '%s  %s\n' 9c4974e07c61547dae14af4ed1f358b7d04618ae194e54d6be72ee126f0d2f53 squashfs-tools-4.6.1.tar.gz | sha256sum -c -
if test ! -d squashfs-tools-4.6.1; then
    tar -xzf squashfs-tools-4.6.1.tar.gz
fi
make -C squashfs-tools-4.6.1/squashfs-tools -j1 GZIP_SUPPORT=1 XZ_SUPPORT=1 LZO_SUPPORT=0 LZ4_SUPPORT=0 ZSTD_SUPPORT=0 mksquashfs unsquashfs
./squashfs-tools-4.6.1/squashfs-tools/mksquashfs -version
# Upstream unsquashfs 4.6.1 reports its version with status 1, not 0.
./squashfs-tools-4.6.1/squashfs-tools/unsquashfs -version || test "$?" -eq 1
sha256sum ./squashfs-tools-4.6.1/squashfs-tools/mksquashfs ./squashfs-tools-4.6.1/squashfs-tools/unsquashfs
