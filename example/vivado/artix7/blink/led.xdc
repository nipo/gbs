set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 1 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 30 [current_design]

set_property CFGBVS GND [current_design]
set_property CONFIG_VOLTAGE 1.8 [current_design]

# Bank 14
set_property IOSTANDARD LVCMOS33 [get_ports {led}]
set_property DRIVE 24 [get_ports {led}]
set_property PACKAGE_PIN H5 [get_ports {led}]
