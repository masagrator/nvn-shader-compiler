#version 430
layout(triangles) in;
layout(triangle_strip, max_vertices = 3) out;

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
	for (int i = 0; i < 3; ++i)
	{
		gl_Position = gl_in[i].gl_Position;
		OUT.Color = IN[i].Color;
		EmitVertex();
	}
	EndPrimitive();
}
