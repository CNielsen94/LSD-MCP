// Exponential decay: X(t) = r * X(t-1). The initial value of X (its first lag)
// and the parameter r both change the mean of X, so both matter for a sensitivity analysis.
#include "fun_head_fast.h"

MODELBEGIN

EQUATION( "X" )
RESULT( V( "r" ) * VL( "X", 1 ) )

MODELEND

void close_sim( void )
{
}
