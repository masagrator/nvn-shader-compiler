#version 430
layout(triangles, equal_spacing, ccw) in;

in gl_PerVertex
{
	vec4 gl_Position;
} gl_in[];

out gl_PerVertex
{
	vec4 gl_Position;
};

in INPUT
{
	vec4	Color;
} IN[];

out INPUT
{
	vec4	Color;
} OUT;

void main()
{
	gl_Position = gl_TessCoord.x * gl_in[0].gl_Position +
	              gl_TessCoord.y * gl_in[1].gl_Position +
	              gl_TessCoord.z * gl_in[2].gl_Position;

	OUT.Color = gl_TessCoord.x * IN[0].Color +
	            gl_TessCoord.y * IN[1].Color +
	            gl_TessCoord.z * IN[2].Color;
}
