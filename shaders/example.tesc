#version 430
layout(vertices = 3) out;

in gl_PerVertex
{
	vec4 gl_Position;
} gl_in[];

out gl_PerVertex
{
	vec4 gl_Position;
} gl_out[];

in INPUT
{
	vec4	Color;
} IN[];

out INPUT
{
	vec4	Color;
} OUT[];

void main()
{
	gl_out[gl_InvocationID].gl_Position = gl_in[gl_InvocationID].gl_Position;
	OUT[gl_InvocationID].Color = IN[gl_InvocationID].Color;

	if (gl_InvocationID == 0)
	{
		gl_TessLevelInner[0] = 1.0;
		gl_TessLevelOuter[0] = 1.0;
		gl_TessLevelOuter[1] = 1.0;
		gl_TessLevelOuter[2] = 1.0;
	}
}
