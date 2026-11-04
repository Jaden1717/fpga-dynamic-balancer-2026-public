import re, html, sys

if len(sys.argv) != 3:
    raise SystemExit('Usage: gen_ps7_tcl.py LOCAL_PRESET_XML NEW_OUTPUT_TCL')
preset = html.unescape(open(sys.argv[1], encoding="utf-8").read())
block = re.search(r'preset_proc_name="ps7_preset".*?</ip_preset>', preset, re.S).group(0)

lines = []
for name, val in re.findall(r'name="(CONFIG\.[^"]+)"\s+value="([^"]*)"', block):
    val = val.replace("{", "").replace("}", "").replace("[", "").replace("]", "")
    lines.append(f"dict set ps7_cfg {name} {{{val}}}")

with open(sys.argv[2], "x", encoding="utf-8", newline='\n') as f:
    f.write("set ps7_cfg {}\n" + "\n".join(lines) + "\n")
print(len(lines), "params")
