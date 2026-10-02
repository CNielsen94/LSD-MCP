# Meta-model sensitivity analysis with LSD's own R package (LSDsensitivity).
# Follows Rpkg/Example/kriging-sobol-SA.R and poly-sobol-SA.R.
#
# Usage: Rscript sa_analysis.R FOLDER BASENAME VARIABLE METAMODEL INIDROP NKEEP
#                              DOEFILE VALIDFILE OUTFOLDER RSEED
# FOLDER, DOEFILE, VALIDFILE and OUTFOLDER are full paths. METAMODEL is
# "kriging" or "polynomial". RSEED seeds R's random numbers (set.seed).
# Results go to OUTFOLDER as fit.csv and sobol.csv;
# on failure OUTFOLDER/error.txt holds the message and the exit status is 1.

args <- commandArgs( trailingOnly = TRUE )
if( length( args ) != 10 ) {
  cat( "usage: sa_analysis.R FOLDER BASENAME VARIABLE METAMODEL INIDROP NKEEP DOEFILE VALIDFILE OUTFOLDER RSEED\n" )
  quit( status = 2 )
}

folder    <- args[ 1 ]
baseName  <- args[ 2 ]
variable  <- args[ 3 ]
metamodel <- args[ 4 ]
iniDrop   <- as.integer( args[ 5 ] )
nKeep     <- as.integer( args[ 6 ] )
doeFile   <- args[ 7 ]
validFile <- args[ 8 ]
outFolder <- args[ 9 ]
rSeed     <- as.integer( args[ 10 ] )

dir.create( outFolder, showWarnings = FALSE, recursive = TRUE )
errorFile <- file.path( outFolder, "error.txt" )
if( file.exists( errorFile ) )
  file.remove( errorFile )

fail <- function( message ) {
  writeLines( message, errorFile )
  cat( message, "\n" )
  quit( status = 1 )
}

if( ! requireNamespace( "LSDsensitivity", quietly = TRUE ) )
  fail( "R package LSDsensitivity is not installed" )

# R turns an LSD name such as "_s" into the column name "X_s", but LSDsensitivity
# then looks the column up under the LSD name "_s" (write.response() in
# Rpkg/LSDsensitivity/R/write_resp.R). This hook, which read.doe.lsd() calls on
# the data before that lookup, gives such columns back their LSD name.
restore.names <- function( dataSet, allVars ) {
  colnames( dataSet ) <- sub( "^X_", "_", colnames( dataSet ) )
  return( dataSet )
}

result <- tryCatch( {
  library( LSDsensitivity )
  set.seed( rSeed )

  dataSet <- read.doe.lsd( folder, baseName, variable,
                           does = 2,
                           doeFile = doeFile,
                           validFile = validFile,
                           iniDrop = iniDrop,
                           nKeep = nKeep,
                           saveVars = variable,
                           eval.vars = restore.names )

  response <- dataSet$resp[ , 1 ]
  if( length( response ) < 2 || isTRUE( all( is.na( response ) ) ) ||
      isTRUE( stats::var( response, na.rm = TRUE ) == 0 ) )
    stop( paste( "The response does not vary over the design (it is constant across",
                 "the design points), so there is nothing to analyse" ) )

  if( metamodel == "polynomial" ) {
    model <- polynomial.model.lsd( dataSet )
    quality <- model$R2
    qualityName <- "R2"
  } else {
    model <- kriging.model.lsd( dataSet )
    quality <- model$Q2
    qualityName <- "Q2"
  }

  sa <- sobol.decomposition.lsd( dataSet, model )

  fit <- data.frame( metric = qualityName, value = quality )
  write.csv( fit, file.path( outFolder, "fit.csv" ), row.names = FALSE )

  table <- data.frame( factor = rownames( sa$sa ),
                       direct = sa$sa[ , 1 ],
                       interactions = sa$sa[ , 2 ],
                       stringsAsFactors = FALSE )
  write.csv( table, file.path( outFolder, "sobol.csv" ), row.names = FALSE )
  TRUE
}, error = function( e ) {
  conditionMessage( e )
} )

if( ! isTRUE( result ) )
  fail( paste( "R analysis failed:", result ) )
