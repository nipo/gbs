Remote Execution
================

Some tools cannot run on the machine a build is started from: they are
too heavy for a laptop, they only exist for another architecture or
operating system, or their license is locked to a given host. Remote
execution lets such a build run anyway: the local gbs keeps doing
everything it does for a local build, and hands the backends whose
tool lives elsewhere to a gbs on the host that has it.

For the implementation, see :doc:`design/remote`.

How It Works
------------

The local gbs loads the configuration, resolves repositories, plans
the build and checks what is up to date, as it always does. Planning
asks each backend for passes on every host that may run it; a backend
whose tool lives on a remote host is asked *on that host*, by the gbs
running there, with that host's own tool configuration.

Passes planned on a remote host are grouped into **segments**. Each
segment is handed to the remote gbs over ssh, with the files it needs.
The remote rebuilds the part of the plan these passes form, runs it in
a temporary workspace with its own tools, and sends the outputs back,
which are installed at their local paths. Everything else in the build
runs locally, and local and remote parts of a build run concurrently
like any two tasks.

Nothing is assumed to be shared between the hosts: no filesystem, no
user name, no directory layout. Every file a remote segment reads is
sent to it, and every path is expressed relative to a known root
(project directory, repositories, build output directory) that each
host places where it wants.

Up-to-date checks stay local: a segment whose outputs are newer than
its inputs is not run, and the remote is not even asked to.

Requirements
------------

On the remote host:

- **Linux.** Remote hosts are expected to run Linux; the local host
  may run anything gbs runs on.
- **gbs installed, the same as locally.** Both sides must run the same
  gbs version and, by default, the same gbs sources (see
  `Compatibility`_). Plugins used by the build should be installed
  there too.
- **Its own configuration.** The remote reads its own
  ``~/.config/gbs.yaml``, which declares the tools installed *there*
  (paths, licenses, ``env``). Local tool definitions never travel.
  Backends select tools by identifier (``tool: vivado:2024.2``), so the
  remote must declare a matching tool.

From the local host:

- **Non-interactive ssh access.** gbs runs ``ssh -T <destination> --
  gbs remote serve --stdio`` and expects it to work without a prompt:
  use keys, an agent, or a ``ControlMaster`` connection. Host aliases,
  users, ports and jump hosts from ``~/.ssh/config`` apply as usual.

A command run over ssh without a terminal gets a non-interactive
shell, whose ``PATH`` often lacks ``~/.local/bin`` (where ``pip install
--user`` and ``pipx`` put ``gbs``). If ``ssh HOST gbs --version`` fails
while an interactive login finds ``gbs``, set ``command`` for the host
(see below).

Configuring Remote Hosts
------------------------

A remote host is any ssh destination: ``--remote user@buildsrv`` works
without configuration. Hosts needing ssh options, another ``gbs``
command, or relaxed checks are declared under ``remote_hosts:``, in
``~/.config/gbs.yaml`` or any other configuration file:

.. code-block:: yaml

   remote_hosts:
     buildsrv:
       ssh: [nipo@buildsrv.example.com, -p, 2222]
       command: ~/.local/bin/gbs
     lab:
       ssh: lab-pc
       command: [/opt/gbs/venv/bin/gbs]
       check_sources: false

Each key is the name the host is selected by. A name not declared here
is used as an ssh destination with default settings.

``ssh`` (required)
    Either a single ssh destination (``lab-pc``, ``user@host``, an alias
    from ``~/.ssh/config``), or a list of ssh arguments ending with the
    destination. Integers in the list are taken as strings, so a port
    number needs no quoting.

``command`` (default ``gbs``)
    How to run gbs on the remote host.

    - A **string** is a remote shell command line, passed to the remote
      shell as is: ``~`` and variables expand there
      (``~/.local/bin/gbs``, ``$HOME/venv/bin/gbs``, ``env
      PATH=/opt/tools/bin:$PATH gbs``).
    - A **list** is a command and its arguments, each quoted for the
      remote shell: use it for paths with spaces or shell characters.

    gbs appends ``remote serve --stdio`` to it. Global gbs options may be
    included, e.g. ``gbs -v`` to get the remote log (see
    `Troubleshooting`_).

``check_sources`` (default ``true``)
    Whether the gbs sources, and the sources of the plugins the build
    uses, must be identical on both hosts. Versions must match anyway.
    See `Compatibility`_.

An invalid entry (missing ``ssh``, an unknown key, a value of the wrong
type, an empty command) is a configuration error reported on any gbs
command, naming the file and the host. Entries of later configuration
files override earlier ones of the same name.

``gbs config dump`` shows the hosts gbs knows, with the file each comes
from and the command line it will run:

.. code-block:: text

   $ gbs config dump
   # Configuration files loaded:
   #   - /home/nipo/.config/gbs.yaml

   remote_hosts:
     buildsrv:  # from /home/nipo/.config/gbs.yaml
       ssh: ['nipo@buildsrv.example.com', '-p', '2222']
       command: '~/.local/bin/gbs'
     lab:  # from /home/nipo/.config/gbs.yaml
       ssh: ['lab-pc']
       command: '/opt/gbs/venv/bin/gbs'
       check_sources: false

Choosing What Runs Remotely
---------------------------

There are two ways to send work to a remote host: redirect a tool for
good in the configuration, or ask for a remote host on one command.

Redirecting a Tool
~~~~~~~~~~~~~~~~~~

A ``tools:`` entry may name a remote host with ``remote:`` instead of
telling where the tool is installed (``path`` or ``executable``). The
value is a ``remote_hosts`` name or an ssh destination. For instance,
on a macOS laptop where Vivado cannot be installed:

.. code-block:: yaml

   # ~/.config/gbs.yaml on the laptop
   remote_hosts:
     buildsrv:
       ssh: nipo@buildsrv.example.com
       command: ~/.local/bin/gbs

   tools:
     - name: vivado
       variant: "2024.2"
       remote: buildsrv

     - name: ghdl
       config:
         executable: /opt/homebrew/bin/ghdl

.. code-block:: yaml

   # ~/.config/gbs.yaml on buildsrv
   tools:
     - name: vivado
       variant: "2024.2"
       config:
         path: /opt/Xilinx/Vivado/2024.2

Every build needing ``vivado:2024.2`` then plans and runs the Vivado
backend on ``buildsrv``, transparently: ``gbs project build`` needs no
option, and projects stay the same whatever host their tools are on.
The Vivado passes are planned by the gbs on ``buildsrv`` with the tool
it declares there; the remote host must declare a tool matching the
identifier the backend asks for.

A redirected entry cannot also declare ``path`` or ``executable``: the
tool is not installed locally, and such an entry is a configuration
error. ``gbs config dump`` and ``gbs config tool`` show the ``remote:``
key of redirected tools.

Other backends of the same build (GHDL simulation here, Yosys, ...)
keep running locally. The connection is only opened when a build
actually needs a redirected tool: a GHDL-only build never contacts
``buildsrv``. Tools redirected to several hosts are each planned and
run on their own host, one connection per host.

``--no-remote`` on project commands disables redirection for one run.
Passes of redirected tools are then unavailable, and the planning
diagnostic lists them as such if the build needs them:

.. code-block:: text

   Passes dropped by probe() or host compatibility:
     - gbs.builtin.vivado/vivado-synthesize on local host: tool 'vivado:2024.2'
       is provided by host buildsrv; redirection disabled by --no-remote

``--no-remote`` cannot be combined with ``--remote``. ``gbs partition
validate`` always runs its tools locally, redirected ones being
unavailable.

Commands that run a tool themselves instead of through a build (such
as ``gbs openxc7 chipdb build``) cannot use a redirected tool; they
refuse with a message naming the tool and its remote host:

.. code-block:: text

   Error: tool 'bbasm:apio-2026' is redirected to host buildsrv; this command runs tools locally

Asking for a Remote Host
~~~~~~~~~~~~~~~~~~~~~~~~

``gbs project build``, ``clean``, ``show`` and ``outputs`` accept
``--remote DEST``, ``DEST`` being a ``remote_hosts`` name or an ssh
destination:

.. code-block:: bash

   gbs project build --remote buildsrv
   gbs project show --remote nipo@buildsrv.example.com

The remote host is then preferred for every backend it can serve: each
backend is asked for passes on the remote first, and a pass the remote
accepts replaces the local pass of the same name. Backends the remote
cannot serve (tool not declared there, plugin not installed or
different, probe failing) stay local. A tool redirected in the
configuration still goes to its own host.

``--remote-keep`` has remote hosts keep their temporary workspace
after the run instead of removing it, to inspect what the tools saw
and produced. It requires ``--remote``, and applies to the hosts of
redirected tools too.

The connection is opened before planning and closed when the command
ends, whatever the outcome.

Remote Commands
---------------

gbs remote info
~~~~~~~~~~~~~~~

Connects to a host and shows what it provides: its gbs version and
protocol, its plugins compared to the local ones, and its tools, with
the reason a tool is unusable there, if any.

.. code-block:: text

   $ gbs remote info buildsrv
   gbs 0.1.0, protocol 2
   plugins:
     gbs.builtin.vivado 1.0.0  # same
     gbs.builtin.yosys 1.0.0  # same
     gbs.plugin.gatecap  # not installed on buildsrv, 0.1.0 on local host
     gbs.plugin.nsl 1.0.0  # sources differ
       cdc.py: differs
   tools:
     vivado:2024.2
     quartus:prime-24.1  # unusable: tool 'quartus:prime-24.1' path /opt/intelFPGA/24.1 does not exist

A plugin's status is ``same``, ``sources differ`` (followed by the
files that differ), ``sources differ, not checked`` (with
``check_sources: false``), ``version differs``, or ``not installed`` on
one side. Only ``same`` plugins, or ``sources differ, not checked``
ones, are used on the remote host; see `Compatibility`_.

``gbs remote info`` is the first thing to run when setting a host up:
it goes through ssh, the remote ``command``, the version checks and the
remote configuration, and fails with the reason if any of them does.

gbs remote serve
~~~~~~~~~~~~~~~~

The command ssh runs on the remote host. It is not meant to be run by
hand.

.. code-block:: text

   gbs remote serve --stdio [--keep] [--blob-store DIR]

``--stdio``
    Serve one client over standard input and output, the only mode.
``--keep``
    Keep the temporary workspace when the connection ends. The local
    ``--remote-keep`` option adds it.
``--blob-store DIR``
    Blob store directory. Defaults to ``$XDG_CACHE_HOME/gbs/remote-blobs``,
    that is ``~/.cache/gbs/remote-blobs`` when ``XDG_CACHE_HOME`` is
    unset.

``gbs remote`` commands log to standard error only and write no log
files, so serving leaves nothing behind in the directory ssh lands in.

Compatibility
-------------

The remote gbs re-runs planning and dispatch code on behalf of the
local one, so both must run the same code, or the remote could build
something other than what was planned.

At Connection
~~~~~~~~~~~~~

Both sides exchange their identity: protocol version, gbs version,
plugin versions, and a digest of the gbs sources and of each plugin's
sources. A source digest covers the ``.py`` files of the package's own
directory, so two checkouts both calling themselves ``0.1.0`` but
holding different code are told apart.

The connection is refused when the protocol or gbs versions differ,
or when the gbs sources differ. The message lists every difference
and, for sources, the files that differ:

.. code-block:: text

   Error: buildsrv: buildsrv runs a gbs incompatible with local host:
     gbs sources: 3f1c...e2a0 on local host, 9b77...41d5 on buildsrv
       remote/client.py: differs
       remote/newfeature.py: only on local host

With ``check_sources: false`` on the host, source differences are only
warned about and the connection goes on; versions must still match.
This is meant for a development checkout whose uncommitted changes do
not matter to the build at hand.

Plugin differences do not prevent the connection. They are checked
where plugins are used.

Where Plugins Are Used
~~~~~~~~~~~~~~~~~~~~~~

A plugin is *identical* on both hosts when it is installed on both with
the same version and, unless ``check_sources`` is off, the same source
digest.

**Backends.** A backend is only asked for passes on the remote if its
plugin is identical on both hosts. Otherwise the remote is skipped for
that backend, which falls back to the local host; the reason shows in
the planning diagnostic (``Passes dropped by probe() or host
compatibility``) if the build fails to plan.

**Generic dispatchers.** Some plugins contribute dispatchers that act on
every build whatever the backends: output copy and compression in gbs,
constraint generators in plugins such as NSL. Inside a remote segment,
the generic dispatchers of plugins that are not identical on both
hosts are skipped, with a warning:

.. code-block:: text

   vivado-synthesize, vivado-implement on buildsrv: skipping dispatchers
   nsl_gowin_cdc, nsl_cdc_vivado: plugin gbs.plugin.nsl is not installed on buildsrv

They still run locally, on what the local part of the build sees. The
risk is in what they would have done *inside* the segment: NSL's CDC
dispatchers, for instance, generate timing constraints for
clock-domain crossings from the synthesized design. Skipped, the
remote Vivado run completes without them, and the resulting bitstream
may not meet timing on crossings although the build succeeds. Treat
this warning as a build defect to fix by installing the same plugin on
the remote host.

The remote host applies the same rules on its side, and refuses a
segment whose passes belong to a plugin it does not share with the
client.

During a Build
--------------

For each remote segment, gbs:

1. Waits until local dispatch has settled, so that every file the
   segment consumes is known (most do not exist yet), then describes
   the segment to the remote: its passes, the output group settings,
   and the files it starts from. Those that already exist, sources
   typically, are sent along. The remote recreates the passes with its
   own backends and tools, and answers with the files the segment will
   produce. The build graph is then complete, before anything runs, as
   for a local build.
2. When the segment's inputs are ready, lists their content by hash
   and uploads only the content the remote does not hold yet. The remote
   keeps received content in a content-addressed blob store,
   ``~/.cache/gbs/remote-blobs`` by default (honoring
   ``XDG_CACHE_HOME``), across runs: unchanged sources are not sent
   again.
3. Has the remote rebuild the input tree in a fresh directory of its
   temporary workspace and run the segment. Tool messages (warnings,
   errors, with file locations mapped back to local paths) and step
   progress are streamed back as the tools run, and show up in the
   local progress display and build report. A remote failure is
   reported like a local one, prefixed with the host name.
4. Downloads the outputs and installs each one at its local path at
   once (write beside, then rename), so an interrupted transfer never
   leaves a half-written output looking up to date.

The remote workspace is removed when the connection ends, unless
``--remote-keep`` is given. Interrupting the local gbs (Ctrl-C) cancels
the remote work too.

Files are sent in bounded pieces, so large inputs and outputs (IP
archives, bitstreams, checkpoints) transfer whatever their size.

Troubleshooting
---------------

**The host does not appear in** ``gbs config dump``
    Check the file it is declared in is among the loaded ones listed at
    the top of the dump. A malformed entry is not skipped: it makes
    every command fail with ``<file>: remote_hosts '<name>': <reason>``.

**Connection fails with** ``handshake failed`` **or** ``command not found``
    The error ends with the last lines the remote wrote, typically
    ``gbs: command not found`` from a non-interactive shell whose
    ``PATH`` lacks gbs. Set ``command`` to the full path
    (``~/.local/bin/gbs``), or prefix it with the environment it needs.
    Check with ``ssh -T DEST 'COMMAND --version'``, which runs exactly
    as gbs will.

**Connection refused for a version or source mismatch**
    Update gbs on one side so both match; the message lists the files
    that differ. For a development checkout on purpose, set
    ``check_sources: false`` on the host.

**A backend still runs locally, or planning fails with the tool unavailable**
    Run ``gbs remote info DEST``: the tool must be declared by the remote
    configuration under a matching identifier and be usable there, and
    the backend's plugin must be ``same``. Planning failures list every
    pass dropped per host with the reason. For a redirected tool, check
    ``gbs config tool NAME`` shows the ``remote:`` key on the entry the
    backend selects.

**Cannot dispatch ...: plugin ... is not installed on HOST**
    A segment's passes belong to a plugin the remote lacks or holds in
    another version. Install or update it there.

**Warning** ``skipping dispatchers``
    A plugin with generic dispatchers is not identical on the remote
    host; see `Where Plugins Are Used`_. Install the same plugin
    remotely.

**More detail**
    ``gbs -v`` shows the progress of the connection, the transfers
    (blobs sent and received) and whatever the remote writes to its
    standard error, prefixed with the host name. The remote gbs logs
    nothing by default and keeps no log file; set ``command: gbs -v``
    (or ``-d``) on the host to have its log forwarded, which also gives
    the workspace path kept by ``--remote-keep``.

Known Limitations
~~~~~~~~~~~~~~~~~

- **Remote tool configuration is not tracked.** Changing the remote
  ``gbs.yaml`` (another tool version, other options) does not make
  local outputs out of date. Clean the affected outputs, or the
  project, to rebuild with the new remote configuration.
- **Only declared files are sent.** A segment only gets the files its
  resources declare: sources, include directories, directory
  resources, repository definition files, the project file. A tool
  reading a file that no backend declares (for instance a Tcl script
  sourced by another script) does not find it remotely, and fails
  where the local build would work.
- **Linux remote hosts only.**
- **One remote host per command line.** ``--remote`` takes a single
  host; tools redirected in the configuration may each go to their own.
- **Redirections are not chained.** The remote gbs reads its own
  configuration. If it shares the client's ``gbs.yaml`` (synchronized
  dotfiles, shared home), it sees the tool as redirected as well and
  rejects the pass. Keep tool redirections out of the build host's
  configuration.
- **Case-insensitive client file systems.** On a client whose file
  system ignores case, as macOS does by default, two outputs whose
  paths only differ by case collide.
