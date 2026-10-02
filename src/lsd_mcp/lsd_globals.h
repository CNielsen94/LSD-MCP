// Definitions every LSD command-line program has to supply for the engine
// objects it links (the same ones lsd_getlimits and lsd_confgen define for
// themselves). Included once, after decl.h, by doe.cpp and structure.cpp.

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

// Dummies for linking, as in lsd_getlimits
double variable::fun( object* r ) { return NAN; }
bool alloc_save_var( variable *v ) { return true; }

