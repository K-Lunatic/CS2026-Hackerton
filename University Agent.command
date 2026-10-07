#!/bin/zsh
cd -- "${0:A:h}" || exit 1
./run_with_python.command skills/university-agent/scripts/update_turtleneck.py --auto
./run_with_python.command -m server.local
