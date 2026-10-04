# provide as argument input folder, it will generate "DUMP.yaml" in working directory.
# pass -o <output_folder> to instead dump one YAML file per input file into that folder
# (the folder is created if it doesn't exist).

import yaml
import glob
import sys
import os
import argparse
import struct
import io

parser = argparse.ArgumentParser(description="Dump shader metadata from unpacked NVN shader files.")
parser.add_argument("input_folder", help="Folder containing the unpacked shader files")
parser.add_argument("-o", "--output-folder", dest="output_folder", default=None,
                     help="If given, dump one YAML file per input file into this folder (created if missing), instead of a single DUMP.yaml")
parser.add_argument("--raw-unknown", action="store_true",
                    help="Also dump the raw words of sections whose payload is not decoded (PERF_STATS), "
                         "and the raw SPH words. Off by default: these are correct bytes with unknown meaning.")
args = parser.parse_args()

files = glob.glob(f"{args.input_folder}/*.*")

def read_string(file_obj):
    chars = []
    while True:
        char = file_obj.read(1)
        if char == b'\x00' or not char:
            break
        chars.append(char)
    
    return b''.join(chars).decode("utf-8")

if (args.output_folder is not None):
    os.makedirs(args.output_folder, exist_ok=True)

class GLSLC:
    SECTION_TYPE_GPU_CODE = 0
    SECTION_TYPE_ASM_DUMP = 1
    SECTION_TYPE_PERF_STATS = 2
    SECTION_TYPE_REFLECTION = 3
    SECTION_TYPE_DEBUG_INFO = 4

OUT_MAGIC_OFF      = 0x00
OUT_RESERVEDBITS   = 0x04
OUT_OPTIONFLAGS    = 0x08
OUT_VERSION        = 0x10
OUT_SIZE           = 0x44
OUT_DATAOFFSET     = 0x48
OUT_NUMSECTIONS    = 0x4C
OUT_HEADERS        = 0x90
SECTION_HEADER_STRIDE = 0x90
SECTION_COMMON_SIZE   = 44          # size, dataOffset, type, reserved[32]

NVN_STAGE = {0: "VERTEX", 1: "FRAGMENT", 2: "GEOMETRY",
             3: "TESS_CONTROL", 4: "TESS_EVALUATION", 5: "COMPUTE"}

# NVNshaderStageBits
NVN_STAGE_BITS = [(0x01, "VERTEX"), (0x02, "FRAGMENT"), (0x04, "GEOMETRY"),
                  (0x08, "TESS_CONTROL"), (0x10, "TESS_EVALUATION"), (0x20, "COMPUTE")]

def stage_bits(mask):
    out = [n for b, n in NVN_STAGE_BITS if mask & b]
    rest = mask & ~0x3F
    if rest:
        out.append("0x%X" % rest)
    return out

UNIFORM_KIND = {0: "PLAIN", 1: "SAMPLER", 2: "IMAGE", 3: "TEXTURE", -1: "INVALID"}

LANGUAGE     = {0: "GLSL", 1: "GLES", 2: "SPIRV"}
DEBUG_LEVEL  = {0: "NONE", 1: "G0", 2: "G1", 3: "G2"}
SPILL_CONTROL = {0: "DEFAULT_SPILL", 1: "NO_SPILL"}
OPT_LEVEL    = {0: "DEFAULT", 1: "NONE"}
UNROLL       = {0: "DEFAULT", 1: "NONE", 2: "ALL"}
WARN_UNINIT  = {0: "DEFAULT", 1: "NONE", 2: "ALL"}

# GLSLCpiqTypeEnum
PIQ_TYPE = {
    0:'BOOL', 1:'BVEC2', 2:'BVEC3', 3:'BVEC4',
    4:'INT', 5:'INT_VEC2', 6:'INT_VEC3', 7:'INT_VEC4',
    8:'INT8', 9:'INT8_VEC2', 10:'INT8_VEC3', 11:'INT8_VEC4',
    12:'INT16', 13:'INT16_VEC2', 14:'INT16_VEC3', 15:'INT16_VEC4',
    16:'INT64', 17:'INT64_VEC2', 18:'INT64_VEC3', 19:'INT64_VEC4',
    20:'UINT', 21:'UINT_VEC2', 22:'UINT_VEC3', 23:'UINT_VEC4',
    24:'UINT8', 25:'UINT8_VEC2', 26:'UINT8_VEC3', 27:'UINT8_VEC4',
    28:'UINT16', 29:'UINT16_VEC2', 30:'UINT16_VEC3', 31:'UINT16_VEC4',
    32:'UINT64', 33:'UINT64_VEC2', 34:'UINT64_VEC3', 35:'UINT64_VEC4',
    36:'FLOAT', 37:'FLOAT_VEC2', 38:'FLOAT_VEC3', 39:'FLOAT_VEC4',
    40:'FLOAT16', 41:'FLOAT16_VEC2', 42:'FLOAT16_VEC3', 43:'FLOAT16_VEC4',
    44:'DOUBLE', 45:'DOUBLE_VEC2', 46:'DOUBLE_VEC3', 47:'DOUBLE_VEC4',
    48:'MAT2', 49:'MAT3', 50:'MAT4', 51:'MAT2X3',
    52:'MAT2X4', 53:'MAT3X2', 54:'MAT3X4', 55:'MAT4X2',
    56:'MAT4X3', 57:'DMAT2', 58:'DMAT3', 59:'DMAT4',
    60:'DMAT2X3', 61:'DMAT2X4', 62:'DMAT3X2', 63:'DMAT3X4',
    64:'DMAT4X2', 65:'DMAT4X3', 66:'SAMPLER_1D', 67:'SAMPLER_2D',
    68:'SAMPLER_3D', 69:'SAMPLER_CUBE', 70:'SAMPLER_1D_SHADOW', 71:'SAMPLER_2D_SHADOW',
    72:'SAMPLER_1D_ARRAY', 73:'SAMPLER_2D_ARRAY', 74:'SAMPLER_1D_ARRAY_SHADOW', 75:'SAMPLER_2D_ARRAY_SHADOW',
    76:'SAMPLER_2D_MULTISAMPLE', 77:'SAMPLER_2D_MULTISAMPLE_ARRAY', 78:'SAMPLER_CUBE_SHADOW', 79:'SAMPLER_BUFFER',
    80:'SAMPLER_2D_RECT', 81:'SAMPLER_2D_RECT_SHADOW', 82:'INT_SAMPLER_1D', 83:'INT_SAMPLER_2D',
    84:'INT_SAMPLER_3D', 85:'INT_SAMPLER_CUBE', 86:'INT_SAMPLER_1D_ARRAY', 87:'INT_SAMPLER_2D_ARRAY',
    88:'INT_SAMPLER_2D_MULTISAMPLE', 89:'INT_SAMPLER_2D_MULTISAMPLE_ARRAY', 90:'INT_SAMPLER_BUFFER', 91:'INT_SAMPLER_2D_RECT',
    92:'UINT_SAMPLER_1D', 93:'UINT_SAMPLER_2D', 94:'UINT_SAMPLER_3D', 95:'UINT_SAMPLER_CUBE',
    96:'UINT_SAMPLER_1D_ARRAY', 97:'UINT_SAMPLER_2D_ARRAY', 98:'UINT_SAMPLER_2D_MULTISAMPLE', 99:'UINT_SAMPLER_2D_MULTISAMPLE_ARRAY',
    100:'UINT_SAMPLER_BUFFER', 101:'UINT_SAMPLER_2D_RECT', 102:'IMAGE_1D', 103:'IMAGE_2D',
    104:'IMAGE_3D', 105:'IMAGE_2D_RECT', 106:'IMAGE_CUBE', 107:'IMAGE_BUFFER',
    108:'IMAGE_1D_ARRAY', 109:'IMAGE_2D_ARRAY', 110:'IMAGE_CUBE_MAP_ARRAY', 111:'IMAGE_2D_MULTISAMPLE',
    112:'IMAGE_2D_MULTISAMPLE_ARRAY', 113:'INT_IMAGE_1D', 114:'INT_IMAGE_2D', 115:'INT_IMAGE_3D',
    116:'INT_IMAGE_2D_RECT', 117:'INT_IMAGE_CUBE', 118:'INT_IMAGE_BUFFER', 119:'INT_IMAGE_1D_ARRAY',
    120:'INT_IMAGE_2D_ARRAY', 121:'INT_IMAGE_CUBE_MAP_ARRAY', 122:'INT_IMAGE_2D_MULTISAMPLE', 123:'INT_IMAGE_2D_MULTISAMPLE_ARRAY',
    124:'UINT_IMAGE_1D', 125:'UINT_IMAGE_2D', 126:'UINT_IMAGE_3D', 127:'UINT_IMAGE_2D_RECT',
    128:'UINT_IMAGE_CUBE', 129:'UINT_IMAGE_BUFFER', 130:'UINT_IMAGE_1D_ARRAY', 131:'UINT_IMAGE_2D_ARRAY',
    132:'UINT_IMAGE_CUBE_MAP_ARRAY', 133:'UINT_IMAGE_2D_MULTISAMPLE', 134:'UINT_IMAGE_2D_MULTISAMPLE_ARRAY', 135:'SAMPLER_CUBE_MAP_ARRAY',
    136:'INT_SAMPLER_CUBE_MAP_ARRAY', 137:'UINT_SAMPLER_CUBE_MAP_ARRAY', 138:'SAMPLER_CUBE_MAP_ARRAY_SHADOW', 139:'SAMPLER',
    140:'TEXTURE_1D', 141:'TEXTURE_2D', 142:'TEXTURE_3D', 143:'TEXTURE_CUBE',
    144:'TEXTURE_1D_SHADOW', 145:'TEXTURE_2D_SHADOW', 146:'TEXTURE_1D_ARRAY', 147:'TEXTURE_2D_ARRAY',
    148:'TEXTURE_1D_ARRAY_SHADOW', 149:'TEXTURE_2D_ARRAY_SHADOW', 150:'TEXTURE_2D_MULTISAMPLE', 151:'TEXTURE_2D_MULTISAMPLE_ARRAY',
    152:'TEXTURE_CUBE_SHADOW', 153:'TEXTURE_BUFFER', 154:'TEXTURE_2D_RECT', 155:'TEXTURE_2D_RECT_SHADOW',
    156:'TEXTURE_CUBE_MAP_ARRAY', 157:'TEXTURE_CUBE_MAP_ARRAY_SHADOW', 158:'INT_TEXTURE_1D', 159:'INT_TEXTURE_2D',
    160:'INT_TEXTURE_3D', 161:'INT_TEXTURE_CUBE', 162:'INT_TEXTURE_1D_ARRAY', 163:'INT_TEXTURE_2D_ARRAY',
    164:'INT_TEXTURE_2D_MULTISAMPLE', 165:'INT_TEXTURE_2D_MULTISAMPLE_ARRAY', 166:'INT_TEXTURE_BUFFER', 167:'INT_TEXTURE_2D_RECT',
    168:'INT_TEXTURE_CUBE_MAP_ARRAY', 169:'UINT_TEXTURE_1D', 170:'UINT_TEXTURE_2D', 171:'UINT_TEXTURE_3D',
    172:'UINT_TEXTURE_CUBE', 173:'UINT_TEXTURE_1D_ARRAY', 174:'UINT_TEXTURE_2D_ARRAY', 175:'UINT_TEXTURE_2D_MULTISAMPLE',
    176:'UINT_TEXTURE_2D_MULTISAMPLE_ARRAY', 177:'UINT_TEXTURE_BUFFER', 178:'UINT_TEXTURE_2D_RECT', 179:'UINT_TEXTURE_CUBE_MAP_ARRAY',
    -1:'GLSLC_PIQ_INVALID_TYPE',
}

def piq_type(v):
    return PIQ_TYPE.get(v, "TYPE_%d" % v)

def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]

def i32(b, off):
    return struct.unpack_from("<i", b, off)[0]

def u64(b, off):
    return struct.unpack_from("<Q", b, off)[0]

def u8(b, off):
    return b[off]


def decode_option_flags(flags):
    fm = (flags >> 20) & 0x3F
    return {
        "GLSL_SEPARABLE":                 bool(flags & (1 << 0)),
        "OUTPUT_ASSEMBLY":                bool(flags & (1 << 1)),
        "OUTPUT_GPU_BINARIES":            bool(flags & (1 << 2)),
        "OUTPUT_PERF_STATS":              bool(flags & (1 << 3)),
        "OUTPUT_SHADER_REFLECTION":       bool(flags & (1 << 4)),
        "LANGUAGE":                       LANGUAGE.get((flags >> 5) & 0xF, (flags >> 5) & 0xF),
        "DEBUG_INFO":                     DEBUG_LEVEL.get((flags >> 9) & 0xF, (flags >> 9) & 0xF),
        "SPILL_CONTROL":                  SPILL_CONTROL.get((flags >> 13) & 0xF, (flags >> 13) & 0xF),
        "OUTPUT_THIN_GPU_BINARIES":       bool(flags & (1 << 17)),
        "TESSELLATION_AND_PASSTHROUGH_GS": bool(flags & (1 << 18)),
        "PRIORITIZE_CONSECUTIVE_TEXTURE_INSTRUCTIONS": bool(flags & (1 << 19)),
        "FAST_MATH_MASK":                 "0x%X (%s)" % (fm, "+".join(stage_bits(fm)) if fm else "none"),
        "OPT_LEVEL":                      OPT_LEVEL.get((flags >> 26) & 0x7, (flags >> 26) & 0x7),
        "UNROLL_CONTROL":                 UNROLL.get((flags >> 29) & 0x7, (flags >> 29) & 0x7),
        "ERROR_ON_SCRATCH_MEM_USAGE":     bool(flags & (1 << 32)),
        "ENABLE_CBF_OPTIMIZATION":        bool(flags & (1 << 33)),
        "ENABLE_WARP_CULLING":            bool(flags & (1 << 34)),
        "ENABLE_MULTITHREAD_COMPILATION": bool(flags & (1 << 35)),
        "WARN_UNINIT_CONTROL":            WARN_UNINIT.get((flags >> 36) & 0x7, (flags >> 36) & 0x7)
    }


def option_flag_names(flags):
    names = []
    if flags & 1:          names.append("GLSL-SEPARABLE")
    if flags & 2:          names.append("OUTPUT-ASSEMBLY")
    if flags & 4:          names.append("OUTPUT-GPU-BINARIES")
    if flags & 8:          names.append("OUTPUT-PERM-STATS")
    if flags & 0x10:       names.append("OUTPUT-REFLECTION")
    if (flags >> 9) & 0xF: names.append("DEBUG-LEVEL_G%d" % (((flags >> 9) & 0xF) - 1))
    if flags & 0x2000:     names.append("SPILL-CONTROL_NO-SPILL")
    if flags & 0x20000:    names.append("OUTPUT-THIN-GPU-BINARIES")
    if flags & 0x40000:    names.append("TESSELATION-AND-PASSTHROUGH-GS")
    if flags & 0x80000:    names.append("PRIORITIZE-CONSECUTIVE-TEXTURE-INSTRUCTIONS")
    if (flags >> 20) & 0x3F: names.append("FAST-MATH-MASK_0x%X" % ((flags >> 20) & 0x3F))
    if flags & 0x4000000:  names.append("OPT-LEVEL_NONE")
    if flags & 0x20000000: names.append("UNROLL-CONTROL_NONE")
    if flags & 0x40000000: names.append("UNROLL-CONTROL_ALL")
    if flags & 0x100000000: names.append("ERROR-ON-SCRATCH-MEM-USE")
    if flags & 0x200000000: names.append("CBF-OPTIMIZATION")
    if flags & 0x400000000: names.append("WARP-CULLING")
    if flags & 0x800000000: names.append("MULTITHREADED-COMPILATION")
    if flags & 0x1000000000: names.append("WARN-UNINIT_NONE")
    if flags & 0x2000000000: names.append("WARN-UNINIT_ALL")
    return names


# GLSLCprogramReflectionHeader.
REFL_FIELDS = ["numUniformBlocks", "uniformBlockOffset", "numUniforms", "uniformOffset",
               "numProgramInputs", "programInputsOffset", "numProgramOutputs", "programOutputsOffset",
               "numSsbo", "ssboOffset", "numBufferVariables", "bufferVariableOffset",
               "numXfbVaryings", "xfbVaryingsOffset", "stringPoolSize", "stringPoolOffset",
               "shaderInfoOffset", "numSubroutines", "subroutineOffset",
               "numSubroutineUniforms", "subroutineUniformOffset",
               "subroutineCompatibleIndexPoolSize", "subroutineCompatibleIndexPoolOffset"]

SZ_UNIFORM_BLOCK   = 108   # GLSLCuniformBlockInfo
SZ_UNIFORM         = 164   # GLSLCuniformInfo
SZ_PROGRAM_INPUT   = 92    # GLSLCProgramInputInfo
SZ_PROGRAM_OUTPUT  = 92    # GLSLCProgramOutputInfo
SZ_SSBO            = 108   # GLSLCssboInfo
SZ_BUFFER_VARIABLE = 112   # see ProcessReflection
SZ_SHADER_INFO     = 44    # GLSLCshaderInfoCompute: workGroupSize[3] + reserved[32]
NAME_INFO_SIZE     = 40    # GLSLCpiqName: nameOffset, nameLength, reserved[32]


def bindings_by_stage(b, off):
    """int32 bindings[6], indexed by NVNshaderStage; -1 where the stage does
    not use the object.  Verified: a fragment-only sampler puts its binding at
    index 1, a compute SSBO at index 5."""
    out = {}
    for st in range(6):
        v = i32(b, off + 4 * st)
        if v != -1:
            out[NVN_STAGE[st]] = v
    return out


def ProcessReflection(buf, hdr_off, data_off, section_size):
    """Decode the reflection section.  `hdr_off` is the section's entry in the
    container header table; `data_off` is where its data starts in the file."""
    RESULT = {"TYPE": "REFLECTION"}
    h = {}
    for i, name in enumerate(REFL_FIELDS):
        h[name] = u32(buf, hdr_off + SECTION_COMMON_SIZE + 4 * i)

    sp_off, sp_size = h["stringPoolOffset"], h["stringPoolSize"]

    def fits(off, count, stride):
        return off >= 0 and count >= 0 and off + count * stride <= section_size

    ok = (sp_off + sp_size <= section_size
          and fits(h["uniformBlockOffset"], h["numUniformBlocks"], SZ_UNIFORM_BLOCK)
          and fits(h["uniformOffset"], h["numUniforms"], SZ_UNIFORM)
          and fits(h["programInputsOffset"], h["numProgramInputs"], SZ_PROGRAM_INPUT)
          and fits(h["programOutputsOffset"], h["numProgramOutputs"], SZ_PROGRAM_OUTPUT)
          and fits(h["ssboOffset"], h["numSsbo"], SZ_SSBO)
          and fits(h["bufferVariableOffset"], h["numBufferVariables"], SZ_BUFFER_VARIABLE)
          and fits(h["shaderInfoOffset"], 6, SZ_SHADER_INFO))
    if ok and h["numUniformBlocks"]:
        ok = h["uniformBlockOffset"] + h["numUniformBlocks"] * SZ_UNIFORM_BLOCK == h["uniformOffset"]
    if ok and h["numUniforms"]:
        ok = h["uniformOffset"] + h["numUniforms"] * SZ_UNIFORM == h["programInputsOffset"]
    if ok and h["numProgramInputs"]:
        ok = h["programInputsOffset"] + h["numProgramInputs"] * SZ_PROGRAM_INPUT == h["programOutputsOffset"]
    if ok and h["numProgramOutputs"]:
        ok = h["programOutputsOffset"] + h["numProgramOutputs"] * SZ_PROGRAM_OUTPUT == h["ssboOffset"]
    if ok and h["numSsbo"]:
        ok = h["ssboOffset"] + h["numSsbo"] * SZ_SSBO == h["bufferVariableOffset"]
    if ok and h["numBufferVariables"]:
        ok = h["bufferVariableOffset"] + h["numBufferVariables"] * SZ_BUFFER_VARIABLE == h["xfbVaryingsOffset"]

    if not ok:
        RESULT["NOTE"] = ("reflection header did not match the layout in "
                          "glslcinterface.h; nothing decoded rather than guessed")
        RESULT["HEADER"] = h
        return RESULT

    base = data_off

    def name_at(rec):
        """GLSLCpiqName.  nameLength INCLUDES the NUL terminator -- verified:
        a 27-character name reports 28."""
        no = u32(buf, rec)
        nl = u32(buf, rec + 4)
        if nl == 0 or no + nl > sp_size:
            return None
        raw = buf[base + sp_off + no: base + sp_off + no + nl]
        if not raw.endswith(b"\x00"):
            return None
        return raw[:-1].decode("utf-8", errors="replace")

    def check_names(off, count, stride):
        for i in range(count):
            if name_at(base + off + i * stride) is None:
                return False
        return True

    if not (check_names(h["uniformBlockOffset"], h["numUniformBlocks"], SZ_UNIFORM_BLOCK)
            and check_names(h["uniformOffset"], h["numUniforms"], SZ_UNIFORM)
            and check_names(h["programInputsOffset"], h["numProgramInputs"], SZ_PROGRAM_INPUT)
            and check_names(h["programOutputsOffset"], h["numProgramOutputs"], SZ_PROGRAM_OUTPUT)
            and check_names(h["ssboOffset"], h["numSsbo"], SZ_SSBO)
            and check_names(h["bufferVariableOffset"], h["numBufferVariables"], SZ_BUFFER_VARIABLE)):
        RESULT["NOTE"] = ("Reflection string pool did not resolve.")
        RESULT["HEADER"] = h
        return RESULT

    blocks = []
    for i in range(h["numUniformBlocks"]):
        r = base + h["uniformBlockOffset"] + i * SZ_UNIFORM_BLOCK
        blocks.append({
            "NAME": name_at(r),
            "ACTIVE_VARIABLES": u32(buf, r + 44),
            "STAGES": stage_bits(u32(buf, r + 48)),
            "BINDINGS": bindings_by_stage(buf, r + 52),
        })
    RESULT["UNIFORM_BLOCKS"] = blocks

    uniforms = []
    for i in range(h["numUniforms"]):
        r = base + h["uniformOffset"] + i * SZ_UNIFORM
        u = {
            "NAME": name_at(r),
            "TYPE": piq_type(i32(buf, r + 40)),
            "BLOCK_INDEX": i32(buf, r + 44),
            "BLOCK_OFFSET": i32(buf, r + 48),
            "ARRAY_SIZE": u32(buf, r + 52),
            "ARRAY_STRIDE": u32(buf, r + 56),
            "MATRIX_STRIDE": i32(buf, r + 60),
            "IS_ROW_MAJOR": bool(u32(buf, r + 64)),
            "STAGES": stage_bits(u32(buf, r + 68)),
            "BINDINGS": bindings_by_stage(buf, r + 72),
            "KIND": UNIFORM_KIND.get(i32(buf, r + 96), i32(buf, r + 96)),
            "IS_IN_UBO": bool(u8(buf, r + 100)),
            "IS_ARRAY": bool(u8(buf, r + 101)),
        }
        uniforms.append(u)
    RESULT["UNIFORMS"] = uniforms

    inputs = []
    for i in range(h["numProgramInputs"]):
        r = base + h["programInputsOffset"] + i * SZ_PROGRAM_INPUT
        inputs.append({
            "NAME": name_at(r),
            "TYPE": piq_type(i32(buf, r + 40)),
            "ARRAY_SIZE": u32(buf, r + 44),
            "LOCATION": i32(buf, r + 48),
            "STAGES": stage_bits(u32(buf, r + 52)),
            "IS_ARRAY": bool(u8(buf, r + 56)),
            "IS_PER_PATCH": bool(u8(buf, r + 57)),
        })
    RESULT["PROGRAM_INPUTS"] = inputs

    outputs = []
    for i in range(h["numProgramOutputs"]):
        r = base + h["programOutputsOffset"] + i * SZ_PROGRAM_OUTPUT
        outputs.append({
            "NAME": name_at(r),
            "TYPE": piq_type(i32(buf, r + 40)),
            "ARRAY_SIZE": u32(buf, r + 44),
            "LOCATION": i32(buf, r + 48),
            "LOCATION_INDEX": i32(buf, r + 52),
            "STAGES": stage_bits(u32(buf, r + 56)),
            "IS_ARRAY": bool(u8(buf, r + 60)),
            "IS_PER_PATCH": bool(u8(buf, r + 61)),
        })
    RESULT["PROGRAM_OUTPUTS"] = outputs

    ssbos = []
    for i in range(h["numSsbo"]):
        r = base + h["ssboOffset"] + i * SZ_SSBO
        ssbos.append({
            "NAME": name_at(r),
            "ACTIVE_VARIABLES": u32(buf, r + 44),
            "BINDINGS": bindings_by_stage(buf, r + 48),
            "STAGES": stage_bits(u32(buf, r + 72)),
        })
    RESULT["SHADER_STORAGE_BLOCKS"] = ssbos
    bufvars = []
    for i in range(h["numBufferVariables"]):
        r = base + h["bufferVariableOffset"] + i * SZ_BUFFER_VARIABLE
        bufvars.append({
            "NAME": name_at(r),
            "TYPE": piq_type(i32(buf, r + 40)),
            "BLOCK_INDEX": i32(buf, r + 44),
            "BLOCK_OFFSET": i32(buf, r + 48),
            "ARRAY_SIZE": u32(buf, r + 52),
            "ARRAY_STRIDE": u32(buf, r + 56),
            "MATRIX_STRIDE": i32(buf, r + 60),
            "IS_ROW_MAJOR": bool(u32(buf, r + 64)),
            "TOP_LEVEL_ARRAY_SIZE": u32(buf, r + 68),
            "TOP_LEVEL_ARRAY_STRIDE": u32(buf, r + 72),
            "STAGES": stage_bits(u32(buf, r + 76)),
            "IS_ARRAY": bool(u8(buf, r + 80)),
        })
    RESULT["BUFFER_VARIABLES"] = bufvars

    wg = struct.unpack_from("<3I", buf, base + h["shaderInfoOffset"] + 5 * SZ_SHADER_INFO)
    if any(wg):
        RESULT["COMPUTE_WORK_GROUP_SIZE"] = list(wg)

    for key, count in (("XFB_VARYINGS", h["numXfbVaryings"]),
                       ("SUBROUTINES", h["numSubroutines"]),
                       ("SUBROUTINE_UNIFORMS", h["numSubroutineUniforms"])):
        if count:
            RESULT[key + "_COUNT"] = count
            RESULT[key + "_NOTE"] = "record layout not documented; not decoded"

    RESULT["STRING_POOL_SIZE"] = sp_size
    return RESULT



# ---- Bob Jenkins lookup8 hash() (64-bit), used by NVN for SOURCE/GLASM/SHADER hashes
_M64 = (1 << 64) - 1
def _mix64(a, b, c):
    a = (a - b - c) & _M64; a ^= c >> 43
    b = (b - c - a) & _M64; b ^= (a << 9) & _M64
    c = (c - a - b) & _M64; c ^= b >> 8
    a = (a - b - c) & _M64; a ^= c >> 38
    b = (b - c - a) & _M64; b ^= (a << 23) & _M64
    c = (c - a - b) & _M64; c ^= b >> 5
    a = (a - b - c) & _M64; a ^= c >> 35
    b = (b - c - a) & _M64; b ^= (a << 49) & _M64
    c = (c - a - b) & _M64; c ^= b >> 11
    a = (a - b - c) & _M64; a ^= c >> 12
    b = (b - c - a) & _M64; b ^= (a << 18) & _M64
    c = (c - a - b) & _M64; c ^= b >> 22
    return a, b, c

def jenkins_lookup8(k, level=0):
    n = len(k); a = b = level & _M64; c = 0x9e3779b97f4a7c13; i = 0
    while n - i >= 24:
        a = (a + int.from_bytes(k[i:i + 8], "little")) & _M64
        b = (b + int.from_bytes(k[i + 8:i + 16], "little")) & _M64
        c = (c + int.from_bytes(k[i + 16:i + 24], "little")) & _M64
        a, b, c = _mix64(a, b, c); i += 24
    c = (c + n) & _M64
    t = k[i:]; L = len(t)
    for j in range(L - 1, 15, -1): c = (c + (t[j] << (8 * (j - 16) + 8))) & _M64
    for j in range(min(L, 16) - 1, 7, -1): b = (b + (t[j] << (8 * (j - 8)))) & _M64
    for j in range(min(L, 8) - 1, -1, -1): a = (a + (t[j] << (8 * j))) & _M64
    return _mix64(a, b, c)[2]

GL_PRIM = {0x0: "POINTS", 0x1: "LINES", 0xA: "LINES_ADJACENCY", 0x4: "TRIANGLES", 0xC: "TRIANGLES_ADJACENCY",
           0x7: "QUADS", 0x8E7A: "ISOLINES"}
GL_SPACING = {0x202: "EQUAL", 0x8E7B: "FRACTIONAL_ODD", 0x8E7C: "FRACTIONAL_EVEN"}
GL_WINDING = {0x900: "CW", 0x901: "CCW"}


INTERLOCK_NVN = {1: ("PIXEL_ORDERED", 0x2), 2: ("PIXEL_UNORDERED", 0xA), 3: ("SAMPLE_ORDERED", 0x1),
                 4: ("SAMPLE_UNORDERED", 0x9)}

def control_program(cb, stage):
    """Program-level control fields (0x6F0..0x7BF).  3D method names are from NVIDIA open-gpu-doc
    classes/3d/clb197.h (method byte offset = deko3d method index * 4)."""
    R = {}
    R["CODE_LENGTH"] = u32(cb, 0x6F8)                 # unpadded, from the code magic; SHADER_HASH input
    R["CONSTBUF1_SIZE"] = u32(cb, 0x6FC)
    R["CONSTBUF1_OFFSET"] = u32(cb, 0x700)
    R["CODE_BLOB_SIZE"] = u32(cb, 0x704)
    R["CODE_ENTRY_OFFSET"] = u32(cb, 0x708)           # 0x30 = SPH (graphics), 0x100 = code (compute)
    R["NUM_GPRS"] = u32(cb, 0x70C)
    R["SCRATCH_PER_WARP"] = u32(cb, 0x710)            # local*32 + CRS
    if stage == 1:
        R["EARLY_FRAGMENT_TESTS"] = bool(cb[0x718])   # method 0x084 SET_API_MANDATED_EARLY_Z
        R["POST_DEPTH_COVERAGE"] = bool(cb[0x719])    # method 0x3C7 SET_POST_Z_PS_IMASK
        if cb[0x71A]:                                  # method 0x3D0 SET_CONSTANT_COLOR_RENDERING (+0x3D1..0x3D4 RGBA)
            R["CONSTANT_COLOR"] = list(struct.unpack_from("<4f", cb, 0x724))
        R["WRITES_DEPTH"] = bool(u32(cb, 0x71C))
        il = u32(cb, 0x720)
        if il:                                         # method 0x489 SET_PIXEL_SHADER_INTERLOCK_CONTROL
            name, reg = INTERLOCK_NVN.get(il, (il, None))
            R["INTERLOCK"] = name
            if reg is not None: R["INTERLOCK_REGISTER"] = "0x%X" % reg
        R["COLOR_OUTPUT_COUNT"] = u32(cb, 0x734)      # highest location + 1
        zb = (1 if cb[0x748] else 0) | (0x10 if cb[0x749] else 0)
        R["ZCULL_BOUNDS"] = "0x%02X" % zb               # method 0x65B SET_ZCULL_BOUNDS: bit0 z-min unbounded, bit4 z-max unbounded
        R["PER_SAMPLE_SHADING"] = bool(cb[0x74A])     # method 0x1D5 SET_HYBRID_ANTI_ALIAS_CONTROL
    if stage == 5:
        R["CS_BLOCK_DIMS"] = list(struct.unpack_from("<3I", cb, 0x748))
        R["CS_SHARED_MEM"] = u32(cb, 0x754)
        R["CS_USES_BARRIER"] = bool(u32(cb, 0x764))
    if stage == 2:
        R["GS_VIEWPORT_RELATIVE_LAYER"] = bool(cb[0x798])   # method 0x47C SET_OFFSET_RENDER_TARGET_INDEX
        if cb[0x799]:                                        # method 0x490 SET_POST_VTG_SHADER_ATTRIBUTE_SKIP_MASK
            R["GS_PASSTHROUGH_SKIP_MASK"] = ["0x%08X" % w for w in struct.unpack_from("<8I", cb, 0x7A0)]
    return R

# Field gating by NVN GPU version (control word 0x08).  Layout below 0x7D0 is common; newer GPU versions
# appended fields.  Versions 1.10-1.13 have not been checked, so words in a gated range on an older version
# are reported raw under UNVERIFIED_CONTROL_WORDS instead of being decoded.
GATE_V10 = 0x7D0   # 0x7D0..0x847 : GPU 1.10+
GATE_V15 = 0x848   # 0x848..0x84F : GPU 1.15+
GATE_V16 = 0x850   # 0x850..      : GPU 1.16+

def control_tail(cb, stage, gpu_minor, code_bytes=None):
    """Decode the remaining control-section fields.  `cb` = control bytes (from its magic)."""
    R = {}
    go, gs = u32(cb, 0x14), u32(cb, 0x18)
    end = min(go or 0x878, 0x878)          # GLASM text starts right after the fixed fields

    # ---- common part (below 0x7D0)
    R["CONTROL_SIZE_COPY"] = u32(cb, 0x7C8)            # == 0x20 / 0x6F0
    if u32(cb, 0x7C0): R["USES_SUBROUTINES"] = True
    if cb[0x6ED]:
        # transform feedback. per buffer b (0..3):
        #   0xDC +0x80b  u8[]  vec4 output slot per entry (0x20+n = location n; 0xFD/0xFB = gap)
        #   0x2DC+0x80b  u8[]  first component inside that slot
        #   0x4DC+0x80b  u8[]  number of components
        #   0x6DC+2b u16 stride, 0x6E4+b u8 vertex stream, 0x6E8+b u8 entry count
        #   0x6EC u8 number of buffers (highest+1), 0x6ED u8 enabled
        xfb = []
        for b in range(cb[0x6EC]):
            n = cb[0x6E8 + b]
            if not n: continue
            ent = []
            for i in range(n):
                slot, comp, cnt = cb[0xDC + 0x80 * b + i], cb[0x2DC + 0x80 * b + i], cb[0x4DC + 0x80 * b + i]
                ent.append({"SKIP": cnt} if slot in (0xFD, 0xFB) else
                           {"LOCATION": slot - 0x20, "COMPONENT": comp, "COUNT": cnt} if slot >= 0x20 else
                           {"SLOT": "0x%02X" % slot, "COMPONENT": comp, "COUNT": cnt})
            xfb.append({"BUFFER": b, "STREAM": cb[0x6E4 + b],
                        "STRIDE": struct.unpack_from("<H", cb, 0x6DC + 2 * b)[0], "ENTRIES": ent})
        R["TRANSFORM_FEEDBACK"] = xfb
    if u32(cb, 0x1C):
        R["DYNAMIC_CONST_ARRAYS_SIZE"] = u32(cb, 0x1C)       # register-indexed const arrays at start of c1, rounded to 0x80
    if cb[0x780]: R["HAS_DEBUG_INFO"] = True                 # debug hash at 0x778
    if cb[0x79A]: R["TESSELLATION_AND_PASSTHROUGH_GS"] = True
    if stage == 5:
        R["CS_LOCAL_MEM_LO"] = u32(cb, 0x758); R["CS_LOCAL_MEM_HI"] = u32(cb, 0x75C); R["CS_CRS_SIZE"] = u32(cb, 0x760)
    if stage == 4:
        R["TES_DOMAIN"] = GL_PRIM.get(u32(cb, 0x738), u32(cb, 0x738))
        R["TES_SPACING"] = GL_SPACING.get(u32(cb, 0x73C), u32(cb, 0x73C))
        R["TES_POINT_MODE"] = bool(u32(cb, 0x740))
        R["TES_WINDING"] = GL_WINDING.get(u32(cb, 0x744), u32(cb, 0x744))
    KNOWN = set(range(0x00, 0x24, 4)) | set(range(0x2C, 0x5C, 4)) | set(range(0xDC, 0x6F0, 4)) | \
        set(range(0x6F0, 0x768, 4)) | {0x778, 0x77C, 0x780, 0x790, 0x794, 0x798} | set(range(0x7A0, 0x7C4, 4)) | \
        {0x7C8}

    # ---- GPU 1.10+ : 0x7D0..0x847
    if gpu_minor >= 10:
        src_h, glasm_h, sh_h = u64(cb, 0x7D0), u64(cb, 0x7D8), u64(cb, 0x7E0)
        R["SOURCE_HASH"] = "%016X" % src_h                 # Jenkins lookup8(source text bytes, 0)
        R["GLASM_HASH"] = "%016X" % glasm_h                # lookup8(GLASM text, 0)
        R["GLASM_HASH_OK"] = jenkins_lookup8(cb[go:go + gs]) == glasm_h
        R["SHADER_HASH"] = "%016X" % sh_h                  # lookup8(code section[0:ctrl 0x6F8], 0)
        if code_bytes is not None:
            R["SHADER_HASH_OK"] = jenkins_lookup8(code_bytes[:u32(cb, 0x6F8)]) == sh_h
        R["CONTROL_SIZE_COPY2"] = u32(cb, 0x7E8)
        n_img, n_smp = cb[0x7F8], cb[0x7F9]
        if n_img:
            R["IMAGE_BINDINGS"] = list(cb[0x7FC:0x7FC + n_img])
        if n_smp:
            R["SAMPLER_BINDINGS"] = list(cb[0x804:0x804 + n_smp])   # combined samplers only
        if cb[0x831]: R["WRITES_IMAGES"] = True
        if cb[0x841]: R["READS_IMAGES"] = True
        if cb[0x832]: R["USES_BINDLESS_TEXTURES"] = True
        if cb[0x840]: R["USES_GLOBAL_POINTERS"] = True           # NV_shader_buffer_load / uint64 handles
        nptr = u32(cb, 0x7F0)
        if nptr:
            # 12-byte records at ctrl[0x20] (end of control), one per *used* buffer member of a 64-bit type
            # (int64/uint64/double scalars, vectors, matrices) or pointer type:
            #   u8 binding, u8 buffer type (0 UBO, 1 SSBO), s16 GLSLCpiqTypeEnum (-1 = pointer),
            #   u32 byte offset of the member in the block, u32 array size (1 = not an array, 0 = unsized)
            tab = u32(cb, 0x20); recs = []
            for i in range(nptr):
                binding, btype, ty, off, arr = struct.unpack_from("<BBhII", cb, tab + 12 * i)
                recs.append({"BUFFER": {0: "UBO", 1: "SSBO"}.get(btype, btype), "BINDING": binding,
                             "TYPE": "POINTER" if ty == -1 else piq_type(ty), "OFFSET": off, "ARRAY_SIZE": arr})
            R["BUFFER_64BIT_MEMBERS"] = recs                      # 0x7EC = table size (12*count), 0x7F0 = count
        if stage == 2:
            R["GS_INPUT_PRIMITIVE"] = GL_PRIM.get(u32(cb, 0x828), u32(cb, 0x828))
        KNOWN |= set(range(0x7D0, 0x824, 4)) | {0x828, 0x830, 0x840}

    # ---- GPU 1.15+ : 0x848..0x84F
    if gpu_minor >= 15:
        if stage == 3:
            R["TCS_DEFAULT_DOMAIN"] = GL_PRIM.get(u32(cb, 0x848), u32(cb, 0x848))
            R["TCS_DEFAULT_SPACING"] = GL_SPACING.get(u32(cb, 0x84C), u32(cb, 0x84C))
        KNOWN |= {0x848, 0x84C}

    # ---- GPU 1.16+ : 0x850..
    if gpu_minor >= 16:
        if stage == 3:
            R["TCS_DEFAULT_WINDING"] = GL_WINDING.get(u32(cb, 0x854), u32(cb, 0x854))
        if stage == 4:
            # 0x868 always 1 for TES, 0x86C fractional spacing, 0x870 point mode, 0x874 clockwise
            R["TES_FLAGS_868"] = [u32(cb, 0x868), u32(cb, 0x86C), u32(cb, 0x870), u32(cb, 0x874)]
        KNOWN |= {0x854, 0x868, 0x86C, 0x870, 0x874}

    gate = GATE_V10 if gpu_minor < 10 else GATE_V15 if gpu_minor < 15 else GATE_V16 if gpu_minor < 16 else end
    unk = {"0x%X" % o: "0x%X" % u32(cb, o) for o in range(0, min(gate, end), 4) if u32(cb, o) and o not in KNOWN}
    if unk: R["UNKNOWN_CONTROL_WORDS"] = unk
    unv = {"0x%X" % o: "0x%X" % u32(cb, o) for o in range(gate, end, 4) if u32(cb, o)}
    if unv: R["UNVERIFIED_CONTROL_WORDS"] = unv          # gated range on an older GPU version: not decoded
    return R

def presenter2(dumper, data):
    if '\n' in data:
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)

def ProcessDebugInfo(file, section_offset, hdr_off=None, section_size=None):
    RESULT = {}
    pos = file.tell()
    file.seek(section_offset)
    magic = int.from_bytes(file.read(4), "little")
    assert(magic == 0x65040891)
    RESULT["TYPE"] = "DEBUG_INFO"
    in_data = u64(BUF, section_offset + 0x18)
    RESULT["HASH"] = "%016X" % in_data
    if hdr_off is not None:
        build_id = struct.unpack_from("<4I", BUF, hdr_off + SECTION_COMMON_SIZE)
        lo = u32(BUF, hdr_off + SECTION_COMMON_SIZE + 16)
        hi = u32(BUF, hdr_off + SECTION_COMMON_SIZE + 20)
        if any(build_id):
            RESULT["BUILD_ID"] = "".join("%08X" % w for w in build_id)
        if in_data != ((hi << 32) | lo):
            RESULT["DEBUG_HASH_NOTE"] = ("section header says %08X%08X" % (hi, lo))
    file.seek(section_offset + 0x30)
    text_length = int.from_bytes(file.read(4), "little")
    header_length = int.from_bytes(file.read(4), "little")
    file.seek(section_offset + header_length)
    text_bytes = file.read(text_length)
    text = text_bytes.decode("utf-8", errors="replace")
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "    ")
    text = text.replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    RESULT["SOURCE"] = text
    file.seek(pos)
    return RESULT

def ProcessAsmDump(file, section_offset, hdr_off=None, section_size=None):
    RESULT = {}
    pos = file.tell()
    file.seek(section_offset)
    RESULT["TYPE"] = "ASM_DUMP"
    if hdr_off is not None:
        st = i32(BUF, hdr_off + SECTION_COMMON_SIZE)
        RESULT["STAGE"] = NVN_STAGE.get(st, st)
    text = read_string(file)
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "    ")
    text = "\n".join(line[1:] if line.startswith("\t") else line
                     for line in text.split("\n"))
    text = text.replace("\x00", "")
    text = text.replace("\t", "    ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    RESULT["ASSEMBLY"] = text
    file.seek(pos)
    return RESULT


def ProcessPerfStats(section_offset, section_size):
    """The perf-stats payload.

    GLSLCperfStatsHeader adds no fields of its own, and the payload's meaning
    is not described anywhere in the header.  Its words are a mixture of IEEE
    floats and integers that could not be tied to anything checkable, so they
    are NOT interpreted here; --raw-unknown dumps them for whoever wants to
    work on it.  The magic and the size are certain, and that is what is
    reported by default.
    """
    RESULT = {"TYPE": "PERF_STATS"}
    if args.raw_unknown:
        words = []
        for off in range(0, section_size & ~3, 4):
            words.append("+%d: 0x%08X" % (off, u32(BUF, section_offset + off)))
        RESULT["UNDECODED_WORDS"] = words
    return RESULT


def gpu_code_header(hdr_off):
    """GLSLCgpuCodeHeader, the fields after the common part.

    Checked for self-consistency on every file this was run against: the word
    at data+controlOffset is the control magic 0x98761234, the word at
    data+dataOffset is the code magic 0x1234567[89], and both
    controlOffset+controlSize and dataOffset+dataSize land inside the section.
    """
    names = ["stage", "controlOffset", "dataOffset", "dataSize", "controlSize",
             "scratchMemBytesPerWarp", "scratchMemBytesRecommended",
             "asmDumpSectionIdx", "perfStatsSectionNdx",
             "subroutineLinkageMapOffset", "subroutineLinkageMapSize"]
    v = struct.unpack_from("<11i", BUF, hdr_off + SECTION_COMMON_SIZE)
    h = dict(zip(names, v))
    out = {
        "STAGE": NVN_STAGE.get(h["stage"], h["stage"]),
        "CONTROL_OFFSET": h["controlOffset"],
        "CONTROL_SIZE": h["controlSize"],
        "CODE_OFFSET": h["dataOffset"],
        "CODE_SIZE": h["dataSize"],
        "SCRATCH_MEM_BYTES_PER_WARP": h["scratchMemBytesPerWarp"],
        "SCRATCH_MEM_BYTES_RECOMMENDED": h["scratchMemBytesRecommended"],
    }
    if h["subroutineLinkageMapSize"]:
        out["SUBROUTINE_LINKAGE_MAP_OFFSET"] = h["subroutineLinkageMapOffset"]
        out["SUBROUTINE_LINKAGE_MAP_SIZE"] = h["subroutineLinkageMapSize"]
    return h, out


# SPH output topology, bits 24..27 of CommonWord3.  Established by compiling a
# geometry shader per output primitive: `layout(points) out` reports 1,
# `line_strip` 6, `triangle_strip` 7.  Anything else is left as a number.
SPH_OUTPUT_TOPOLOGY = {1: "POINTS", 6: "LINE_STRIP", 7: "TRIANGLE_STRIP"}


def Process(magic, file, hdr_off=None, section_size=None):
    ENTRY = {}
    base = file.tell()
    if (magic == 0x12345679):
        ENTRY["TYPE"] = "CODE"
        ENTRY["SHADER_TYPE"] = "COMPUTE"
    elif (magic == 0x12345678):
        ENTRY["TYPE"] = "CODE"
        file.seek(base + 0x30)
        CommonWord0 = int.from_bytes(file.read(4), "little")
        ENTRY["SPH_TYPE"] = CommonWord0 & 0b11111
        match(ENTRY["SPH_TYPE"]):
            case 1: ENTRY["SPH_TYPE"] = "VertexTessGeometry"
            case 2: ENTRY["SPH_TYPE"] = "PixelShader"
        ENTRY["VERSION"] = (CommonWord0 & 0b1111100000) >> 5
        ENTRY["SHADER_TYPE"] = (CommonWord0 & 0b11110000000000) >> 10
        match(ENTRY["SHADER_TYPE"]):
            case 1: ENTRY["SHADER_TYPE"] = "VERTEX"
            case 2: ENTRY["SHADER_TYPE"] = "TESS_INIT"
            case 3: ENTRY["SHADER_TYPE"] = "TESS"
            case 4: ENTRY["SHADER_TYPE"] = "GEOMETRY"
            case 5: ENTRY["SHADER_TYPE"] = "PIXEL"
        ENTRY["MRT_ENABLE"] = bool((CommonWord0 & 0b100000000000000) >> 14)
        ENTRY["KILL_PIXELS"] = bool((CommonWord0 & 0b1000000000000000) >> 15)
        ENTRY["GLOBAL_STORE"] = bool((CommonWord0 & 0b10000000000000000) >> 16)
        ENTRY["SASS_VERSION"] = (CommonWord0 & 0b111100000000000000000) >> 17
        ENTRY["RESERVED0"] = (CommonWord0 & 0b1000000000000000000000) >> 21
        ENTRY["RESERVED1"] = (CommonWord0 & 0b10000000000000000000000) >> 22
        ENTRY["RESERVED2"] = (CommonWord0 & 0b100000000000000000000000) >> 23
        ENTRY["FAST_GS"] = bool((CommonWord0 & 0b1000000000000000000000000) >> 24)
        ENTRY["VSH_UNK_FLAG"] = bool((CommonWord0 & 0b10000000000000000000000000) >> 25)
        ENTRY["LOAD_OR_STORE"] = bool((CommonWord0 & 0b100000000000000000000000000) >> 26)
        ENTRY["FP64"] = bool((CommonWord0 & 0b1000000000000000000000000000) >> 27)
        ENTRY["STREAM_OUT_MASK"] = (CommonWord0 & 0b11110000000000000000000000000000) >> 28
        CommonWord1 = int.from_bytes(file.read(4), "little")
        CommonWord2 = int.from_bytes(file.read(4), "little")
        ENTRY["SH_LOCAL_MEM"] = (CommonWord1 & 0b111111111111111111111111) + ((CommonWord2 & 0b111111111111111111111111) << 24) # Dunno if good
        ENTRY["PER_PATCH_ATTR_CNT"] = (CommonWord1 & 0b11111111000000000000000000000000) >> 24
        ENTRY["THR_PER_INPUT_PRIM"] = (CommonWord2 & 0b11111111000000000000000000000000) >> 24
        CommonWord3 = int.from_bytes(file.read(4), "little")
        ENTRY["SH_LOCAL_MEM_CRS_SZ"] = (CommonWord3 & 0b111111111111111111111111)
        # FIXED: this used to read bits 28..31 but shift by 24, which made it
        # always report 0.  The field is bits 24..27; see SPH_OUTPUT_TOPOLOGY.
        topology = (CommonWord3 >> 24) & 0xF
        ENTRY["OUTPUT_TOPOLOGY"] = SPH_OUTPUT_TOPOLOGY.get(topology, topology)
        # FIXED: the old mask for this was 36 bits wide, applied to a 32-bit
        # word, so it could never be anything but 0.
        ENTRY["RESERVED3"] = (CommonWord3 >> 28) & 0xF
        CommonWord4 = int.from_bytes(file.read(4), "little")
        ENTRY["MAX_OUT_VTX_CNT"] = (CommonWord4 & 0b111111111111)
        ENTRY["STORE_REQ_START"] = (CommonWord4 & 0b11111111000000000000) >> 12
        ENTRY["RESERVED4"] = (CommonWord4 & 0b111100000000000000000000) >> 20
        ENTRY["STORE_REQ_END"] = (CommonWord4 & 0b11111111000000000000000000000000) >> 24
        if args.raw_unknown:
            file.seek(base + 0x30)
            raw = file.read(0x50)
            ENTRY["SPH_RAW_WORDS"] = ["+0x%02X: 0x%08X" % (0x30 + i * 4, w)
                                      for i, w in enumerate(struct.unpack("<%dI" % (len(raw) // 4), raw))]
    elif (magic == 0x98761234):
        #print("offset: 0x%x" % file.tell())
        ENTRY["TYPE"] = "CONTROL"
        file.seek(4, 1)
        gpu_major = int.from_bytes(file.read(4), "little")
        gpu_minor = int.from_bytes(file.read(4), "little")
        ENTRY["NVN_VERSION"] = "%d.%d" % (gpu_major, gpu_minor)
        arch = int.from_bytes(file.read(4), "little")
        impl = int.from_bytes(file.read(4), "little")
        if (arch == 0x120 and impl == 0xB):
            ENTRY["ARCH"] = "GM20B"
        else:
            ENTRY["ARCH"] = "0x%x/0x%x" % (arch, impl)
        glasm_offset = int.from_bytes(file.read(4), "little")
        glasm_size = int.from_bytes(file.read(4), "little")
        if (glasm_size > 0):
            pos = file.tell()
            file.seek(base + glasm_offset)
            #print("glasm: 0x%x" % file.tell())
            text = file.read(glasm_size).decode("ascii")
            file.seek(pos)
            ENTRY["GLASM"] = text
        else:
            ENTRY["GLASM"] = None
        file.seek(base + 0x20)
        unk_section_offset = int.from_bytes(file.read(4), "little")
        unk_section_size = int.from_bytes(file.read(4), "little") # always 0
        control_size = unk_section_offset + unk_section_size
        file.seek(base + 0x790)
        flags = int.from_bytes(file.read(8), "little")
        ENTRY["FLAGS"] = option_flag_names(flags)
        if (flags & 0x20 == 0x20):
            ENTRY["INPUT_LANGUAGE"] = "GLES"
        elif (flags & 0x40 == 0x40):
            ENTRY["INPUT_LANGUAGE"] = "SPIR-V"
        else:
            ENTRY["INPUT_LANGUAGE"] = "GLSL"
        ENTRY["OPTIONS"] = decode_option_flags(flags)
        file.seek(base + 0x714)
        type = file.read(1)[0]
        match(type):
            case 0:
                ENTRY["STAGE"] = "VERTEX"
            case 1:
                ENTRY["STAGE"] = "FRAGMENT"
            case 2:
                ENTRY["STAGE"] = "GEOMETRY"
            case 3:
                ENTRY["STAGE"] = "TESS_CONTROL"
            case 4:
                ENTRY["STAGE"] = "TESS_EVALUATION"
            case 5:
                ENTRY["STAGE"] = "COMPUTE"
            case _:
                print("Unknown stage: %d!" % type)
                sys.exit()
        file.seek(base + 0x778)
        debug_hash = file.read(8).hex().upper()
        ENTRY["DEBUG-INFO_HASH"] = debug_hash
        cb = BUF[base:]
        ENTRY.update(control_program(cb, type))
        ENTRY.update(control_tail(cb, type, gpu_minor, CODE_FOR_CONTROL.get(base)))

    elif (magic == 0x19866891):
        ENTRY["TYPE"] = "OUTPUT"
        size = u32(BUF, OUT_SIZE)
        reserved_bits = u32(BUF, OUT_RESERVEDBITS)
        if reserved_bits:
            ENTRY["RESERVED_BITS"] = "0x%08X" % reserved_bits
        ver = struct.unpack_from("<5I", BUF, OUT_VERSION)
        ENTRY["GLSLC_VERSION"] = {
            "API": "%d.%d" % (ver[0], ver[1]),
            "NVN_VERSION": "%d.%d" % (ver[2], ver[3]),
            "PACKAGE": ver[4],
        }
        ENTRY["OPTIONS"] = decode_option_flags(u64(BUF, OUT_OPTIONFLAGS))
        data_offset = u32(BUF, OUT_DATAOFFSET)
        ENTRY["DATA"] = []
        file.seek(OUT_NUMSECTIONS)
        section_num = int.from_bytes(file.read(4), "little")
        ENTRY["SECTION_COUNT"] = section_num
        sections = []
        for i in range(section_num):
            hdr = OUT_HEADERS + (i * SECTION_HEADER_STRIDE)
            file.seek(hdr)
            size = int.from_bytes(file.read(4), "little")
            offset = int.from_bytes(file.read(4), "little")
            type = int.from_bytes(file.read(4), "little")
            info = {"INDEX": i, "OFFSET": offset,
                    "TYPE": {0: "GPU_CODE", 1: "ASM_DUMP", 2: "PERF_STATS",
                             3: "REFLECTION", 4: "DEBUG_INFO"}.get(type, type)}
            if (type == GLSLC.SECTION_TYPE_GPU_CODE):
                raw, decoded = gpu_code_header(hdr)
                info.update(decoded)
                if raw["asmDumpSectionIdx"] or raw["perfStatsSectionNdx"]:
                    info["ASM_DUMP_SECTION_INDEX"] = raw["asmDumpSectionIdx"]
                    info["PERF_STATS_SECTION_INDEX"] = raw["perfStatsSectionNdx"]
            sections.append(info)
            if (type > GLSLC.SECTION_TYPE_DEBUG_INFO):
                continue
            if (type == GLSLC.SECTION_TYPE_DEBUG_INFO):
                ENTRY["DATA"].append(ProcessDebugInfo(file, offset, hdr, size))
                continue
            elif (type == GLSLC.SECTION_TYPE_ASM_DUMP):
                ENTRY["DATA"].append(ProcessAsmDump(file, offset, hdr, size))
                continue
            elif (type != GLSLC.SECTION_TYPE_GPU_CODE):
                if (type == GLSLC.SECTION_TYPE_REFLECTION):
                    ENTRY["DATA"].append(ProcessReflection(BUF, hdr, offset, size))
                else:
                    ENTRY["DATA"].append(ProcessPerfStats(offset, size))
                continue
            control_offset = raw["controlOffset"]
            code_offset = raw["dataOffset"]
            ENTRY2 = []
            CODE_FOR_CONTROL[offset + control_offset] = BUF[offset + code_offset: offset + code_offset + raw["dataSize"]]
            file.seek(offset + control_offset)
            magic = int.from_bytes(file.read(4), "little")
            file.seek(-4, 1)
            ENTRY2.append(Process(magic, file))
            file.seek(offset + code_offset)
            #print("0x%x" % file.tell())
            magic = int.from_bytes(file.read(4), "little")
            file.seek(-4, 1)
            ENTRY2.append(Process(magic, file))
            if ENTRY2[0].get("STAGE") not in (None, info.get("STAGE")):
                ENTRY2[0]["STAGE_NOTE"] = ("gpu code header says %s"
                                           % info.get("STAGE"))
            ENTRY["DATA"].append(ENTRY2)
        ENTRY["SECTIONS"] = sections
    elif (magic == 0x19292919):
        # A reflection section on its own. Its counts and offsets live in the
        # container's section header table, not in this blob, so a standalone
        # file cannot be decoded -- dump the .nvn instead.
        ENTRY["TYPE"] = "REFLECTION"
    elif (magic == 0x12898888):
        ENTRY = ProcessPerfStats(0, len(BUF))
    elif (magic == 0x65040891):
        ENTRY = ProcessDebugInfo(file, 0, None, len(BUF))
    return ENTRY


CODE_FOR_CONTROL = {}
yaml.add_representer(str, presenter2)
yaml.add_representer(str, presenter2, Dumper=yaml.SafeDumper)

dump_file = None
if (args.output_folder is None):
    dump_file = open("DUMP.yaml", "w", encoding="UTF-8")

for i in range(len(files)):
    print(files[i])
    with open(files[i], "rb") as fh:
        BUF = fh.read()
    file = io.BytesIO(BUF)
    magic = int.from_bytes(file.read(4), "little")
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    ENTRY = Process(magic, file)
    file.close()

    if (args.output_folder is not None):
        out_name = os.path.basename(files[i]) + ".yaml"
        out_path = os.path.join(args.output_folder, out_name)
        out_file = open(out_path, "w", encoding="UTF-8")
        yaml.safe_dump({files[i]: ENTRY}, out_file, sort_keys=False, default_flow_style=False)
        out_file.close()
    else:
        yaml.safe_dump({files[i]: ENTRY}, dump_file, sort_keys=False, default_flow_style=False)
        
    del ENTRY

if (args.output_folder is None):
    dump_file.close()
    print("Dumped metadata to DUMP.yaml")
else:
    print(f"Dumped {len(files)} YAML files to {args.output_folder}")
