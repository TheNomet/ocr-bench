#!/usr/bin/env bash
# Download pinned terraform + crane into .tools/ (no system install, no sudo), checksum-verified.
# Re-downloads if the installed version differs from the pin.
set -euo pipefail
cd "$(dirname "$0")/.."
TF_VERSION=1.9.8
CRANE_VERSION=0.20.2
mkdir -p .tools
os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m); [ "$arch" = "x86_64" ] && arch=amd64; [ "$arch" = "aarch64" ] && arch=arm64
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

if ! .tools/terraform version 2>/dev/null | head -1 | grep -q "v$TF_VERSION$"; then
  base="https://releases.hashicorp.com/terraform/$TF_VERSION"
  zip="terraform_${TF_VERSION}_${os}_${arch}.zip"
  curl -sSfLo "$tmp/$zip" "$base/$zip"
  curl -sSfLo "$tmp/sums" "$base/terraform_${TF_VERSION}_SHA256SUMS"
  want=$(grep " $zip\$" "$tmp/sums" | cut -d' ' -f1)
  [ -n "$want" ] && [ "$(sha "$tmp/$zip")" = "$want" ] || { echo "terraform checksum mismatch" >&2; exit 1; }
  unzip -oq "$tmp/$zip" terraform -d .tools
fi

if ! .tools/crane version 2>/dev/null | grep -qx "$CRANE_VERSION"; then
  carch=$arch; [ "$carch" = "amd64" ] && carch=x86_64
  cos=$( [ "$os" = darwin ] && echo Darwin || echo Linux )
  base="https://github.com/google/go-containerregistry/releases/download/v$CRANE_VERSION"
  tgz="go-containerregistry_${cos}_${carch}.tar.gz"
  curl -sSfLo "$tmp/$tgz" "$base/$tgz"
  curl -sSfLo "$tmp/sums" "$base/checksums.txt"
  want=$(grep " $tgz\$" "$tmp/sums" | cut -d' ' -f1)
  [ -n "$want" ] && [ "$(sha "$tmp/$tgz")" = "$want" ] || { echo "crane checksum mismatch" >&2; exit 1; }
  tar -xzf "$tmp/$tgz" -C .tools crane
fi
.tools/terraform version | head -1
echo "crane $(.tools/crane version)"
