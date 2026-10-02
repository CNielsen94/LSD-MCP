// Y = a - b + noise: a stochastic test model.
#include "fun_head_fast.h"

MODELBEGIN

EQUATION( "Y" )
RESULT( V( "a" ) - V( "b" ) + norm( 0, 0.1 ) )

MODELEND

void close_sim( void )
{
}
