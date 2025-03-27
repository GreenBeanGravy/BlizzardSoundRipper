import os
import sys
import argparse
import subprocess
import struct
import shutil
from pathlib import Path
import tempfile
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import uuid

# Set up logging with timestamp, level and message format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Primary Wwise extraction script for QuickBMS
# Handles standard BKHD/DIDX/DATA structure format commonly found in Wwise soundbanks
WWISE_BMS_SCRIPT_V1 = r"""
# Wwise extraction script for QuickBMS - Standard format
# Based on various community scripts

idstring "BKHD"
get DUMMY long
get SIZE long
math OFFSET = 12 + SIZE

goto OFFSET
findloc DIDX_OFFSET string "DIDX"
goto DIDX_OFFSET
get DIDX_SIGN long
get DIDX_SIZE long

# Get WEM file information from DIDX
math NUM_FILES = DIDX_SIZE / 12
for i = 0 < NUM_FILES
    get ID long
    get OFFSET long
    get SIZE long
    putarray 0 i ID
    putarray 1 i OFFSET
    putarray 2 i SIZE
next i

# Find DATA section that contains the actual WEM files
findloc DATA_OFFSET string "DATA"
goto DATA_OFFSET
get DATA_SIGN long
get DATA_SIZE long
savepos BASE_OFFSET

# Extract each WEM file
for i = 0 < NUM_FILES
    getarray ID 0 i
    getarray OFFSET 1 i
    getarray SIZE 2 i
    math OFFSET += BASE_OFFSET + 8
    
    log MEMORY_FILE OFFSET SIZE
    string FILENAME p= "%08d.wem" ID
    get MEMORY_SIZE asize MEMORY_FILE
    log FILENAME 0 MEMORY_SIZE MEMORY_FILE
next i
"""

# Secondary Wwise extraction script for QuickBMS
# Handles newer RIFF-based formats and attempts multiple extraction methods
# for cases where the standard format extraction fails
WWISE_BMS_SCRIPT_V2 = r"""
# Alternative Wwise extraction script for QuickBMS
# For newer Wwise versions

idstring "RIFF"
get RIFF_SIZE long
get WAVE string

# Look for the HIRC chunk which may contain embedded files
findloc HIRC_OFFSET string "HIRC"
if HIRC_OFFSET != ""
    goto HIRC_OFFSET
    get HIRC_ID long
    get HIRC_SIZE long
    savepos HIRC_START
    
    get NUM_ITEMS long
    
    # Scan through HIRC items
    set EXTRACTED 0
    for i = 0 < NUM_ITEMS
        get TYPE byte
        get SIZE long
        savepos ITEM_START
        
        # Look for sound objects (type 2 or type 12)
        if TYPE == 2 || TYPE == 12
            # Read sound ID
            get ID long
            
            # Skip to potential embedded WEM data
            math DATA_POS = ITEM_START + SIZE - 16
            goto DATA_POS
            
            # Check for embedded WEM marker (some formats have it)
            getdstring MARKER 4
            if MARKER == "RIFF" || MARKER == "wem "
                # We found an embedded WEM file
                goto DATA_POS
                savepos WEM_START
                
                # Try to determine size (different formats may store it differently)
                # Just grab a reasonable chunk of data that should contain a full WEM
                math WEM_SIZE = SIZE - (DATA_POS - ITEM_START)
                
                # Log the file
                log MEMORY_FILE WEM_START WEM_SIZE
                string FILENAME p= "hirc_%08d.wem" ID
                get MEMORY_SIZE asize MEMORY_FILE
                log FILENAME 0 MEMORY_SIZE MEMORY_FILE
                math EXTRACTED + 1
            endif
        endif
        
        # Move to next item
        math NEXT_POS = ITEM_START + SIZE
        goto NEXT_POS
    next i
    
    if EXTRACTED == 0
        print "No HIRC embedded WEM files found"
    endif
endif

# Also look for DATA sections with embedded WEMs
findloc DATA_START string "DATA"
if DATA_START != ""
    goto DATA_START
    get DATA_ID long
    get DATA_SIZE long
    savepos DATA_POS
    
    # Try to find embedded WEM files by RIFF header scanning
    set FOUND 0
    for i = 0 < DATA_SIZE step 4
        savepos CURRENT_POS
        if CURRENT_POS >= DATA_POS + DATA_SIZE
            break
        endif
        
        getdstring MARKER 4
        if MARKER == "RIFF"
            # Possible WEM file
            goto CURRENT_POS
            
            # Read RIFF size
            get POSSIBLE_SIZE long
            if POSSIBLE_SIZE > 0 && POSSIBLE_SIZE < 100000000  # Size sanity check
                # Extract this probable WEM
                log MEMORY_FILE CURRENT_POS POSSIBLE_SIZE + 8
                string FILENAME p= "data_%08d.wem" FOUND
                get MEMORY_SIZE asize MEMORY_FILE
                log FILENAME 0 MEMORY_SIZE MEMORY_FILE
                math FOUND + 1
                
                # Skip ahead to avoid overlapping extractions
                math CURRENT_POS + POSSIBLE_SIZE + 8
                goto CURRENT_POS
            endif
        endif
        
        # Continue scanning
        math CURRENT_POS + 1
        goto CURRENT_POS
    next i
    
    if FOUND == 0
        print "No DATA embedded WEM files found"
    endif
endif

# Alternative method - direct scan for WEM signatures
goto 0
findloc RIFF_OFFSET string "RIFF" 0 ""
set WEM_COUNT 0
if RIFF_OFFSET != ""
    do
        goto RIFF_OFFSET
        get RIFF_TAG long
        get WEM_SIZE long
        getdstring FORMAT_CHECK 4
        
        # Check if this is a likely WEM file
        if FORMAT_CHECK == "WAVE" || FORMAT_CHECK == "XWMA" || FORMAT_CHECK == "OggS"
            # It's likely a WEM file
            log MEMORY_FILE RIFF_OFFSET WEM_SIZE + 8
            string FILENAME p= "scan_%08d.wem" WEM_COUNT
            get MEMORY_SIZE asize MEMORY_FILE
            log FILENAME 0 MEMORY_SIZE MEMORY_FILE
            math WEM_COUNT + 1
        endif
        
        math RIFF_OFFSET + 4  # Move past current "RIFF"
        findloc RIFF_OFFSET string "RIFF" RIFF_OFFSET ""
    while RIFF_OFFSET != ""
endif
"""

def create_wwise_bms_script(path):
    """
    Creates a combined QuickBMS script file that contains multiple extraction methods.
    
    The script combines standard BKHD/DIDX/DATA extraction with alternative methods
    to maximize the chances of successful extraction from various Wwise formats.
    
    Args:
        path: Path where the BMS script will be saved
        
    Returns:
        The path to the created script file
    """
    # Create a combined script that tries multiple methods
    script = "# Combined Wwise extraction script\n\n"
    script += WWISE_BMS_SCRIPT_V1
    script += "\n\n# If standard method failed, try alternative method\n"
    script += WWISE_BMS_SCRIPT_V2
    
    with open(path, 'w') as f:
        f.write(script)
    return path

def extract_wsb_direct(wsb_file, output_dir, wsb_prefix):
    """
    Fallback extraction method that directly scans for RIFF/WEM signatures in binary data.
    
    Used when QuickBMS extraction fails. This method searches for RIFF headers
    and attempts to extract WEM files based on size fields in the header.
    
    Args:
        wsb_file: Path to the WSB file to extract
        output_dir: Directory where extracted WEM files will be saved
        wsb_prefix: Prefix to add to extracted filenames
        
    Returns:
        Number of successfully extracted WEM files
    """
    try:
        with open(wsb_file, 'rb') as f:
            data = f.read()
        
        # Search for RIFF signatures (likely WEM files)
        positions = []
        pos = 0
        while True:
            pos = data.find(b'RIFF', pos)
            if pos == -1:
                break
            positions.append(pos)
            pos += 4
        
        if not positions:
            return 0
        
        # Extract each potential WEM file
        extracted = 0
        for i, pos in enumerate(positions):
            try:
                # RIFF size is at offset +4 (4 bytes)
                if pos + 8 >= len(data):
                    continue
                
                size_bytes = data[pos+4:pos+8]
                size = struct.unpack('<I', size_bytes)[0]
                
                # Sanity check on size (max 100MB per WEM)
                if size < 8 or size > 100000000 or pos + size + 8 > len(data):
                    continue
                
                # Extract to file with unique name based on WSB file and position
                wem_file = os.path.join(output_dir, f"{wsb_prefix}_{i:08d}.wem")
                with open(wem_file, 'wb') as wf:
                    wf.write(data[pos:pos+size+8])  # Include RIFF header + size
                
                extracted += 1
            except Exception:
                continue
        
        return extracted
    except Exception:
        return 0

def extract_wsb_worker(args):
    """
    Worker function for parallel extraction of WSB files using QuickBMS.
    
    Creates a temporary directory, runs QuickBMS to extract WEM files,
    then renames and moves the files to the output directory.
    Falls back to direct extraction if QuickBMS fails.
    
    Args:
        args: Tuple containing (wsb_file, output_dir, quickbms_path, bms_script_path, prefix)
        
    Returns:
        Number of successfully extracted WEM files
    """
    wsb_file, output_dir, quickbms_path, bms_script_path, prefix = args
    
    try:
        # Create a temporary directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            # Generate a unique identifier for this extraction
            wsb_name = Path(wsb_file).stem
            file_prefix = f"{prefix}_{wsb_name}_" if prefix else f"{wsb_name}_"
            
            # Run QuickBMS with redirection of output to avoid cluttering console
            subprocess.run(
                [quickbms_path, "-o", bms_script_path, wsb_file, temp_dir],
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            
            # Check for extracted files
            wem_files = list(Path(temp_dir).glob("*.wem"))
            
            if not wem_files:
                # Try direct extraction as fallback method if QuickBMS found nothing
                extract_wsb_direct(wsb_file, temp_dir, file_prefix)
                wem_files = list(Path(temp_dir).glob("*.wem"))
                if not wem_files:
                    return 0
            
            # Move all WEM files to output directory with unique names
            file_count = 0
            for wem_file in wem_files:
                output_file = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                shutil.move(str(wem_file), str(output_file))
                file_count += 1
            
            return file_count
    except Exception:
        return 0

def convert_wem_to_wav_worker(args):
    """
    Worker function for parallel conversion of WEM files to WAV format.
    
    Uses vgmstream-cli to convert WEM audio files to standard WAV format.
    Optionally deletes the original WEM file after successful conversion.
    
    Args:
        args: Tuple containing (wem_file, vgmstream_cmd, keep_wem)
        
    Returns:
        Boolean indicating conversion success
    """
    wem_file, vgmstream_cmd, keep_wem = args
    
    try:
        output_file = wem_file.with_suffix(".wav")
        
        # Convert WEM to WAV using vgmstream
        result = subprocess.run(
            [vgmstream_cmd, "-o", str(output_file), str(wem_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Delete WEM file if conversion was successful and not keeping WEMs
        if result.returncode == 0 and output_file.exists() and not keep_wem:
            try:
                wem_file.unlink()
            except:
                pass
            return True
        
        return result.returncode == 0
    except Exception:
        return False

def main():
    """
    Main function that parses arguments and orchestrates the extraction process.
    
    Handles command-line arguments, validates inputs, manages parallel extraction
    of WSB files using QuickBMS, and optional conversion of WEM to WAV using vgmstream.
    """
    parser = argparse.ArgumentParser(description="Extract audio from WSB files using QuickBMS (Optimized)")
    parser.add_argument("--input", "-i", default="input", help="Input folder containing WSB files (default: 'input')")
    parser.add_argument("--output", "-o", default="output", help="Output folder for extracted audio (default: 'output')")
    parser.add_argument("--quickbms", "-q", default="quickbms.exe", help="Path to QuickBMS executable (default: 'quickbms.exe')")
    parser.add_argument("--vgmstream", "-v", default="vgmstream-cli", help="Path to vgmstream-cli for converting WEM files (default: 'vgmstream-cli')")
    parser.add_argument("--keep-wem", "-k", action="store_true", help="Keep WEM files after WAV conversion")
    parser.add_argument("--prefix", "-p", default="", help="Add a prefix to all output files")
    parser.add_argument("--workers", "-w", type=int, default=0, help="Number of worker processes (default: number of CPU cores)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        # Reduce log spam by setting higher log level for performance
        logger.setLevel(logging.WARNING)
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    quickbms_path = args.quickbms
    
    # Determine optimal number of worker processes based on available CPU cores
    num_workers = args.workers if args.workers > 0 else multiprocessing.cpu_count()
    
    # Validate input path
    if not input_path.exists():
        logger.error(f"Input path {input_path} does not exist")
        sys.exit(1)
    
    # Validate QuickBMS executable
    if not Path(quickbms_path).exists():
        logger.error(f"QuickBMS executable not found at {quickbms_path}")
        sys.exit(1)
    
    # Ensure output directory exists
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Begin extraction process
    logger.info(f"Starting extraction process with {num_workers} workers")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create BMS script with combined extraction methods
        bms_script_path = create_wwise_bms_script(Path(temp_dir) / "wwise.bms")
        
        # Find all WSB files recursively
        wsb_files = list(input_path.glob("**/*.wsb"))
        
        if not wsb_files:
            logger.error(f"No WSB files found in {input_path}")
            sys.exit(1)
        
        logger.info(f"Processing {len(wsb_files)} WSB files")
        
        # Prepare tasks for parallel extraction
        extract_tasks = [
            (wsb_file, output_path, quickbms_path, bms_script_path, args.prefix)
            for wsb_file in wsb_files
        ]
        
        # Execute extraction tasks in parallel
        extracted_count = 0
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            for result in executor.map(extract_wsb_worker, extract_tasks):
                extracted_count += result
        
        logger.info(f"Extracted {extracted_count} audio files")
        
        # Check if vgmstream is available for WEM to WAV conversion
        have_vgmstream = False
        try:
            subprocess.run([args.vgmstream, "--help"], 
                          stdout=subprocess.DEVNULL, 
                          stderr=subprocess.DEVNULL)
            have_vgmstream = True
        except:
            logger.warning("vgmstream-cli not found. WEM files will not be converted to WAV.")
        
        # Convert WEM files to WAV format if vgmstream is available
        if have_vgmstream and extracted_count > 0:
            wem_files = list(output_path.glob("*.wem"))
            
            if wem_files:
                logger.info(f"Converting {len(wem_files)} WEM files to WAV")
                
                # Prepare tasks for parallel conversion
                convert_tasks = [
                    (wem_file, args.vgmstream, args.keep_wem)
                    for wem_file in wem_files
                ]
                
                # Execute conversion tasks in parallel
                converted_count = 0
                with ProcessPoolExecutor(max_workers=num_workers) as executor:
                    for result in executor.map(convert_wem_to_wav_worker, convert_tasks):
                        if result:
                            converted_count += 1
                
                logger.info(f"Converted {converted_count} WEM files to WAV")
    
    logger.info(f"Processing complete. Audio files saved to {output_path}")

if __name__ == "__main__":
    main()