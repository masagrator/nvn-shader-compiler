#!/usr/bin/env python3
"""
Compile a GLSL shader with glslc.elf, entirely in Python,
by emulating the AArch64 binary with Unicorn.

Usage:
    python compile_shader.py glslc.elf shaders/example.frag:fragment
    python compile_shader.py glslc.elf shaders/example.frag:fragment --debug
    python compile_shader.py glslc.elf shaders/example.frag:fragment -o out.bin

    # multiple shaders in one call, ";"-separated -- independent compiles
    # by default, or linked into one program with --no-glsl-separable:
    python compile_shader.py glslc.elf "a.vert:vertex;a.frag:fragment" --no-glsl-separable -o out.bin

This mirrors the C++ example:

    std::vector<const char *> shaderSources;
    std::vector<NVNshaderStage> shaderStages;
    shaderSources.push_back(source);
    shaderStages.push_back(NVN_SHADER_STAGE_FRAGMENT);
    GLSLCcompileObject m_CompileObject{};
    glslcInitialize(&m_CompileObject);
    GLSLCoptions *options = &(m_CompileObject.options);
    options->optionFlags.outputAssembly = true;
    options->optionFlags.outputGpuBinaries = true;
    options->optionFlags.glslSeparable = true;
    options->optionFlags.outputPerfStats = true;
    options->optionFlags.outputShaderReflection = true;
    options->optionFlags.outputDebugInfo = GLSLC_DEBUG_LEVEL_G0;
    m_CompileObject.input.sources = &shaderSources[0];
    m_CompileObject.input.stages = &shaderStages[0];
    m_CompileObject.input.count = shaderSources.size();
    if (!glslcCompile(&m_CompileObject)) { ...fail... }
    glslcFinalize(&m_CompileObject);
"""
import argparse
import os
import sys

from nx_emu.emu_core import Emulator, EmulatorError, GuestExit
from nx_emu import glslc_structs as gs

STAGE_NAMES = {
    'vertex': gs.NVN_SHADER_STAGE_VERTEX,
    'fragment': gs.NVN_SHADER_STAGE_FRAGMENT,
    'geometry': gs.NVN_SHADER_STAGE_GEOMETRY,
    'tess_control': gs.NVN_SHADER_STAGE_TESS_CONTROL,
    'tess_evaluation': gs.NVN_SHADER_STAGE_TESS_EVALUATION,
    'compute': gs.NVN_SHADER_STAGE_COMPUTE,
}
LANGUAGE_NAMES = {
    'glsl': gs.GLSLC_LANGUAGE_GLSL,
    'gles': gs.GLSLC_LANGUAGE_GLES,
    'spirv': gs.GLSLC_LANGUAGE_SPIRV,
}
DEBUG_LEVEL_NAMES = {
    'none': gs.GLSLC_DEBUG_LEVEL_NONE,
    'g0': gs.GLSLC_DEBUG_LEVEL_G0,
    'g1': gs.GLSLC_DEBUG_LEVEL_G1,
    'g2': gs.GLSLC_DEBUG_LEVEL_G2,
}
SPILL_NAMES = {'default': gs.DEFAULT_SPILL, 'no_spill': gs.NO_SPILL}
OPTLEVEL_NAMES = {'default': gs.GLSLC_OPTLEVEL_DEFAULT, 'none': gs.GLSLC_OPTLEVEL_NONE}
UNROLL_NAMES = {
    'default': gs.GLSLC_LOOP_UNROLL_DEFAULT,
    'none': gs.GLSLC_LOOP_UNROLL_NONE,
    'all': gs.GLSLC_LOOP_UNROLL_ALL,
}
WARN_UNINIT_NAMES = {
    'default': gs.GLSLC_WARN_UNINIT_DEFAULT,
    'none': gs.GLSLC_WARN_UNINIT_NONE,
    'all': gs.GLSLC_WARN_UNINIT_ALL,
}


def parse_shader_spec(spec):
    """Parse "path:stage[;path:stage...]" into [(path, stage_name), ...].

    Splits entries on ';' and, within each entry, splits on the *last*
    ':' (rsplit, maxsplit=1) so a Windows-style drive-letter path like
    "C:\\shaders\\a.vert:vertex" still separates into
    ("C:\\shaders\\a.vert", "vertex") correctly.
    """
    entries = []
    for chunk in spec.split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ':' not in chunk:
            raise ValueError(f"'{chunk}' is missing a :stage suffix, e.g. 'a.vert:vertex'")
        path, stage_name = chunk.rsplit(':', 1)
        path, stage_name = path.strip(), stage_name.strip()
        if stage_name not in STAGE_NAMES:
            raise ValueError(f"unknown stage '{stage_name}' in '{chunk}' -- "
                              f"choices are: {', '.join(STAGE_NAMES)}")
        entries.append((path, stage_name))
    if not entries:
        raise ValueError("no shaders given")
    if len(entries) > 6:
        # glslc.elf itself rejects an input.count >= 7 -- verified by
        # disassembly and by actually driving it with count=7 (see the long
        # comment on build_input()'s assert in glslc_structs.py for the
        # exact addresses/error string, on both glslc.elf builds). Not
        # reachable through this CLI anyway (STAGE_NAMES only has 6 keys
        # and duplicates are rejected below), but kept as an explicit,
        # readable error for anyone hitting this from their own code.
        raise ValueError(f"{len(entries)} shaders given, but glslc.elf accepts at most 6 per call")
    seen_stages = [s for _, s in entries]
    dupes = {s for s in seen_stages if seen_stages.count(s) > 1}
    if dupes:
        # Confirmed by actually trying it (both glslc.elf builds, both
        # --glsl-separable and --no-glsl-separable): glslc.elf rejects this
        # itself with "Can't have duplicate stages in the input GLSL
        # source strings." -- checking for it here just fails fast instead
        # of spinning up the emulator for a guaranteed rejection. Combined
        # with COMPUTE never being allowed alongside a non-compute stage
        # (also unconditional -- see "Example shaders" in the README), the
        # actual reachable ceiling for a call with any chance of
        # succeeding is 5 shaders (one of each non-compute stage), not 6.
        raise ValueError(f"duplicate stage(s) {sorted(dupes)} -- glslc.elf only accepts one shader per stage per call")
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('glslc_elf', help='path to glslc.elf')
    ap.add_argument('shaders', help=(
        'one or more shaders to compile in the same call, as "path:stage" '
        'entries separated by \';\' -- e.g. a single shader is just '
        '"shaders/example.frag:fragment", multiple is '
        '"a.vert:vertex;a.frag:fragment". Stage is one of: '
        + ', '.join(STAGE_NAMES) + '. With the default --glsl-separable '
        'each shader compiles independently; pass --no-glsl-separable to '
        'link them together into one program instead (matching stages '
        'must then agree on interfaces, or glslc will report a link error).'
    ))
    ap.add_argument('-o', '--output', help='write the compiled GLSLCoutput binary blob here')
    ap.add_argument('--debug', action='store_true', help='trace every stub call')

    # ---- GLSLCoptions.optionFlags (every bit in the header, one flag each) ----
    g = ap.add_argument_group('GLSLCoptionFlags (all default to the values glslcHelper.cpp used)')
    g.add_argument('--glsl-separable', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-assembly', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-gpu-binaries', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-perf-stats', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-shader-reflection', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-thin-gpu-binaries', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--tessellation-passthrough-gs', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--prioritize-consecutive-tex', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--error-on-scratch-mem-usage', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--enable-cbf-optimization', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--enable-warp-culling', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--enable-multithread-compilation', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--language', choices=LANGUAGE_NAMES, default='glsl')
    g.add_argument('--debug-level', choices=DEBUG_LEVEL_NAMES, default='g0')
    g.add_argument('--spill-control', choices=SPILL_NAMES, default='default')
    g.add_argument('--opt-level', choices=OPTLEVEL_NAMES, default='default')
    g.add_argument('--unroll-control', choices=UNROLL_NAMES, default='default')
    g.add_argument('--warn-uninit', choices=WARN_UNINIT_NAMES, default='default')
    g.add_argument('--fast-math-mask', type=lambda s: int(s, 0), default=0,
                    help='6-bit mask, per-component fast-math enable (accepts 0x.. or decimal)')

    # ---- the rest of GLSLCoptions ----
    g2 = ap.add_argument_group('GLSLCoptions (forceIncludeStdHeader / includeInfo / xfbVaryingInfo)')
    g2.add_argument('--force-include-std-header-file',
                     help='file whose contents get force-included as a standard header')
    g2.add_argument('--include-path', action='append', default=[],
                     help='#include search path (repeatable)')
    g2.add_argument('--xfb-varying', action='append', default=[],
                     help='transform-feedback varying name (repeatable)')

    args = ap.parse_args()

    try:
        shader_specs = parse_shader_spec(args.shaders)
    except ValueError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1

    sources = []
    for path, stage_name in shader_specs:
        with open(path, 'r', encoding='utf-8') as f:
            sources.append(f.read())
    stages = [STAGE_NAMES[stage_name] for _, stage_name in shader_specs]

    force_include_std_header = None
    if args.force_include_std_header_file:
        with open(args.force_include_std_header_file, 'r', encoding='utf-8') as f:
            force_include_std_header = f.read()

    print(f"[*] loading {args.glslc_elf} ...")
    emu = Emulator(args.glslc_elf, debug=args.debug)
    print("[*] module loaded, relocated, constructors run")
    link_note = " (will be linked together)" if not args.glsl_separable and len(shader_specs) > 1 else ""
    for path, stage_name in shader_specs:
        print(f"    - {path} [{stage_name}]{link_note}")

    glslcInitialize = emu.symbols['glslcInitialize']
    glslcCompile = emu.symbols['glslcCompile']
    glslcFinalize = emu.symbols['glslcFinalize']
    glslcSetAllocator = emu.symbols['glslcSetAllocator']

    # glslcGetVersion() isn't declared anywhere in glslcinterface.h (that
    # header is data structures only, no function prototypes at all), but
    # it's a real exported symbol in glslc.elf and doesn't depend on
    # anything glslcSetAllocator/glslcInitialize set up, so it's safe to
    # call first. See glslc_structs.get_version()'s docstring for its
    # calling convention (an AAPCS64 indirect-result return, not a normal
    # argument/return-value pair).
    glslcGetVersion = emu.symbols.get('glslcGetVersion')
    if glslcGetVersion is not None:
        version = gs.get_version(emu, glslcGetVersion)
        print(
            "[*] glslcGetVersion -> "
            f"api {version['apiMajor']}.{version['apiMinor']}, "
            f"gpuCode {version['gpuCodeVersionMajor']}.{version['gpuCodeVersionMinor']}, "
            f"package {version['package']}"
        )
    else:
        print("[!] glslcGetVersion not exported by this build -- skipping", file=sys.stderr)

    # glslcInitialize refuses to run until the caller has registered
    # allocator callbacks via glslcSetAllocator(allocate, free, reallocate,
    # userPtr) -- it's a separate one-time global registration, not
    # something glslcInitialize sets up itself. The glslc_Alloc/glslc_Free/
    # glslc_Realloc stub handlers we already register as imports have the
    # right signatures ((size, align, userPtr) / (addr, userPtr) /
    # (addr, newSize, userPtr), ignoring the trailing userPtr each time),
    # so just point glslcSetAllocator at those same stub slots.
    print("[*] glslcSetAllocator ...")
    emu.call_guest_function(glslcSetAllocator, [
        emu.get_stub_addr('glslc_Alloc'),
        emu.get_stub_addr('glslc_Free'),
        emu.get_stub_addr('glslc_Realloc'),
        0,  # userPtr
    ])

    # Real usage order (matches the C++ example above): glslcInitialize
    # is called on an EMPTY compile object -- it resets/owns the `input`
    # sub-struct as part of initializing -- and options/input are only
    # populated *after* that succeeds. Building options_bytes/input_bytes
    # and splicing them in before calling glslcInitialize looked harmless
    # but glslcInitialize actually clears compileObject.input as part of
    # its own setup, silently wiping out our shader source/stage/count
    # before glslcCompile ever saw them (hence "No shader objects attached").
    compile_obj = gs.new_compile_object(emu, bytes(gs.OPTIONS_SIZE), bytes(gs.INPUT_SIZE))

    try:
        print("[*] glslcInitialize ...")
        ok = emu.call_guest_function(glslcInitialize, [compile_obj])
        init_status = emu.read_i32(compile_obj + gs.CO_OFF_INIT_STATUS)
        if not ok:
            print(f"[!] glslcInitialize failed (initStatus={init_status})", file=sys.stderr)
            return 1

        options_bytes = gs.build_options(
            emu=emu,
            force_include_std_header=force_include_std_header,
            glslSeparable=args.glsl_separable,
            outputAssembly=args.output_assembly,
            outputGpuBinaries=args.output_gpu_binaries,
            outputPerfStats=args.output_perf_stats,
            outputShaderReflection=args.output_shader_reflection,
            outputThinGpuBinaries=args.output_thin_gpu_binaries,
            tessellationAndPassthroughGS=args.tessellation_passthrough_gs,
            prioritizeConsecutiveTextureInstructions=args.prioritize_consecutive_tex,
            errorOnScratchMemUsage=args.error_on_scratch_mem_usage,
            enableCBFOptimization=args.enable_cbf_optimization,
            enableWarpCulling=args.enable_warp_culling,
            enableMultithreadCompilation=args.enable_multithread_compilation,
            language=LANGUAGE_NAMES[args.language],
            outputDebugInfo=DEBUG_LEVEL_NAMES[args.debug_level],
            spillControl=SPILL_NAMES[args.spill_control],
            optLevel=OPTLEVEL_NAMES[args.opt_level],
            unrollControl=UNROLL_NAMES[args.unroll_control],
            warnUninitControl=WARN_UNINIT_NAMES[args.warn_uninit],
            enableFastMathMask=args.fast_math_mask,
            include_paths=args.include_path,
            xfb_varyings=args.xfb_varying,
        )
        # spirv_entry_point_names / spirv_module_sizes / spirv_spec_info
        # only apply when --language spirv is used with binary (bytes)
        # sources -- not wired to the CLI since this script only ever reads
        # plain-text source files, but build_input() itself accepts them;
        # see its docstring in nx_emu/glslc_structs.py if you're compiling
        # from SPIR-V and calling build_input() directly from your own code.
        input_bytes = gs.build_input(emu, sources=sources, stages=stages)
        emu.write_bytes(compile_obj + gs.CO_OFF_OPTIONS, options_bytes)
        emu.write_bytes(compile_obj + gs.CO_OFF_INPUT, input_bytes)

        print("[*] glslcCompile ...")
        ok = emu.call_guest_function(glslcCompile, [compile_obj])

        success, info_log, output_blob = gs.read_compile_results(emu, compile_obj)
        if info_log:
            print("---- compiler info log ----")
            print(info_log)
            print("----------------------------")

        if not ok or not success:
            print("[!] compilation failed.", file=sys.stderr)
            emu.call_guest_function(glslcFinalize, [compile_obj])
            return 1

        print("[+] compilation succeeded.")
        if output_blob is not None:
            print(f"[+] compiled GLSLCoutput blob: {len(output_blob)} bytes")
            parsed = gs.parse_glslc_output(output_blob)
            print(f"    (of which {parsed['dataOffset']} bytes are GLSLCoutput header/section-table, "
                  f"{parsed['numSections']} section(s) follow)")
            for sec in parsed['sections']:
                extra = f" stage={sec['stage_name']}" if 'stage_name' in sec else ""
                print(f"      [{sec['type_name']}]{extra} {sec['size']} bytes @ offset {sec['data_offset']}")

            if args.output:
                with open(args.output, 'wb') as f:
                    f.write(output_blob)
                print(f"[+] wrote {args.output}  (full GLSLCoutput container -- header + all sections)")

                base, _ = os.path.splitext(args.output)
                # parse_shader_spec() already rejects duplicate stages (and
                # glslc.elf would reject them too), so stage_name is unique
                # per GPU_CODE section here -- no filename collisions.
                for sec in parsed['sections']:
                    if sec['type'] != gs.GLSLC_SECTION_TYPE_GPU_CODE:
                        continue
                    code_path = f"{base}.{sec['stage_name']}.code.bin"
                    control_path = f"{base}.{sec['stage_name']}.control.bin"
                    with open(code_path, 'wb') as f:
                        f.write(sec['code'])
                    with open(control_path, 'wb') as f:
                        f.write(sec['control'])
                    print(f"[+] wrote {code_path}  ({len(sec['code'])} bytes -- just the GPU machine code, "
                          f"no container/header bytes)")
                    print(f"[+] wrote {control_path}  ({len(sec['control'])} bytes -- the NVN control segment "
                          f"that has to accompany the code)")

        print("[*] glslcFinalize ...")
        emu.call_guest_function(glslcFinalize, [compile_obj])
        return 0

    except GuestExit as e:
        print(f"[!] guest code called exit({e.code}) -- something it treated as fatal", file=sys.stderr)
        return e.code or 1
    except EmulatorError as e:
        print(f"[!] emulation error: {e}", file=sys.stderr)
        print("    re-run with --debug to see the stub call trace leading up to this", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
