#!/bin/bash
# rbtools-build (Issue #1093): baut aus dem gemounteten Repo (/src)
#   - die 4 Server-I/O-Binaries nach $RBB_OUT/rbtools (build_rbbridge_tools.sh)
#   - gns_probe.exe nach $RBB_OUT/gns (tools/gns-proxy/build.sh)
set -euo pipefail

src="${RBB_SRC:-/src}"
out="${RBB_OUT:-/out}"

mkdir -p "$out/rbtools" "$out/gns"

echo "rbtools-build: Server-I/O-Tools -> $out/rbtools"
bash "$src/scripts/build_rbbridge_tools.sh" "$out/rbtools"

echo "rbtools-build: gns_probe.exe -> $out/gns"
bash "$src/tools/gns-proxy/build.sh" "$out/gns"

echo "rbtools-build: OK"
