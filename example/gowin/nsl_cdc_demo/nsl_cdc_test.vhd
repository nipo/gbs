-- NSL CDC Test Design
-- This design uses NSL naming patterns to test CDC constraint generation

library ieee;
use ieee.std_logic_1164.all;

entity nsl_cdc_test is
    port (
        clk : in std_logic;
        rst_n : in std_logic;

        -- Test inputs
        async_input : in std_logic;
        cross_domain_input : in std_logic;

        -- Test outputs
        test_output : out std_logic;
        tig_output : out std_logic
    );
end entity;

architecture rtl of nsl_cdc_test is
    -- Asynchronous net - used for CDC
    signal async_net_data : std_logic;
    signal async_net_q : std_logic;

    -- Cross-region register - CDC boundary
    signal cross_region_reg_d : std_logic;
    signal cross_region_reg_q : std_logic;

    -- TIG register with ignored output
    signal tig_reg_q_internal : std_logic;
    signal tig_reg_q_o : std_logic;

    -- Static register - constant after init
    signal tig_static_reg_q : std_logic := '0';
    signal tig_static_reg_d : std_logic;

    -- TIG register with clear/preset
    signal tig_reg_clr_q : std_logic;
    signal tig_reg_pre_q : std_logic;

    signal counter : integer range 0 to 15 := 0;

begin
    -- Async net process - asynchronous signal
    async_net_q <= async_input;
    async_net_data <= async_net_q;

    -- Cross-region register - CDC boundary
    cross_region_reg_d <= cross_domain_input;

    process(clk, rst_n)
    begin
        if rst_n = '0' then
            cross_region_reg_q <= '0';
            tig_reg_q_internal <= '0';
            counter <= 0;
            tig_reg_clr_q <= '0';
            tig_reg_pre_q <= '1';  -- Preset to 1
        elsif rising_edge(clk) then
            -- Cross-region CDC register
            cross_region_reg_q <= cross_region_reg_d;

            -- TIG register with ignored output
            tig_reg_q_internal <= cross_region_reg_q;

            -- Counter
            if counter < 15 then
                counter <= counter + 1;
            end if;

            -- TIG clear register
            if counter = 0 then
                tig_reg_clr_q <= '0';  -- CLEAR pin used
            else
                tig_reg_clr_q <= tig_reg_q_internal;
            end if;

            -- TIG preset register
            if counter = 15 then
                tig_reg_pre_q <= '1';  -- PRE pin used
            else
                tig_reg_pre_q <= not tig_reg_clr_q;
            end if;
        end if;
    end process;

    -- TIG register outputs (Q and O pins)
    tig_reg_q_o <= tig_reg_q_internal;

    -- Static register - constant value, D input should be TIG
    tig_static_reg_d <= '0';  -- Always zero
    tig_static_reg_q <= tig_static_reg_d;  -- Constant after init

    -- Outputs
    test_output <= cross_region_reg_q xor tig_reg_pre_q;
    tig_output <= tig_reg_q_o xor tig_static_reg_q;

end architecture;
