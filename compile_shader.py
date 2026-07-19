#!/usr/bin/env python3
"""
Compile a GLSL shader with glslc.elf, entirely in Python,
by emulating the AArch64 binary with Unicorn.

Usage:
    python compile_shader.py glslc.elf shaders/example.frag --stage fragment
    python compile_shader.py glslc.elf shaders/example.frag --stage fragment --debug
    python compile_shader.py glslc.elf shaders/example.frag --stage fragment -o out.bin
"""
import argparse
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('glslc_elf', help='path to glslc.elf')
    ap.add_argument('shader_source', help='path to a .glsl/.frag/.vert source file')
    ap.add_argument('--stage', choices=STAGE_NAMES, default='fragment')
    ap.add_argument('-o', '--output', help='write the compiled shader data here '
                     '(GLSLCoutput.dataOffset..dataOffset+size -- just the payload, no header/section table)')
    ap.add_argument('--full-blob', action='store_true',
                     help='write the complete GLSLCoutput struct to -o (headers + section table + data) '
                          'instead of just the payload -- only useful if you enabled more than one output '
                          'section (--output-shader-reflection etc.) and need the headers to tell them apart')
    ap.add_argument('--debug', action='store_true', help='trace every stub call')

    # ---- GLSLCoptions.optionFlags (every bit in the header, one flag each) ----
    g = ap.add_argument_group('GLSLCoptionFlags (all default to the values glslcHelper.cpp used)')
    g.add_argument('--glsl-separable', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-assembly', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--output-gpu-binaries', action=argparse.BooleanOptionalAction, default=True)
    g.add_argument('--output-perf-stats', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--output-shader-reflection', action=argparse.BooleanOptionalAction, default=False)
    g.add_argument('--output-thin-gpu-binaries', action=argparse.BooleanOptionalAction, default=True)
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

    with open(args.shader_source, 'r', encoding='utf-8') as f:
        source = f.read()

    force_include_std_header = None
    if args.force_include_std_header_file:
        with open(args.force_include_std_header_file, 'r', encoding='utf-8') as f:
            force_include_std_header = f.read()

    print(f"[*] loading {args.glslc_elf} ...")
    emu = Emulator(args.glslc_elf, debug=args.debug)
    print("[*] module loaded, relocated, constructors run")

    glslcInitialize = emu.symbols['glslcInitialize']
    glslcCompile = emu.symbols['glslcCompile']
    glslcFinalize = emu.symbols['glslcFinalize']
    glslcSetAllocator = emu.symbols['glslcSetAllocator']

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
        input_bytes = gs.build_input(emu, sources=[source], stages=[STAGE_NAMES[args.stage]])
        emu.write_bytes(compile_obj + gs.CO_OFF_OPTIONS, options_bytes)
        emu.write_bytes(compile_obj + gs.CO_OFF_INPUT, input_bytes)

        print("[*] glslcCompile ...")
        ok = emu.call_guest_function(glslcCompile, [compile_obj])

        success, info_log, output_blob, full_blob = gs.read_compile_results(emu, compile_obj)
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
            print(f"[+] compiled shader data: {len(output_blob)} bytes "
                  f"(full GLSLCoutput struct: {len(full_blob)} bytes)")
            if args.output:
                to_write = full_blob if args.full_blob else output_blob
                with open(args.output, 'wb') as f:
                    f.write(to_write)
                print(f"[+] wrote {args.output} ({len(to_write)} bytes, "
                      f"{'full struct' if args.full_blob else 'shader data only'})")

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
