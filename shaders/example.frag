#version 430
in INPUT
{
	vec4	Color;
} IN;
out vec4 OutColor;
void main()
{
	if( IN.Color.w == 0.0 )		discard;
	OutColor = IN.Color;
}
