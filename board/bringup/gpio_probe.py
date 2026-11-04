"""AXI GPIO smoke test for bringup.xsa -- address map, output enable, input read.

Standalone: use it whenever SPI is suspect but you only want to prove the GPIO path.
Do NOT use mmap slicing for register access; pynq.MMIO.read/write(offset) is the
supported form (a raw mmap slice assignment fails unless the byte count matches).

Run on board:  sudo env HOME=/home/xilinx PATH="$PATH" python3 gpio_probe.py
"""
from pynq import MMIO

BASE = 0x41200000                   # gpio_ja C_BASEADDR, from design_1.hwh
CH1_DATA, CH2_DATA = 0x00, 0x08     # C_IS_DUAL=1 -> channel 2 data sits at 0x08
JA_O_MASK = 0x7                     # ja_o[2:0] = CS / MOSI / SCLK

mm = MMIO(BASE, 0x1000)

for val in (0x7, 0x0):
    mm.write(CH1_DATA, val)
    back = mm.read(CH1_DATA) & JA_O_MASK
    print("write 0x%X -> ja_o=0x%X %s" % (val, back, "OK" if back == val else "MISMATCH"))

for bit in (0x1, 0x2, 0x4):         # CS / MOSI / SCLK one at a time
    mm.write(CH1_DATA, bit)
    print("only 0x%X high -> ja_o=0x%X" % (bit, mm.read(CH1_DATA) & JA_O_MASK))

mm.write(CH1_DATA, 0x1)             # idle: CS high, SCLK/MOSI low
print("ja_i=0x%08X  bit0=MISO(JA3) bit1=INT1(JA7) bit2=INT2(JA9) bit3=DRDY(JA10)"
      % mm.read(CH2_DATA))
