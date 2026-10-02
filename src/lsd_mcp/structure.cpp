// lsd_edit: edits the structure of an LSD configuration with LSD's own code.
//
// Usage: lsd_edit (-f IN.lsd | -n) -o OUTBASE -e EXECDIR -x OPERATIONS
//   -f IN.lsd      configuration to load with LSD's loader
//   -n             start from an empty Root instead
//   -o OUTBASE     LSD's save_configuration writes OUTBASE.lsd (current folder)
//   -e EXECDIR     folder holding the equation file (load_configuration keeps
//                  the EQUATION name only if the file exists there)
//   -x OPERATIONS  text file, one operation per line, fields separated by tabs:
//                  the operation name, then key=value pairs. In a value, \t, \n
//                  and \\ stand for tab, newline and backslash; lists are
//                  comma separated numbers.
// All operations are applied in memory; the file is written only if every one
// succeeded. On error nothing is written, the message names the operation
// (number and name) and the exit status is 1.
//
// Each operation makes the calls LSD's interface makes (src/interf.cpp, the
// model structure menu); the cases are named in the comments below.

#include "decl.h"
#include "lsd_globals.h"

#include <map>
#include <stdexcept>
#include <string>
#include <vector>

typedef std::map < std::string, std::string > Fields;

// --- reading the operations -------------------------------------------------

static std::string unescape( const std::string &text )
{
	std::string out;
	for ( size_t i = 0; i < text.size( ); ++i )
	{
		if ( text[ i ] == '\\' && i + 1 < text.size( ) )
		{
			++i;
			out += text[ i ] == 't' ? '\t' : text[ i ] == 'n' ? '\n' : text[ i ];
		}
		else
			out += text[ i ];
	}
	return out;
}

static std::vector < Fields > read_operations( const char *file_name )
{
	std::vector < Fields > operations;
	std::string line;
	int c;
	FILE *f = fopen( file_name, "rb" );

	if ( f == NULL )
		throw std::runtime_error( "cannot read the operations file" );

	for ( ; ; )
	{
		c = fgetc( f );
		if ( c != EOF && c != '\n' )
		{
			line += ( char ) c;
			continue;
		}
		if ( line.size( ) > 0 )
		{
			Fields fields;
			size_t start = 0, end;
			bool first = true;
			do
			{
				end = line.find( '\t', start );
				std::string part = line.substr( start, end == std::string::npos ? end : end - start );
				if ( first )
					fields[ "op" ] = part;
				else
				{
					size_t eq = part.find( '=' );
					fields[ part.substr( 0, eq ) ] = unescape( eq == std::string::npos ? "" : part.substr( eq + 1 ) );
				}
				first = false;
				start = end + 1;
			}
			while ( end != std::string::npos );
			operations.push_back( fields );
		}
		line = "";
		if ( c == EOF )
			break;
	}

	fclose( f );
	return operations;
}

static bool has( const Fields &fields, const char *key )
{
	return fields.find( key ) != fields.end( );
}

static std::string need( const Fields &fields, const char *key )
{
	Fields::const_iterator it = fields.find( key );
	if ( it == fields.end( ) )
		throw std::runtime_error( std::string( "missing field " ) + key );
	return it->second;
}

static int whole_number( const std::string &text, const char *key )
{
	char *end;
	long value = strtol( text.c_str( ), &end, 10 );
	if ( text.empty( ) || *end != '\0' )
		throw std::runtime_error( std::string( key ) + " must be a whole number" );
	return ( int ) value;
}

static double number( const std::string &text, const char *key )
{
	char *end;
	double value = strtod( text.c_str( ), &end );
	if ( text.empty( ) || *end != '\0' )
		throw std::runtime_error( std::string( key ) + " must be a number" );
	return value;
}

static std::vector < double > number_list( const std::string &text, const char *key )
{
	std::vector < double > values;
	size_t start = 0, end;
	do
	{
		end = text.find( ',', start );
		values.push_back( number( text.substr( start, end == std::string::npos ? end : end - start ), key ) );
		start = end + 1;
	}
	while ( end != std::string::npos );
	return values;
}

// --- finding things and checking names --------------------------------------

static object *find_object( const std::string &name )
{
	return root->search( name.c_str( ) );
}

static variable *find_element( const std::string &name )
{
	return root->search_var( NULL, name.c_str( ), true );
}

// the rule of LSD's valid_label (common.cpp): ^[a-zA-Z_][a-zA-Z0-9_]*$
static bool valid_name( const std::string &name )
{
	for ( size_t i = 0; i < name.size( ); ++i )
	{
		char c = name[ i ];
		bool letter = ( c >= 'a' && c <= 'z' ) || ( c >= 'A' && c <= 'Z' ) || c == '_';
		if ( ! letter && ! ( i > 0 && c >= '0' && c <= '9' ) )
			return false;
	}
	return name.size( ) > 0;
}

// check_label (interf.cpp, minus valid_label): is the label used by r, one of
// its elements or one of its descendants?
static bool name_taken( const char *lab, object *r )
{
	bridge *cb;
	object *cur;
	variable *cv;

	if ( ! strcmp( lab, r->label ) )
		return true;

	for ( cv = r->v; cv != NULL; cv = cv->next )
		if ( ! strcmp( lab, cv->label ) )
			return true;

	for ( cb = r->b; cb != NULL; cb = cb->next )
	{
		cur = cb->head == NULL ? blueprint->search( cb->blabel ) : cb->head;
		if ( name_taken( lab, cur ) )
			return true;
	}

	return false;
}

// Objects and elements share one name space in the whole model (interf.cpp,
// cases 2, 3 and 83: check_label on the root, with LSD's two messages).
static void check_new_name( const std::string &name )
{
	if ( ! valid_name( name ) )
		throw std::runtime_error( "invalid name '" + name + "': names must begin with a letter (English "
								  "alphabet) or underscore ('_') and may contain letters, numbers or '_' "
								  "but no spaces or other characters" );
	if ( name.size( ) >= MAX_ELEM_LENGTH )
		throw std::runtime_error( "name '" + name + "' is too long" );
	if ( name_taken( name.c_str( ), root ) )
		throw std::runtime_error( "the name '" + name + "' already exists in the model" );
}

// descriptions are changed through char buffers: LSD edits the text it is given
static std::vector < char > buffer( const std::string &text )
{
	return std::vector < char > ( text.c_str( ), text.c_str( ) + text.size( ) + 1 );
}

// --- the operations ---------------------------------------------------------

// number of instances of the object under every parent instance, as
// chg_obj_num (edit.cpp) does it from the interface (interf.cpp, case 6 ->
// number of instances) for the whole model: new instances copy the first
// instance (values and descendants), extra ones are removed from the end.
// chg_obj_num itself asks which instances to remove in a dialog, so its loop
// is repeated here.
static void change_instances( const std::string &name, int value )
{
	int i, num;
	object *cur, *cur1, *cur2, *first, *last;

	first = find_object( name );
	for ( cur = first; cur != NULL; )
	{
		skip_next_obj( cur, & num );
		if ( num <= value )
			cur->up->add_n_objects2( first->label, value - num, first );
		else
		{
			for ( i = 1, cur1 = cur; i < value; ++i, cur1 = cur1->next );
			while ( go_brother( cur1 ) != NULL )
			{
				cur2 = cur1->next->next;
				cur1->next->delete_obj( );
				cur1->next = cur2;
			}
		}

		for ( last = NULL, cur1 = cur; cur1 != NULL; cur1 = go_brother( cur1 ) )
			last = cur1;
		cur = last == NULL ? NULL : last->hyper_next( cur->label );
	}
}

// interf.cpp, case 3: add_obj under every instance of the parent, then its description
static void add_object( const Fields &f )
{
	std::string parent = need( f, "parent" ), name = need( f, "name" );
	int instances = has( f, "instances" ) ? whole_number( f.at( "instances" ), "instances" ) : 1;
	object *p = find_object( parent );

	if ( p == NULL )
		throw std::runtime_error( "unknown object '" + parent + "'" );
	if ( instances < 1 )
		throw std::runtime_error( "instances must be at least 1" );
	check_new_name( name );

	p->add_obj( name.c_str( ), 1, 1 );
	std::vector < char > text = buffer( "" );
	add_description( name.c_str( ), 4, & text[ 0 ] );
	if ( instances > 1 )
		change_instances( name, instances );
}

// interf.cpp, case 2: add_empty_var in every instance of the object, then the
// fields the interface sets, then the description. param: 0 variable,
// 1 parameter, 2 function.
static void add_element( const Fields &f, int param )
{
	std::string object_name = need( f, "object" ), name = need( f, "name" );
	int lags = param == 0 && has( f, "lags" ) ? whole_number( f.at( "lags" ), "lags" ) : 0;
	bool saved = has( f, "saved" ) && f.at( "saved" ) == "1";
	std::vector < double > initial;
	object *o = find_object( object_name ), *cur;
	variable *cv;
	int i;

	if ( o == NULL )
		throw std::runtime_error( "unknown object '" + object_name + "'" );
	if ( lags < 0 )
		throw std::runtime_error( "lags must be 0 or more" );
	const char *value_key = param == 1 ? "value" : "initial";
	if ( has( f, value_key ) )
	{
		initial = number_list( f.at( value_key ), value_key );
		if ( param == 0 && lags == 0 )
			throw std::runtime_error( "a variable with no lags has no initial value" );
		if ( param == 1 && initial.size( ) != 1 )
			throw std::runtime_error( "a parameter takes one value" );
		if ( param == 0 && ( int ) initial.size( ) != 1 && ( int ) initial.size( ) != lags )
			throw std::runtime_error( "initial needs one number or one per lag (lags is " + std::to_string( lags ) + ")" );
	}
	check_new_name( name );

	for ( cur = o; cur != NULL; cur = cur->hyper_next( cur->label ) )
	{
		cv = cur->add_empty_var( name.c_str( ) );
		cv->val = new double[ lags + 1 ];
		cv->save = saved ? 1 : 0;
		cv->param = param;
		cv->num_lag = lags;
		cv->deb_mode = 'n';
		cv->data_loaded = '+';
		for ( i = 0; i < lags + 1; ++i )
			cv->val[ i ] = 0;
		for ( i = 0; i < ( int ) initial.size( ) && i < lags + ( param == 1 ? 1 : 0 ); ++i )
			cv->val[ i ] = initial[ i ];
		if ( param == 0 && initial.size( ) == 1 )		// one number for all lags
			for ( i = 1; i < lags; ++i )
				cv->val[ i ] = initial[ 0 ];
	}

	std::vector < char > text = buffer( "" );
	add_description( name.c_str( ), param, & text[ 0 ] );
}

// description entries of an object, its elements and its descendants
// (the interface's wipe_out removes only the first two)
static void forget_descriptions( object *d )
{
	bridge *cb;
	variable *cv;

	change_description( d->label );
	for ( cv = d->v; cv != NULL; cv = cv->next )
		change_description( cv->label );
	for ( cb = d->b; cb != NULL; cb = cb->next )
		if ( cb->head != NULL )
			forget_descriptions( cb->head );
}

// wipe_out (interf.cpp): every instance in turn, then its bridge
void wipe_out( object *d )
{
	object *cur = d->hyper_next( d->label );
	if ( cur != NULL )
		wipe_out( cur );
	delete_bridge( d );
}

// interf.cpp, cases 83 and 7 (rename)
static void rename_thing( const Fields &f )
{
	std::string name = need( f, "name" ), new_name = need( f, "new_name" );
	object *o = find_object( name ), *cur;
	variable *cv = find_element( name );

	if ( o == NULL && cv == NULL )
		throw std::runtime_error( "unknown object or element '" + name + "'" );
	if ( o != NULL && o == root )
		throw std::runtime_error( "Root cannot be renamed" );
	check_new_name( new_name );

	change_description( name.c_str( ), new_name.c_str( ) );
	if ( o != NULL )
		o->chg_lab( new_name.c_str( ) );
	else
		for ( cur = cv->up; cur != NULL; cur = cur->hyper_next( cur->label ) )
			cur->chg_var_lab( name.c_str( ), new_name.c_str( ) );
}

// interf.cpp, case 74 (delete object: wipe_out) and case 76 (delete element:
// delete_var in every instance of its object)
static void delete_thing( const Fields &f )
{
	std::string name = need( f, "name" );
	bool force = has( f, "force" ) && f.at( "force" ) == "1";
	object *o = find_object( name ), *cur;
	variable *cv = find_element( name );
	int elements = 0, children = 0;
	bridge *cb;
	variable *v;

	if ( o == NULL && cv == NULL )
		throw std::runtime_error( "unknown object or element '" + name + "'" );
	if ( o != NULL )
	{
		if ( o == root )
			throw std::runtime_error( "Root cannot be deleted" );
		for ( v = o->v; v != NULL; v = v->next )
			++elements;
		for ( cb = o->b; cb != NULL; cb = cb->next )
			++children;
		if ( ( elements > 0 || children > 0 ) && ! force )
			throw std::runtime_error( "object '" + name + "' still contains " + std::to_string( elements ) +
									  " element(s) and " + std::to_string( children ) +
									  " child object(s); give \"force\": true to delete them with it" );
		forget_descriptions( o );
		wipe_out( o );
	}
	else
	{
		change_description( name.c_str( ) );
		for ( cur = cv->up; cur != NULL; cur = cur->hyper_next( cur->label ) )
			cur->delete_var( name.c_str( ) );
	}
}

static void set_instances( const Fields &f )
{
	std::string name = need( f, "object" );
	int value = whole_number( need( f, "instances" ), "instances" );
	object *o = find_object( name );

	if ( o == NULL )
		throw std::runtime_error( "unknown object '" + name + "'" );
	if ( o == root )
		throw std::runtime_error( "Root always has one instance" );
	if ( value < 1 )
		throw std::runtime_error( "instances must be at least 1 (delete the object to remove it)" );
	change_instances( name, value );
}

// interf.cpp, cases 77 (initial values): the value of a parameter is val[ 0 ],
// the initial value of the k-th lag of a variable is val[ k - 1 ], in every
// instance of the element's object
static void set_instance_values( const Fields &f )
{
	std::string name = need( f, "name" );
	std::vector < double > values = number_list( need( f, "values" ), "values" );
	variable *cv = find_element( name );
	object *cur;
	int lag = has( f, "lag" ) ? whole_number( f.at( "lag" ), "lag" ) : 1, count = 0, i;

	if ( cv == NULL )
		throw std::runtime_error( "unknown element '" + name + "'" );
	if ( cv->param == 2 )
		throw std::runtime_error( "'" + name + "' is a function" );
	if ( cv->param == 1 && has( f, "lag" ) )
		throw std::runtime_error( "'" + name + "' is a parameter, which has no lags" );
	if ( cv->param == 0 && cv->num_lag == 0 )
		throw std::runtime_error( "variable '" + name + "' has no lags, so no initial values" );
	if ( lag < 1 || ( cv->param == 0 && lag > cv->num_lag ) )
		throw std::runtime_error( "variable '" + name + "' has " + std::to_string( cv->num_lag ) + " lag(s)" );

	for ( cur = cv->up; cur != NULL; cur = cur->hyper_next( cur->label ) )
		++count;
	if ( ( int ) values.size( ) != count )
		throw std::runtime_error( "'" + name + "' has " + std::to_string( count ) + " instance(s) but " +
								  std::to_string( values.size( ) ) + " value(s) were given" );

	for ( i = 0, cur = cv->up; cur != NULL; ++i, cur = cur->hyper_next( cur->label ) )
	{
		variable *each = cur->search_var( NULL, name.c_str( ), true, true );
		each->val[ lag - 1 ] = values[ i ];
		each->data_loaded = '+';
	}
}

// add_description / change_description (util.cpp), as the interface's
// description dialogs do
static void describe( const Fields &f )
{
	std::string name = need( f, "name" );
	std::vector < char > text = buffer( need( f, "text" ) );
	object *o = find_object( name );
	variable *cv = find_element( name );

	if ( o == NULL && cv == NULL )
		throw std::runtime_error( "unknown object or element '" + name + "'" );
	if ( search_description( name.c_str( ), false ) != NULL )
		change_description( name.c_str( ), NULL, -1, & text[ 0 ] );
	else
		add_description( name.c_str( ), o != NULL ? 4 : cv->param, & text[ 0 ] );
}

static void apply( const Fields &f )
{
	std::string op = need( f, "op" );

	if ( op == "add_object" )
		add_object( f );
	else if ( op == "add_parameter" )
		add_element( f, 1 );
	else if ( op == "add_variable" )
		add_element( f, 0 );
	else if ( op == "add_function" )
		add_element( f, 2 );
	else if ( op == "rename" )
		rename_thing( f );
	else if ( op == "delete" )
		delete_thing( f );
	else if ( op == "set_instances" )
		set_instances( f );
	else if ( op == "set_instance_values" )
		set_instance_values( f );
	else if ( op == "describe" )
		describe( f );
	else
		throw std::runtime_error( "unknown operation '" + op + "'" );
}

// --- main -------------------------------------------------------------------

static int fail( int code, const std::string &message )
{
	fprintf( stderr, "%s\n", message.c_str( ) );
	return code;
}

int lsdmain( int argn, const char **argv )
{
	const char *in_file = NULL, *out_base = NULL, *exec_dir = ".", *operations_file = NULL;
	bool empty = false;
	int i;
	FILE *f;

	for ( i = 1; i < argn; ++i )
	{
		if ( ! strcmp( argv[ i ], "-n" ) )
			empty = true;
		else if ( i + 1 >= argn )
			return fail( 2, "option without a value" );
		else if ( ! strcmp( argv[ i ], "-f" ) )
			in_file = argv[ ++i ];
		else if ( ! strcmp( argv[ i ], "-o" ) )
			out_base = argv[ ++i ];
		else if ( ! strcmp( argv[ i ], "-e" ) )
			exec_dir = argv[ ++i ];
		else if ( ! strcmp( argv[ i ], "-x" ) )
			operations_file = argv[ ++i ];
		else
			return fail( 2, "unknown option" );
	}
	if ( ( in_file != NULL ) == empty || out_base == NULL || operations_file == NULL )
		return fail( 2, "usage: lsd_edit (-f IN.lsd | -n) -o OUTBASE -e EXECDIR -x OPERATIONS" );

	path = new char[ 1 ];
	strcpy( path, "" );
	exec_path = new char[ strlen( exec_dir ) + 1 ];
	strcpy( exec_path, exec_dir );
	simul_name = new char[ strlen( out_base ) + 1 ];
	strcpy( simul_name, out_base );

	root = new object;
	root->init( NULL, "Root" );
	add_description( "Root" );
	reset_blueprint( NULL );

	if ( ! empty )
	{
		f = fopen( in_file, "r" );
		if ( f == NULL )
			return fail( 4, "configuration file not found" );
		fclose( f );
		struct_file = new char[ strlen( in_file ) + 1 ];
		strcpy( struct_file, in_file );
		if ( load_configuration( true ) != 0 )
			return fail( 5, "LSD cannot load the configuration file" );
	}

	std::vector < Fields > operations;
	try
	{
		operations = read_operations( operations_file );
	}
	catch ( std::exception &e )
	{
		return fail( 2, e.what( ) );
	}

	for ( i = 0; i < ( int ) operations.size( ); ++i )
	{
		try
		{
			apply( operations[ i ] );
			reset_blueprint( root );		// as the interface does after changing the structure
		}
		catch ( std::exception &e )
		{
			return fail( 1, "operation " + std::to_string( i + 1 ) + " (" + operations[ i ][ "op" ] + "): " + e.what( ) );
		}
	}

	if ( ! save_configuration( 0, "", false ) )
		return fail( 3, "LSD cannot save the configuration" );
	return 0;
}
