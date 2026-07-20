# Running NVN glslc on Windows via Unicorn

This repository is used to run glslc included with some Nintendo Switch games locally to generate NVN shaders.

# Supported glslc versions:

| GLSLC version | NVN version | Included with game | exefs filename |
| --- | --- | --- | --- |
| 13.0.0.2 | 1.9 | Cave Story+ 1.0 | subsdk1  |
| 17.21.0.88 | 1.16 | Cave Story+ 1.3 | subsdk0 |
| 17.24.0.113 | 1.16 | Tomb Raider Definitive Edition 1.0.3 | subsdk0 |

"NVN version" cannot be newer than what game supports. NVN is backwards compatible, which means you can use files generated with old glslc in newer versions of NVN.

## Setup

Python 3.x is required. Additional python packages are required, you can install them like this:

```
pip install -r requirements.txt
```

That's `unicorn` (the CPU emulator) and `pyelftools` (ELF parsing). Both
have prebuilt Windows wheels, no compiler needed.

You need to get `glslc` from somewhere, for example from listed games above. It will be in exefs partition as some numbered "subsdk" file. You need to convert it to ELF file with `nx2elf`.<br>
Linux: https://github.com/open-ead/nx-decomp-tools-binaries/blob/master/linux/nx2elf <br>
MacOS: https://github.com/open-ead/nx-decomp-tools-binaries/blob/master/macos/nx2elf <br>
Windows: included in "tools" folder

To confirm it's glslc, convert file to ELF, then use hex editor to find inside ELF file `glslcCompile` text. 
Put generated elf file to root of this repository.

## Run

```
python compile_shader.py glslc.elf shaders/example.frag:fragment -o out.bin
```

Add `--debug` to see every imported-function call (name + first 4 args)
as it happens — this is your main debugging tool if something misbehaves,
since it shows exactly how far the compiler got and what it was asking
for when things went wrong.

### Every option

Some of those options were not tested or implemented fully.
```
usage: compile_shader.py [-h] [-o OUTPUT] [--debug] [--glsl-separable | --no-glsl-separable]
                         [--output-gpu-binaries | --no-output-gpu-binaries]
                         [--output-perf-stats | --no-output-perf-stats]
                         [--output-shader-reflection | --no-output-shader-reflection]
                         [--output-thin-gpu-binaries | --no-output-thin-gpu-binaries]
                         [--tessellation-passthrough-gs | --no-tessellation-passthrough-gs]
                         [--prioritize-consecutive-tex | --no-prioritize-consecutive-tex]
                         [--error-on-scratch-mem-usage | --no-error-on-scratch-mem-usage]
                         [--enable-cbf-optimization | --no-enable-cbf-optimization]
                         [--enable-warp-culling | --no-enable-warp-culling]
                         [--enable-multithread-compilation | --no-enable-multithread-compilation]
                         [--language {glsl,gles,spirv}] [--debug-level {none,g0,g1,g2}]
                         [--spill-control {default,no_spill}] [--opt-level {default,none}]
                         [--unroll-control {default,none,all}] [--warn-uninit {default,none,all}]
                         [--fast-math-mask FAST_MATH_MASK]
                         [--force-include-std-header-file FORCE_INCLUDE_STD_HEADER_FILE] [--include-path INCLUDE_PATH]
                         [--xfb-varying XFB_VARYING]
                         glslc_elf shaders
```

Default settings:
```
--glsl-separable --output-thin-gpu-binaries --language glsl --debug-level g0 --opt-level default --fast-math-mask 1
```

Example usage:
```
    python compile_shader.py glslc.elf shaders/example.frag:fragment
    python compile_shader.py glslc.elf shaders/example.frag:fragment --debug
    python compile_shader.py glslc.elf shaders/example.frag:fragment -o out.bin
    python compile_shader.py glslc.elf "shaders/example.vert:vertex;shaders/example.frag:fragment" --no-glsl-separable -o out.bin
```

positional arguments:
```
  glslc_elf             path to glslc.elf
  shaders               one or more shaders to compile in the same call, as "path:stage" entries separated by ';' --
                        e.g. a single shader is just "shaders/example.frag:fragment", multiple is
                        "a.vert:vertex;a.frag:fragment". Stage is one of: vertex, fragment, geometry, tess_control,
                        tess_evaluation, compute. With the default --glsl-separable each shader compiles
                        independently; pass --no-glsl-separable to link them together into one program instead
                        (matching stages must then agree on interfaces, or glslc will report a link error).
```

options:
```
  -h, --help            show this help message and exit
  -o, --output OUTPUT   write the compiled GLSLCoutput binary blob here
  --debug               trace every stub call
```

GLSLCoptionFlags:
```
  --glsl-separable, --no-glsl-separable
  --output-assembly, --no-output-assembly
  --output-gpu-binaries, --no-output-gpu-binaries
  --output-perf-stats, --no-output-perf-stats
  --output-shader-reflection, --no-output-shader-reflection
  --output-thin-gpu-binaries, --no-output-thin-gpu-binaries
  --tessellation-passthrough-gs, --no-tessellation-passthrough-gs
  --prioritize-consecutive-tex, --no-prioritize-consecutive-tex
  --error-on-scratch-mem-usage, --no-error-on-scratch-mem-usage
  --enable-cbf-optimization, --no-enable-cbf-optimization
  --enable-warp-culling, --no-enable-warp-culling
  --enable-multithread-compilation, --no-enable-multithread-compilation
  --language {glsl,gles,spirv}
  --debug-level {none,g0,g1,g2}
  --spill-control {default,no_spill}
  --opt-level {default,none}
  --unroll-control {default,none,all}
  --warn-uninit {default,none,all}
  --fast-math-mask FAST_MATH_MASK
                        6-bit mask, per-component fast-math enable (accepts 0x.. or decimal)
```

GLSLCoptions (forceIncludeStdHeader / includeInfo / xfbVaryingInfo):
```
  --force-include-std-header-file FORCE_INCLUDE_STD_HEADER_FILE
                        file whose contents get force-included as a standard header
  --include-path INCLUDE_PATH
                        #include search path (repeatable)
  --xfb-varying XFB_VARYING
                        transform-feedback varying name (repeatable)
```
