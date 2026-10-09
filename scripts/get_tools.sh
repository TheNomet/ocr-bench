#!/usr/bin/env bash
# Download pinned terraform + crane into .tools/ (no system install, no sudo).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .tools
os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m); [ "$arch" = "x86_64" ] && arch=amd64; [ "$arch" = "aarch64" ] && arch=arm64
tmp=$(mktemp -d)
if [ ! -x .tools/terraform ]; then
  curl -sSfLo "$tmp/tf.zip" "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_${os}_${arch}.zip"
  unzip -oq "$tmp/tf.zip" terraform -d .tools
fi
if [ ! -x .tools/crane ]; then
  carch=$arch; [ "$carch" = "amd64" ] && carch=x86_64
  cos=$( [ "$os" = darwin ] && echo Darwin || echo Linux )
  curl -sSfL "https://github.com/google/go-containerregistry/releases/download/v0.20.2/go-containerregistry_${cos}_${carch}.tar.gz" \
    | tar -xz -C .tools crane
fi
rm -rf "$tmp"
.tools/terraform version | head -1
echo "crane $(.tools/crane version)"
