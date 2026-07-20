#version 430
out gl_PerVertex
{
	vec4 gl_Position;
};
out INPUT
{
	vec4	Color;
} OUT;
void main()
{
	gl_Position = vec4(0.0, 0.0, 0.0, 1.0);
	OUT.Color = vec4(1.0, 1.0, 1.0, 1.0);
}
