library work;
use work.test_plugin.all;

entity main is
end entity;

architecture test of main is

begin

  t: process is
  begin
    print_hello("world");
    wait;
  end process;
  
end architecture;
