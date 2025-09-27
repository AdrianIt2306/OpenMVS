#!/bin/bash
# Inicia el emulador MVS y el bridge Python

/tk5-/mvs &
python3 /app/bridge.py &

wait