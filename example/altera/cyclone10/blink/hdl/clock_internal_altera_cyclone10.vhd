library ieee;
use ieee.std_logic_1164.all;

entity clock_internal is
  port(
    clock_o      : out std_ulogic
    );
end entity;

architecture efinix of clock_internal is

  signal int_clk : std_ulogic;

  component cyclone10lp_oscillator is
    generic(
      lpm_type: string := "cyclone10lp_oscillator"
      );
    port(
      oscena: in std_ulogic := '1';
      clkout : out std_ulogic
      );
  end component;
  
begin

  gen: cyclone10lp_oscillator
    port map(
      oscena => '1',
      clkout => clock_o
      );
  
end architecture;
