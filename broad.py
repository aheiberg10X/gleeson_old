import globes

import re
import os

COLUMN_MAP  =       {"chrom" :  0,
                     "pos" :    1,
                     "dbSNP" :  2,
                     "ref" :    3,
                     "mut" :    4,
                     "qual" :   5,
                     "filter" : 6,
                     "info"   : 7,
                     "format" : 8}

#COLUMN_CAST = {"chrom" : str,
               #"pos" : int,
               #"dbSNP" : str,
               #"ref" : str,
               #"mut

CALL_START = len(COLUMN_MAP)

CALL_MAP = {"GT" : 0,
            "AD" : 1,
            "DP" : 2,
            "GQ" : 3,
            "PL" : 4}



#in Broad file, there can be multiple variations @ the same position
#they get smashed into same column, delimited by ','
#each family can only have one variant at the locus
#0/1 means het for variant 1, 2/2 means homozygous for variant 2, etc.
#This function all the calls, and returns what their zygosity for 
#that particular variant
#i.e If the family is 0/1, they are 0/0 for variant_ix=2
def makeMultiCallsSpecific( multi_calls, variant_ix ) :
    indexOf = CALL_MAP
    calls_for_variation = []
    someone_has_variant = False
    for call in multi_calls :
        call_splt = splitCall(call)
        GT = convertGT( call_splt, variant_ix )
        if isMutated( GT ) : someone_has_variant = True
        call_splt[ indexOf["GT"] ] = encodeGT(GT)
        call = ':'.join( call_splt )
        calls_for_variation.append( call )

    if someone_has_variant : return calls_for_variation
    else : return [] 

#filter_non_passes : Broad applys filters to each variant. If anyone finds
#the variant to be of low quality or otherwise suspicious, it marks it something
#that != 'PASS'
def separateSNPSandINDELS( ) :
    fin = open( "%s/PlateIII/Gleeson_B3_110908_105_Samples.vcf" % globes.ROOT_DIR ) 
    #fin = open( '%s/raw_data/SUBSET.vcf' % (globes.DATA_DIR) )
    fsnp = open( "%s/PlateIII/plateIII_snps.vcf" % globes.ROOT_DIR, 'wb' )
    findel = open( "%s/PlateIII/plateIII_indels.vcf" % globes.ROOT_DIR, 'wb' )
    fnotrepped = open( "%s/PlateIII/not_repped.tsv" % (globes.ROOT_DIR), 'wb' )

    indexOf = COLUMN_MAP

    #fast-forward to 
    (patients,headers) = getColumnsAndHeaders( fin )
    header_string = '\n'.join(headers)
    patients_string = '\t'.join(patients)
    fsnp.write( "%s\n%s\n" % (header_string, patients_string ) )
    findel.write( "%s\n%s\n" % (header_string, patients_string) )
    variants_not_repped = []

    lines_written = 0
    for dataline in fin :
        splt = dataline.strip().split('\t')
        col_keys = ['chrom','pos','ref','mut', 'info']
        (chrom,pos,ref,muts,info) = [ splt[ indexOf[k] ] for k in col_keys ]
        calls_splt = splt[ CALL_START: ]

        #if filter_non_passes and splt[ indexOf["filter"] ] != "PASS" : continue

        dinfo = makeInfoDict( info )
        keys = ("AC","AF")
        (ACs,AFs) = [ dinfo[key].split(',') for key in keys ]
        muts = muts.split(',')

        #IT IS CRUCIAL THAT VARIANTS LOCATED AT THE SAME CHROM,LOC
        #ARE OUTPUT SORTED BY REF,MUT.  When one of the two output files
        #is turned into an iterator to compare with a sql iterator,
        #they need to be sorted in exactly the same manner. 
        #From inspection, there is no repeat of a position, and multiple alts
        # on the same line are never of the same type.  But in theory this could
        # happen, code here NEEDS TO DEAL WITH IT.

        for variant_ix,z in enumerate( zip(muts,ACs,AFs) ) :
            (mut,ac,af) = z
            #The original columns of splt. Cloned because some values need 
            #to be changed before writing (for instance as we break up 
            #multi-valued columns up by variant)
            splt_copy = list( splt[:CALL_START] )
            dinfo_copy = dict(dinfo)
            dinfo_copy["AC"] = ac
            dinfo_copy["AF"] = af

            #see makeMultiCallsSpecific for explanation
            calls_for_variation = makeMultiCallsSpecific( calls_splt, \
                                                          variant_ix+1 )
            someone_has_variant = len(calls_for_variation) > 0
            if someone_has_variant :
                isSNP = len(ref) == len(mut)
                if isSNP :
                    #figure out where ref and mut are different
                    #a cursory inspect suggests this is unnecessary, but hey
                    diffix = -1
                    for i in range(len(ref)) :
                        if not mut[i] == ref[i] :
                            diffix = i
                            break
                    assert not diffix == -1

                    #add that offset to the reported pos 
                    splt_copy[ indexOf["pos"] ] = \
                            str( int(splt[ indexOf["pos"] ]) + diffix )

                    #get rid of allele fluff
                    splt_copy[ indexOf["ref"] ] = ref[diffix]
                    splt_copy[ indexOf["mut"] ] = mut[diffix]
                    writer = fsnp

                else :
                    splt_copy[ indexOf["mut"] ] = mut
                    writer = findel

                info_string = infoDictToString( dinfo_copy )
                splt_copy[ indexOf["info"] ] = info_string

                new_splt = splt_copy + calls_for_variation
                writer.write( "%s\n" % '\t'.join(new_splt) )
                lines_written += 1

            else :
                fnotrepped.write( "%s\t%s\t%s\n" \
                                    % (chrom,pos,variant_ix) )


    fnotrepped.close()
    findel.close()
    fsnp.close()
    fin.close()
    print lines_written

def sanitizePatientName( family_name ) :
    return family_name.replace('/','|')

#orig_filename = broad vcf file
#outdir: where the output is going
#cols_to_use: Which data are we going to include about each call?
#           List of column indexes, 0-indexed, defaults to everything
#family groups : this is where specific family splits are specified
#                should look like: {groupName1 : ("fam1","fam2"),
#                                   groupName2 : ("fam3"), etc}
#                every groups will get its own file containing just the 
#                calls for the fams specified
#callToString: a function that takes call data and parses it to return
#              whatever.  Like turning "0/0:123:45:etc" -> "AA" for 
#              homozygosity mapper.
#lineFilter : take a dataline split and return whether we are 
#                      interested in printing it
#see breakIntoFamilyFiles and hommap.makeInput for examples 
def pickOutFamilies( orig_filename, outdir,family_groups, \
                     callToString = lambda x:x,\
                     lineFilter = lambda x:True, \
                     cols_to_use = range(len(COLUMN_MAP)), \
                   ) :
    fin = open( orig_filename, "rb" )

    #open filehandles for each group, initialize group's column index list
    if not os.path.isdir(outdir) : os.mkdir(outdir)
    fouts = {}
    groupIXs = {}
    for group in family_groups :
        safe_group = sanitizePatientName( group )
        fouts[group] = open( "%s/%s.vcf" % (outdir,safe_group), 'wb' )
        groupIXs[group] = []

    (columns,headers) = getColumnsAndHeaders( fin )

    header_string = "\n".join(headers)

    for i in range( len(columns) ) :
        for group in family_groups :
            family_names = family_groups[group]
            print columns[i], family_names
            if columns[i] in family_names :
                groupIXs[group].append( i )

    print groupIXs

    #print headers
    for group in family_groups :
        fouts[group].write( "%s\n" % header_string )
        out_header = '\t'.join( [columns[i] for i in \
                                 cols_to_use + groupIXs[group]] )
        fouts[group].write( "%s\n" % out_header )
        #fouts[group].write( '\n'.join(columns) )
        #fouts[group].write( "%s\n" % out_header )

    # 'indexOf' dictionary maps header string to it's column index 
    #in the input file
    indexOf = COLUMN_MAP
    globes.printColumnWarning( orig_filename, indexOf )

    # process the data lines
    for dataline in fin.readlines() :
        splt = dataline.strip().split('\t')
        if lineFilter( splt ) :
            data = [splt[ix] for ix in cols_to_use]
            for group in family_groups :
                calls = [ callToString(splt[ix]) for ix in groupIXs[group] ]
                string = "%s\t%s\n" % ( '\t'.join(data), '\t'.join(calls))
                fouts[group].write( string )

    #close filehandles for each group
    for group in fouts :
        fouts[group].close()

    fin.close()

def breakIntoFamilyFiles( orig_filename, outdir ) :
    fin = open( orig_filename )
    #raise back to upper...
    patients = [p.upper() for p in getPatients( fin )]
    family_groups = {}
    for patient in patients :
        family_groups[patient] = (patient)
    fin.close()

    if not os.path.isdir( outdir ) : os.mkdir( outdir ) 
    pickOutFamilies( orig_filename, outdir, family_groups )

#######################################################################
#############    Helper Classes    ####################################
#######################################################################


#######################################################################
#############     Helper Funcs  #######################################
#######################################################################

#returns the list of patients
#also, advances broad_fh to the spot where data lines start
def getColumnsAndHeaders( broad_fh, toFind = "CHROM" ) :
    #skip all the header files
    headers = []
    while True :
        line = broad_fh.readline().strip()
        lineIsHeader = toFind not in line #line.find( toFind ) == -1
        if not lineIsHeader : break
        else : headers.append( line )

    #process the header line w/ header colums
    columns = line.strip().split("\t")
    return (columns, headers)

def getPatients( broad_fh ) :
    columns = getColumnsAndHeaders(broad_fh)[0]
    return [sanitizePatientName(p) for p in columns[CALL_START:]]

#strip of transcript means to ignore num part of: 'att_1, att_2, etc'
#will throw an exception if all values are not the same
def makeInfoDict( info, strip_of_transcript = False) :
    dinfo = {}
    for kv in info.split(';') :
        if kv.startswith("refseq") : continue
        try :
            (key,value) = kv.split('=')
        except ValueError :
            key,value = kv,""

        if strip_of_transcript :
            try :
                (key,transcript_ix) = key.split('_')
            except ValueError :
                pass
            if key in dinfo :
                if not dinfo[key] == value :
                    raise Exception( \
                       "\n\n--------------------------------------- \n" + \
                       "problem key: %s \n" % (key) + \
                       "info: %s \n " % (info) )
            else :
                dinfo[key] = value
        else :
            dinfo[key] = value

    return dinfo

def noRefseqAnnotation( dinfo ) :
    for k in dinfo : 
        if k.startswith("refseq") :
            del dinfo[k]
    return dinfo

def infoDictToString( dinfo ) :
    string = []
    for key in dinfo :
        if dinfo[key] == "" :
            string.append( "%s" % key )
        else :
            string.append( "%s=%s" % (key,dinfo[key]) )
    return ";".join( string )

def splitCall( call ) :
    if call.startswith( './.' ) : return ['./.']
    else : return call.split(":")

def justCalls( splt ) :
    return splt[ CALL_START: ]

#take the genotype from a broad call and convert into an int
def convertGT( call_splt, variant_ix=1 ) :
    GT_string = call_splt[ CALL_MAP["GT"] ]
    if   GT_string == "./." : return 0
    elif GT_string == "0/0" : return 3
    elif GT_string == "%d/%d" % (variant_ix,variant_ix) : return 2
    elif GT_string == "0/%d" % (variant_ix) : return 1
    #the call is a different variant
    else : return -1

def encodeGT( gt, variant_ix=1 ) :
    if   gt == 0 : return './.'
    elif gt == 3 : return '0/0'
    elif gt == 2 : return '%d/%d' % (variant_ix, variant_ix)
    elif gt == 1 : return '0/%d' % variant_ix
    elif gt == -1 : return '0/0'
    else : raise Exception("GT must be 0,1,2,3, not %d" % gt)

def isMutated( gt ) :
    return gt == 1 or gt == 2

def noInf( gt ) : return gt == 0 

def isCovered( call_splt, coverage_thresh=8 ) :
    if len(call_splt) == 1 : return False
    else:
        return int( call_splt[ CALL_MAP["DP"] ] ) > coverage_thresh

if __name__ == "__main__" :
    #breakIntoFamilyFiles( globes.INDEL_FILE, \
                          #outdir = "%s/raw_data/indels_by_fam" \
                                    #% (globes.DATA_DIR) )

    separateSNPSandINDELS()

#def pickOutFamilies( orig_filename, outdir,family_groups, \
                     #callToString = lambda x:x,\
                     #lineFilter = lambda x:True, \
                     #cols_to_use = range(len(COLUMN_MAP)-1), \
                     #custom_headers = ""
                   #) :
    #groups= {"spoan-1513" : ["SPOAN-1513"]}
    #groups = {"lis-pmg-771" : ["LIS-PMG-711-II-4-4"]}
    #pickOutFamilies( globes.BROAD_FILE, ".", groups )
    pass
