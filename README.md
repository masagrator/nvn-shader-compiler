# Running glslc.elf on Windows via Unicorn

This repository is used to run glslc included with some Nintendo Switch games locally to generate NVN shaders.

# Supported glslc versions:

| glslc version | shader version | Included with game |
| --- | --- | --- |
| 13.0.0.2 | 1.9 | Cave Story+ 1.0 |
| 17.21.0.88 | 1.16 | Cave Story+ 1.3 |
| 17.24.0.113 | 1.16 | Tomb Raider Definitive Edition 1.0.3 |

## Setup

Python 3.x is required. Additional python packages are required, you can install them like this:

```
pip install -r requirements.txt
```

That's `unicorn` (the CPU emulator) and `pyelftools` (ELF parsing). Both
have prebuilt Windows wheels, no compiler needed.

You need to get `glslc` from somewhere, for example from listed games above. It will be in exefs partition as some numbered "subsdk" file. You need to convert it to ELF file with `nx2elf`. To confirm it's glslc, use hex editor to find inside `glslcCompile` text. Put generated elf file to root of this repository.

## Run

```
python compile_shader.py glslc.elf shaders/example.frag --stage fragment -o out.bin
```

Add `--debug` to see every imported-function call (name + first 4 args)
as it happens — this is your main debugging tool if something misbehaves,
since it shows exactly how far the compiler got and what it was asking
for when things went wrong.

### Every option

Some of those options were not tested or implemented fully.
```
usage: compile_shader.py [-h] [--stage {vertex,fragment,geometry,tess_control,tess_evaluation,compute}] [-o OUTPUT]
                         [--full-blob] [--debug] [--glsl-separable | --no-glsl-separable]
                         [--output-assembly | --no-output-assembly] [--output-gpu-binaries | --no-output-gpu-binaries]
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
                         glslc_elf shader_source

```

Usage:
```
    python compile_shader.py glslc.elf shaders/example.frag --stage fragment
    python compile_shader.py glslc.elf shaders/example.frag --stage fragment --debug
    python compile_shader.py glslc.elf shaders/example.frag --stage fragment -o out.bin
```

positional arguments:
```
  glslc_elf             path to glslc.elf
  shader_source         path to a .glsl/.frag/.vert source file
```

options:
```
  -h, --help            show this help message and exit
  --stage {vertex,fragment,geometry,tess_control,tess_evaluation,compute}
  -o, --output OUTPUT   write the compiled shader data here (GLSLCoutput.dataOffset..dataOffset+size -- just the
                        payload, no header/section table)
  --full-blob           write the complete GLSLCoutput struct to -o (headers + section table + data) instead of just
                        the payload -- only useful if you enabled more than one output section (--output-shader-
                        reflection etc.) and need the headers to tell them apart
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
