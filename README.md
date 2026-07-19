# Running glslc.elf on Windows via Unicorn

`glslc.elf` is the AArch64 GLSL→NVN shader compiler. It's built as a shared object with **no `DT_NEEDED` entries**
and about 87 dynamically-imported functions (libc, NVIDIA's `NvOs*` file
API, and glslc's own `glslc_Alloc`/`glslc_Realloc`/`glslc_Free` allocator
hooks) — on author's own build machines a small in-house tool maps it
and supplies all of that by hand. This project reproduces that: it's a
tiny AArch64 "process" for exactly this one file, running under Unicorn
on your x86-64 Windows machine.

## Setup

```
pip install -r requirements.txt
```

That's `unicorn` (the CPU emulator) and `pyelftools` (ELF parsing). Both
have prebuilt Windows wheels, no compiler needed.

## Run

```
python compile_shader.py glslc.elf shaders/example.frag --stage fragment -o out.bin
```

Add `--debug` to see every imported-function call (name + first 4 args)
as it happens — this is your main debugging tool if something misbehaves,
since it shows exactly how far the compiler got and what it was asking
for when things went wrong.

### Every option in glslcinterface.h is settable

Every field of `GLSLCoptions` and `GLSLCinput` that's meant to be filled
in by the caller (as opposed to `reserved[]` padding or fields glslc
itself writes, like `initStatus`/`lastCompiledResults`) is reachable:

- All 19 `GLSLCoptionFlags` bits, `forceIncludeStdHeader`, `includeInfo`
  (`--include-path`, repeatable), and `xfbVaryingInfo` (`--xfb-varying`,
  repeatable) each have a CLI flag — run `--help` for the full list.
- `sources`/`stages`/`count`, plus the SPIR-V-only fields
  (`spirvEntryPointNames`, `spirvModuleSizes`) and `spirvSpecInfo`, are
  parameters on `glslc_structs.build_input()` — not wired to the CLI
  (this script only ever reads plain-text source files) but available if
  you call it from your own Python. See its docstring for the SPIR-V
  binary-source calling convention (pass `bytes` instead of `str` per
  source). Note `GLSLCspirvSpecializationInfo`'s internal layout isn't in
  the header at all (marked `// todo: reverse from nnSdk, type never
  used`) — `build_input()` will write pre-built raw bytes there if you
  supply them, but can't construct that struct's contents for you.
- `GLSLCcompileObject.privateData` is *not* exposed as a setting: like
  `lastCompiledResults`/`reflectionSection`/`initStatus`, it's managed by
  glslc itself (confirmed by disassembly — `glslcInitialize` overwrites
  it), not something the original C++ example ever touches.

## How it works

- **`nx_emu/elf_loader.py`** — parses the ELF, maps its `PT_LOAD`
  segments, and applies the AArch64 relocations that are actually present
  in this file: `R_AARCH64_RELATIVE` (internal pointers — the vast
  majority, ~44k of them), and `R_AARCH64_GLOB_DAT`/`R_AARCH64_JUMP_SLOT`
  (imported functions/data, resolved against the stub table below). There
  is no `PT_TLS` segment, so no thread-local-storage setup is needed.

- **`nx_emu/emu_core.py`** — the Unicorn wrapper. Maps a stub region, a
  256MB heap, and an 8MB stack. The core trick for calling into/out of
  guest code from Python: point `LR` at a sentinel address that's never
  mapped, then `uc.emu_start(func, until=sentinel)` — execution stops
  automatically the instant the callee's `ret` lands on it. The same
  trick lets *stub handlers* call back into guest code (e.g. `qsort`'s
  comparator), so nested calls just work.

- **`nx_emu/libc_shim.py`** — Python implementations of all ~87 imported
  symbols: `string.h`/`ctype.h`/`math.h`, `setjmp`/`longjmp` (implemented
  properly via Unicorn's register-context save/restore, not faked),
  `qsort` (calls back into the real guest comparator), C++
  `operator new`/`delete`, the `glslc_Alloc` family, a best-effort
  `printf` family (including real AAPCS64 `va_list` parsing for the
  `v*printf` variants), and NVIDIA's `NvOs*` file/mutex API.

- **`nx_emu/glslc_structs.py`** — exact byte-for-byte layout of every
  struct in `glslcinterface.h` for the AArch64 LP64 ABI, hand-derived
  from the header (offsets computed with standard AAPCS64 alignment
  rules). `GLSLCoptionFlags` is a 19-field C bitfield mixing `uint32_t`
  and 4-byte enums — the packing simulation used here lands the fields
  into *exactly* two 32-bit words with zero slack, which is a strong
  signal it matches what the real compiler used.

- **`compile_shader.py`** — the driver, a direct translation of the C++
  example: build the source array, set `optionFlags`, call
  `glslcInitialize` → `glslcCompile` → read back
  `lastCompiledResults->compilationStatus->infoLog` → `glslcFinalize`.

## Honest caveats

I built this from **static analysis only** — disassembly, symbol tables,
relocation tables, and the header you gave me — because I don't have a
way to actually execute Unicorn/ARM64 code in the environment I wrote
this in. It's a careful, deliberate reconstruction, not a tested one. The
places most likely to need a debugging pass on your end:

1. **`NvOsFopen`/`NvOsFclose`/`NvOsFread`/`NvOsFwrite`/`NvOsFseek`/`NvOsFtell`**
   — I don't have NVIDIA's `nvos.h`, so the argument order/error-code
   convention is reconstructed from the well-known public shape of that
   API, not confirmed against this exact binary. Your example doesn't use
   `#include` paths or on-disk debug dumps, so these probably aren't even
   called for a plain in-memory compile — but if you hit a crash inside
   one of them, that's the first place to check with `--debug`.
2. **`__nnmusl_init_dso`** is stubbed as a no-op. Its real purpose
   (registering the module with author's musl-based runtime for TLS/
   relocations) is made moot by the fact that we do all the relocation
   work ourselves before any guest code runs — but if there's some other
   side effect it's supposed to have, this is where to look.
3. Any crash will print the faulting PC and the nearest known symbol
   (`--debug` isn't even required for this part) — use that plus the
   stub trace to figure out which imported function's behavior needs
   adjusting in `nx_emu/libc_shim.py`.

If you hit a wall, paste me the `--debug` output around the crash and
I'll help track it down.
