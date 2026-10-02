// Globals the LSD 8.1 engine expects from lsdmain.cpp; the command-line
// utilities do not define them.
#include "decl.h"
bool no_search_up;
bool watch_trigger = false;
bool watch_write_mode;
char watch_elem[ MAX_ELEM_LENGTH + 1 ] = "";
int stack_level;
