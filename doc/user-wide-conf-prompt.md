It would be nice to have some system-wide or project-wide
configuration.

It should be found in .gbs.yaml in current directory or any parent
(like .git is found is first parent that contains it).

It should also be found in user directory in ~/.config/gbs.yaml

User-wide config should be parsed first, then local (cwd or first
found parent) should be able to override any key or subtree. (both
configs are additive).

What they should contain:

* tool definintion and configuration, for instance path where to find
  tools, matched settings (key/values),

  Tools should be identified by name like "vivado", "ghdl",
  "host_c_compiler". User may add a "variant" attribute to the tool in
  order to select among multiple concurrent installs.

* profiles that shortly define a set of backends to be used quickly
  from a project config. They should associate a name to a set of
  backends as they would be defined in `backends:` in project config.

* repositories definitions.

Example user or tree-wise configuration file:

```yaml
tools:
 - name: vivado
   variant: 2016.2
   config:
     path: /opt/Xilinx/Vivado/2016.2
 - name: vivado
   variant: 2019.2
   config:
     path: /opt/Xilinx/Vivado/2019.2
 - name: vivado
   variant: 2022.2
   config:
     path: /opt/Xilinx/Vivado/2022.2
 - name: ghdl
   vairant: jit
   config:
     executable: /Users/nipo/local/bin/ghdl
 - name: ghdl
   vairant: llvm
   config:
     executable: /opt/homebrew/bin/ghdl
 - name: gowin
   vairant: V1.9.11.3
   config:
     path: /opt/Gowin/GowinIDE.app/Contents/Resources/Gowin_EDA/

profiles:
  simulation:
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          tool: ghdl
          variant: llvm
          output_dir: build

repositories:
  - name: nsl_clean
    path: /Users/nipo/projects/nsl_clean
    loader: gbs.plugin.nsl.tree

  - name: nsl_cortex
    path: /Users/nipo/projects/nsl_cortex
    loader: gbs.plugin.nsl.tree
```

When we load project definitions, those should be automatically
merged into the project context.

Project can override anything defined in those user-wide and tree-wide
configs.

After this, `project.gbs.yaml` would look like:

```yaml
name: simple_project
topcell: top

root_library:
  name: root
  partitions:
    - name: top
      deps:
        - nsl_data.bytestream
        - nsl_data.endian
        - nsl_data.crc
        - nsl_data.prbs
        - nsl_data.text
        - nsl_simulation.assertions
        - nsl_simulation.driver
        - nsl_simulation.logging
        - nsl_amba.axi4_stream
        - nsl_amba.stream_fifo
      sources:
        - language: vhdl
          files:
            - top.vhd
```
