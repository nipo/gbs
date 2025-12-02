
I realize we currently match build pass for synthesis or simulation by
sources, but not by output.

We should change this model and add in the project definition the
extent of expected outputs. For a gowin synthesis project, this would
be something like:

```yaml
outputs:
  - type: gowin-bitstream
    path: bitstream.fs
```

That would allow us to have post-build backends able to create more
artifacts.

Adding this requires:
* adding new "output" definition in all project files, mandatory, must
  contain a list of paths/type couples.
* we define two new types: 'ghdl-simulator' and 'gowin-bitstream',
* we change gowin and ghdl to have their backend matching if (and only
  if) somebody
  
