# Generic timing constraint for IP validation
# The actual clock constraint will come from the consuming project
create_clock -period 10.000 -name clk [get_ports clk]
