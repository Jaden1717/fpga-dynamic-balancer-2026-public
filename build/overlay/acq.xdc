# JA PMOD constraints for the acquisition overlay (PMDZ P1 still plugged straight into JA)
#   outputs: JA1=CS Y18, JA2=MOSI Y19, JA4=SCLK Y17
#   inputs : JA3=MISO Y16, JA10=DRDY W19   (JA7=INT1 U18 / JA9=INT2 W18 are not used here)
#   JA5/JA11=GND, JA6=3.3V (PMDZ VDD), JA8=NC, JA12=3.3V (unused; TUL PYNQ-Z2 v1.1 section 16)
set_property PACKAGE_PIN Y18 [get_ports spi_cs]
set_property PACKAGE_PIN Y19 [get_ports spi_mosi]
set_property PACKAGE_PIN Y17 [get_ports spi_sclk]
set_property PACKAGE_PIN Y16 [get_ports spi_miso]
set_property PACKAGE_PIN W19 [get_ports drdy]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_cs spi_mosi spi_sclk spi_miso drdy}]
