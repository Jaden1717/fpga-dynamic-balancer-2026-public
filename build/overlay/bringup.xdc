# JA PMOD direct-plug constraints (PMDZ P1 plugged straight into JA, pin n -> JA pin n)
#   out: JA1=CS, JA2=MOSI, JA4=SCLK        in: JA3=MISO, JA7=INT1, JA9=INT2, JA10=DRDY
#   JA5/JA11=GND, JA6=3.3V (PMDZ VDD), JA8=NC, JA12=3.3V (unused; TUL PYNQ-Z2 v1.1 section 16)
set_property PACKAGE_PIN Y18 [get_ports {ja_o[0]}]
set_property PACKAGE_PIN Y19 [get_ports {ja_o[1]}]
set_property PACKAGE_PIN Y17 [get_ports {ja_o[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ja_o[*]}]

set_property PACKAGE_PIN Y16 [get_ports {ja_i[0]}]
set_property PACKAGE_PIN U18 [get_ports {ja_i[1]}]
set_property PACKAGE_PIN W18 [get_ports {ja_i[2]}]
set_property PACKAGE_PIN W19 [get_ports {ja_i[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ja_i[*]}]
