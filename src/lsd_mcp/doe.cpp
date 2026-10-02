// lsd_doe: creates a design of experiments with LSD's own design code
// (design and sensitivity_doe in src/set_all.cpp), making the same calls as
// the sensitivity menu of LSD's interface (src/interf.cpp, cases 72, 80, 81).
//
// Usage: lsd_doe -f BASE.lsd -s BASE.sa -m MODE [options]
//   -m nolh   near-orthogonal Latin hypercube; -x for the extended table
//   -m mc     Monte Carlo range sampling; -n SAMPLES, -i FIRST file number
//   -m ee     elementary effects; -t TRAJECTORIES -p POOL -l LEVELS -j JUMP
//   -r SEED   random seed of the design (default: the SEED of the configuration,
//             which is what the interface uses)
// Writes BASE_<first>_<last>.csv and BASE_<k>.lsd into the current folder and
// prints one line: "points N first F last L".
//
// LSD's set_all.cpp is the windowed program's file: it calls Tcl in functions
// this program never reaches. Everything it needs is defined here, ahead of
// the include; the LSD file itself is used unmodified. The globals and the
// two dummies are what lsd_getlimits and lsd_confgen define for themselves.

// Tcl, only for the windowed dialogs of set_all.cpp (never called here)
typedef void Tcl_Interp;
Tcl_Interp *inter = 0;
#define TCL_LINK_INT 0
#define TCL_LINK_DOUBLE 0
#define TCL_LINK_BOOLEAN 0
#define Tcl_LinkVar( a, b, c, d )
#define Tcl_UnlinkVar( a, b )
#define Tcl_DoOneEvent( a ) 0

#include "set_all.cpp"   // includes decl.h; LSD's header has no include guard
#include "tables.h"     // the NOLH tables, which lsdmain.cpp includes in the full program

bool ignore_eq_file = true;
bool message_logged = false;
bool meta_par_in[ META_PAR_NUM ];
bool no_more_memory = false;
bool no_saved = true;
bool no_search;
bool no_zero_instance = true;
bool on_bar;
bool parallel_mode;
bool running = false;
bool save_ok = true;
bool struct_loaded = false;
bool unsavedData = false;
bool unsavedSense = false;
bool user_exception = false;
bool use_nan;
char *config_file = NULL;
char *eq_file = NULL;
char *exec_path = NULL;
char *path = NULL;
char *sens_file = NULL;
char *simul_name = NULL;
char *struct_file = NULL;
char equation_name[ MAX_PATH_LENGTH ] = "";
char lsd_eq_file[ MAX_FILE_SIZE ] = "";
char name_rep[ MAX_PATH_LENGTH ] = "";
char nonavail[ ] = "NA";
const bool no_pointer_check = false;
int actual_steps = 0;
int debug_flag = false;
int fast_mode = 1;
int findex = 1;
int findexSens = 0;
int max_step = 100;
int no_ptr_chk = false;
int parallel_disable = false;
int prof_aggr_time = false;
int prof_min_msecs = 0;
int prof_obs_only = false;
int quit = 0;
int t;
int series_saved = 0;
int sim_num = 1;
int stack;
int stack_info = 0;
int when_debug;
int wr_warn_cnt;
long nodesSerial = 1;
unsigned seed = 1;
description *descr = NULL;
lsdstack *stacklog = NULL;
object *blueprint = NULL;
object *root = NULL;
object *wait_delete = NULL;
o_setT obj_list;
sense *rsense = NULL;
variable *cemetery = NULL;
variable *last_cemetery = NULL;

const char *signal_names[ REG_SIG_NUM ] = REG_SIG_NAME;
const int signals[ REG_SIG_NUM ] = REG_SIG_CODE;

// Dummies for linking, as in lsd_getlimits; sensitivity_created only shows a
// message box in the interface (interf.cpp)
void sensitivity_created( const char *path, const char *sim_name, int findex ) { }
double variable::fun( object* r ) { return NAN; }
bool alloc_save_var( variable *v ) { return true; }

// Globals of the interface that set_all.cpp reads
int stop = false;
int choice = 0;
int choice_g = 0;

static int fail( int code, const char *message )
{
	fprintf( stderr, "lsd_doe: %s\n", message );
	return code;
}

int lsdmain( int argn, const char **argv )
{
	const char *mode = "";
	int i, extended = 0, samples = 0, first = 1;
	int trajectories = 10, pool = 100, levels = 4, jump = 2, new_seed = 0;
	FILE *f;
	design *doe;

	path = new char[ 1 ];
	strcpy( path, "" );
	// The configuration names its equation file; load_configuration keeps that
	// name only if the file exists in exec_path. The interface runs in the
	// model folder, and so does this program.
	exec_path = new char[ 2 ];
	strcpy( exec_path, "." );

	for ( i = 1; i < argn; ++i )
	{
		if ( ! strcmp( argv[ i ], "-x" ) )
		{
			extended = 1;
			continue;
		}
		if ( i + 1 >= argn )
			return fail( 2, "option without a value" );
		if ( ! strcmp( argv[ i ], "-f" ) )
		{
			struct_file = new char[ strlen( argv[ i + 1 ] ) + 1 ];
			strcpy( struct_file, argv[ i + 1 ] );
		}
		else if ( ! strcmp( argv[ i ], "-s" ) )
		{
			sens_file = new char[ strlen( argv[ i + 1 ] ) + 1 ];
			strcpy( sens_file, argv[ i + 1 ] );
		}
		else if ( ! strcmp( argv[ i ], "-m" ) )
			mode = argv[ i + 1 ];
		else if ( ! strcmp( argv[ i ], "-n" ) )
			samples = atoi( argv[ i + 1 ] );
		else if ( ! strcmp( argv[ i ], "-i" ) )
			first = atoi( argv[ i + 1 ] );
		else if ( ! strcmp( argv[ i ], "-t" ) )
			trajectories = atoi( argv[ i + 1 ] );
		else if ( ! strcmp( argv[ i ], "-p" ) )
			pool = atoi( argv[ i + 1 ] );
		else if ( ! strcmp( argv[ i ], "-l" ) )
			levels = atoi( argv[ i + 1 ] );
		else if ( ! strcmp( argv[ i ], "-j" ) )
			jump = atoi( argv[ i + 1 ] );
		else if ( ! strcmp( argv[ i ], "-r" ) )
			new_seed = atoi( argv[ i + 1 ] );
		else
			return fail( 2, "unknown option" );
		++i;
	}
	if ( struct_file == NULL || sens_file == NULL )
		return fail( 2, "usage: lsd_doe -f BASE.lsd -s BASE.sa -m nolh|mc|ee [options]" );

	f = fopen( struct_file, "r" );
	if ( f == NULL )
		return fail( 4, "configuration file not found" );
	fclose( f );

	simul_name = new char[ strlen( struct_file ) + 1 ];
	strcpy( simul_name, struct_file );
	i = strlen( simul_name );
	simul_name[ i > 4 ? i - 4 : i ] = '\0';

	root = new object;
	root->init( NULL, "Root" );
	add_description( "Root" );
	reset_blueprint( NULL );

	if ( load_configuration( true ) != 0 )
		return fail( 5, "the configuration file is invalid" );

	if ( new_seed > 0 )
		seed = ( unsigned ) new_seed;		// the design constructor starts the generator from it

	f = fopen( sens_file, "rt" );
	if ( f == NULL )
		return fail( 7, "sensitivity file not found" );
	if ( load_sensitivity( f ) != 0 )
	{
		fclose( f );
		return fail( 8, "the sensitivity file is invalid" );
	}
	fclose( f );

	if ( ! strcmp( mode, "nolh" ) )
	{
		// interf.cpp, case 72: table chosen by LSD from the number of factors
		doe = new design( rsense, 1, "", "", 1, extended ? -1 : 0, 0 );
		if ( doe->n == 0 )
			return fail( 9, "no NOLH table for this number of factors (more than 29 needs an external table)" );
		findexSens = 1;
	}
	else if ( ! strcmp( mode, "mc" ) )
	{
		// interf.cpp, case 80, with "append" when first > 1
		if ( samples < 1 )
			return fail( 2, "mc needs -n of at least 1" );
		if ( first < 1 )
			return fail( 2, "-i must be at least 1" );
		findexSens = first;
		doe = new design( rsense, 2, "", "", findexSens, samples );
		if ( doe->n == 0 )
			return fail( 9, "could not create the Monte Carlo design" );
	}
	else if ( ! strcmp( mode, "ee" ) )
	{
		// interf.cpp, case 81, with the same validity check
		if ( levels < 2 || levels % 2 != 0 || trajectories < 2 || pool < trajectories || jump < 1 )
			return fail( 2, "invalid elementary effects settings (levels must be even and at least 2, trajectories at least 2, pool at least trajectories, jump at least 1)" );
		findexSens = 1;
		doe = new design( rsense, 3, "", "", findexSens, pool, levels, jump, trajectories );
		if ( doe->n == 0 )
			return fail( 9, "could not create the elementary effects design" );
	}
	else
		return fail( 2, "mode must be nolh, mc or ee" );

	int points = doe->n, begin = findexSens;
	sensitivity_doe( &findexSens, doe, "" );
	printf( "points %d first %d last %d\n", points, begin, findexSens - 1 );
	return findexSens - begin == points ? 0 : 10;
}
