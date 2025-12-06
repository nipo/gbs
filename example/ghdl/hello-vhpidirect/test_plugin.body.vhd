package body test_plugin is

  procedure print_hello_foreign(name: string)
  is
  begin
    assert false report "Should not be called" severity failure;
  end procedure;
  
  attribute foreign of print_hello_foreign: procedure is "VHPIDIRECT test_plugin.so print_hello";

  procedure print_hello(name: string)
  is
  begin
    print_hello_foreign(name);
  end procedure;
  
end package body;
