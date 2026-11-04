# PYNQ-Z2 ADXL355 PMOD A + optical DO on PMOD B JB1.
set_property PACKAGE_PIN Y18 [get_ports spi_cs]
set_property PACKAGE_PIN Y19 [get_ports spi_mosi]
set_property PACKAGE_PIN Y17 [get_ports spi_sclk]
set_property PACKAGE_PIN Y16 [get_ports spi_miso]
set_property PACKAGE_PIN W19 [get_ports drdy]
set_property PACKAGE_PIN W14 [get_ports tacho]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_cs spi_mosi spi_sclk spi_miso drdy tacho}]
# Board-level SPI setup/hold and sensor group delay still require board validation.
