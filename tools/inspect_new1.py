from pathlib import Path

lines = Path("new_1.csv").read_text(encoding="utf-16").splitlines()
# show structure around first 80 non-empty lines after start
shown = 0
for i, line in enumerate(lines):
    if not line.strip():
        kind = "BLANK"
    elif line.startswith("CH#|Channel Name|Machine"):
        kind = "META_HDR"
    elif line.startswith("CH#|Channel Name|Sample#"):
        kind = "SAMP_HDR"
    elif line.split("|")[0].strip().isdigit() and len(line.split("|")) >= 8:
        parts = line.split("|")
        kind = "META_ROW" if parts[2] in {"Turbine", "Compressor", "Generator", "Collector", ""} or "°" in parts[4] else "SAMP_ROW"
        if parts[1] not in {"BRG1X", "BRG1Y", "Kph 1"} and kind == "SAMP_ROW":
            continue
    else:
        kind = "OTHER"
    if kind == "SAMP_ROW" and "BRG1X" not in line and "BRG1Y" not in line:
        continue
    print(f"{i+1:6} {kind:9} {line[:100]}")
    shown += 1
    if shown >= 60:
        break

print("--- around sample 2 transition ---")
# find second SAMP_HDR or first BRG1X sample 2
for i, line in enumerate(lines):
    parts = line.split("|")
    if len(parts) > 3 and parts[1] == "BRG1X" and parts[2] == "2":
        for j in range(max(0, i - 30), i + 5):
            print(f"{j+1:6} {lines[j][:110]}")
        break
