#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p build
clang -framework Foundation -framework IOBluetooth -o build/tivoo_cmd native/tivoo_cmd.m -fobjc-arc
