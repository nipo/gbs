-- PWM Generator IP
--
-- Simple configurable PWM output with Vivado IP packaging annotations.
-- Intended as an example of a packagable IP for the vivado-ip backend.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity pwm_generator is
  generic (
    counter_width_c : integer := 8
  );
  port (
    clk       : in  std_ulogic;
    resetn    : in  std_ulogic;
    duty      : in  std_ulogic_vector(counter_width_c - 1 downto 0);
    pwm_out   : out std_ulogic
  );
end entity;

architecture rtl of pwm_generator is

  -- Vivado IP interface annotations
  attribute X_INTERFACE_INFO : string;
  attribute X_INTERFACE_PARAMETER : string;

  attribute X_INTERFACE_INFO of clk : signal is
    "xilinx.com:signal:clock:1.0 clk CLK";
  attribute X_INTERFACE_PARAMETER of clk : signal is
    "ASSOCIATED_RESET resetn, FREQ_HZ 100000000";

  attribute X_INTERFACE_INFO of resetn : signal is
    "xilinx.com:signal:reset:1.0 resetn RST";
  attribute X_INTERFACE_PARAMETER of resetn : signal is
    "POLARITY ACTIVE_LOW";

  signal counter : unsigned(counter_width_c - 1 downto 0);

begin

  process(clk, resetn)
  begin
    if resetn = '0' then
      counter <= (others => '0');
      pwm_out <= '0';
    elsif rising_edge(clk) then
      counter <= counter + 1;
      if counter < unsigned(duty) then
        pwm_out <= '1';
      else
        pwm_out <= '0';
      end if;
    end if;
  end process;

end architecture;
