library ieee;
use ieee.std_logic_1164.all;

entity boundary is
  port (
    clk: in std_ulogic;
    led: out std_ulogic
  );
end boundary;

architecture arch of boundary is

  constant blink_time: integer := 50_000_000;
  signal cnt: integer := 0;
  signal led_state: std_ulogic := '0';

begin

  led <= led_state;

  process (clk)
  begin
    if (rising_edge(clk)) then
      if (cnt >= blink_time) then
        cnt <= 0;
        led_state <= not led_state;
      else
        cnt <= cnt + 1;
      end if;
    end if;
  end process;

end arch;
