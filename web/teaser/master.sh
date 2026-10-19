#!/bin/bash
# Synthèse puis normalisation en deux passes (-14 LUFS, -1,5 dBTP, mode linéaire)
set -e; cd "$(dirname "$0")"
python3 -I audio4.py "$PWD"
J=$(ffmpeg -hide_banner -nostats -i bande-son-brute.wav -af loudnorm=I=-14:TP=-1.5:LRA=9:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
MI=$(echo "$J"|python3 -c "import json,sys;d=json.load(sys.stdin);print(f\"measured_I={d['input_i']}:measured_TP={d['input_tp']}:measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:offset={d['target_offset']}\")")
ffmpeg -hide_banner -loglevel error -y -i bande-son-brute.wav -af "loudnorm=I=-14:TP=-1.5:LRA=9:linear=true:$MI" -ar 48000 bande-son.wav
ffmpeg -hide_banner -nostats -i bande-son.wav -af ebur128=peak=true -f null - 2>&1 | grep -A14 Summary | grep -E " I:| Peak:|LRA:"
