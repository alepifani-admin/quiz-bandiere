#!/bin/sh
# Exports the local standalone Node.js toolchain into PATH.
# Usage: . .tools/path.sh   (works in bash and zsh)
NODE_LOCAL="$(cd "$(dirname "$0")" && pwd)/node/bin"
export NODE_LOCAL
export PATH="$NODE_LOCAL:$PATH"
