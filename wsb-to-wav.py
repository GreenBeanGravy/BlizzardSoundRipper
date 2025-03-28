import os
import sys
import argparse
import subprocess
import struct
import shutil
import math
from pathlib import Path
import tempfile
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm

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

# Try RIFF scanning without specific header check
goto 0

# Full file scan for audio markers
print "Scanning for audio signatures..."

# Get file size
savepos CURRENT_POS
goto EOF
savepos FILE_SIZE
goto CURRENT_POS

set TOTAL_FOUND 0
for POS = 0 < FILE_SIZE step 256
    goto POS
    getdstring CHECK_SIG 4
    
    # Check for RIFF, RIFX, OggS
    if CHECK_SIG == "RIFF" || CHECK_SIG == "RIFX" || CHECK_SIG == "OggS"
        # Extract this block
        goto POS
        
        # Allocate a reasonable chunk size
        math CHUNK_SIZE = 2000000  # 2MB
        if POS + CHUNK_SIZE > FILE_SIZE
            math CHUNK_SIZE = FILE_SIZE - POS
        endif
        
        # Extract the data
        log MEMORY_FILE POS CHUNK_SIZE
        string FILENAME p= "scan_%s_%08d.wem" CHECK_SIG TOTAL_FOUND
        get MEMORY_SIZE asize MEMORY_FILE
        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
        math TOTAL_FOUND + 1
        
        # Skip ahead to avoid duplicates
        math POS + 1000000
    endif
next POS

if TOTAL_FOUND > 0
    print "Found %TOTAL_FOUND% potential audio files"
endif

# Look for HIRC chunk which may contain embedded files
findloc HIRC_OFFSET string "HIRC" 0 ""
if HIRC_OFFSET != ""
    goto HIRC_OFFSET
    get HIRC_ID long
    get HIRC_SIZE long
    savepos HIRC_START
    
    # Try to read number of items
    get NUM_ITEMS long
    
    # Sanity check
    if NUM_ITEMS > 0 && NUM_ITEMS < 100000
        # Scan through HIRC items
        set EXTRACTED 0
        for i = 0 < NUM_ITEMS
            get TYPE byte
            get SIZE long
            savepos ITEM_START
            
            # Look for sound objects
            if TYPE <= 20 && SIZE > 16 && SIZE < 10000000
                get ID long
                
                # Try to find embedded audio data
                math DATA_POS = ITEM_START + SIZE - 16
                if DATA_POS < HIRC_START + HIRC_SIZE
                    goto DATA_POS
                    getdstring MARKER 4
                    if MARKER == "RIFF" || MARKER == "wem " || MARKER == "OggS"
                        # Found embedded audio
                        goto DATA_POS
                        
                        # Extract with a reasonable size
                        math WEM_SIZE = SIZE - (DATA_POS - ITEM_START)
                        
                        log MEMORY_FILE DATA_POS WEM_SIZE
                        string FILENAME p= "hirc_%08d.wem" ID
                        get MEMORY_SIZE asize MEMORY_FILE
                        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
                        math EXTRACTED + 1
                    endif
                endif
            endif
            
            # Move to next item
            goto ITEM_START
            goto SIZE
        next i
    endif
endif

# Check for DATA chunks with embedded WEMs
findloc DATA_START string "DATA" 0 ""
if DATA_START != ""
    goto DATA_START
    get DATA_ID long
    get DATA_SIZE long
    savepos DATA_POS
    
    # Try to find embedded WEM files by scanning
    set FOUND 0
    for i = 0 < DATA_SIZE step 4
        savepos CURRENT_POS
        if CURRENT_POS >= DATA_POS + DATA_SIZE
            break
        endif
        
        getdstring MARKER 4
        if MARKER == "RIFF" || MARKER == "wem " || MARKER == "OggS"
            # Possible audio file
            goto CURRENT_POS
            
            # Try to extract a reasonable chunk
            math EXTRACT_SIZE = 2000000  # Use fixed size
            if CURRENT_POS + EXTRACT_SIZE > DATA_POS + DATA_SIZE
                math EXTRACT_SIZE = DATA_POS + DATA_SIZE - CURRENT_POS
            endif
            
            log MEMORY_FILE CURRENT_POS EXTRACT_SIZE
            string FILENAME p= "data_%08d.wem" FOUND
            get MEMORY_SIZE asize MEMORY_FILE
            log FILENAME 0 MEMORY_SIZE MEMORY_FILE
            math FOUND + 1
            
            # Skip ahead to avoid duplicates
            math CURRENT_POS + EXTRACT_SIZE
            goto CURRENT_POS
        endif
    next i
endif
"""

def create_wwise_bms_script(path):
    """
    Creates a combined QuickBMS script file that contains multiple extraction methods.
    
    Args:
        path: Path where the BMS script will be saved
        
    Returns:
        The path to the created script file
    """
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
    
    Args:
        wsb_file: Path to the WSB file to extract
        output_dir: Directory where extracted files will be saved
        wsb_prefix: Prefix to add to extracted filenames
        
    Returns:
        Tuple of (number of successfully extracted files, error message if any)
    """
    try:
        with open(wsb_file, 'rb') as f:
            data = f.read()
        
        extracted = 0
        signatures_found = []
        
        # 1. Scan for RIFF signatures (standard WEM/WAV files)
        riff_positions = []
        pos = 0
        while True:
            pos = data.find(b'RIFF', pos)
            if pos == -1:
                break
            riff_positions.append(pos)
            pos += 4
        
        for i, pos in enumerate(riff_positions):
            try:
                if pos + 8 >= len(data):
                    continue
                
                size_bytes = data[pos+4:pos+8]
                size = struct.unpack('<I', size_bytes)[0]
                
                # Reasonableness checks
                if size < 8 or size > 50000000 or pos + size + 8 > len(data):
                    # Try alternative size determination
                    estimated_size = min(2000000, len(data) - pos)  # 2MB or remaining data
                    
                    # Check format type
                    if pos + 12 < len(data):
                        format_type = data[pos+8:pos+12]
                        if format_type in (b'WAVE', b'XWMA', b'wem '):
                            # Extract with estimated size
                            wem_file = os.path.join(output_dir, f"{wsb_prefix}_riff_{i:08d}.wem")
                            with open(wem_file, 'wb') as wf:
                                wf.write(data[pos:pos+estimated_size])
                            
                            extracted += 1
                            signatures_found.append("RIFF")
                    continue
                
                # Standard extraction with proper size
                wem_file = os.path.join(output_dir, f"{wsb_prefix}_riff_{i:08d}.wem")
                with open(wem_file, 'wb') as wf:
                    wf.write(data[pos:pos+size+8])
                
                extracted += 1
                signatures_found.append("RIFF")
            except Exception:
                continue
        
        # 2. Scan for OggS signatures (Vorbis)
        ogg_positions = []
        pos = 0
        while True:
            pos = data.find(b'OggS', pos)
            if pos == -1:
                break
            ogg_positions.append(pos)
            pos += 4
        
        for i, pos in enumerate(ogg_positions):
            try:
                # Estimate size
                next_ogg = data.find(b'OggS', pos + 4)
                if next_ogg != -1 and next_ogg < pos + 10000000:
                    extract_size = next_ogg - pos
                else:
                    extract_size = min(2000000, len(data) - pos)
                
                wem_file = os.path.join(output_dir, f"{wsb_prefix}_ogg_{i:08d}.wem")
                with open(wem_file, 'wb') as wf:
                    wf.write(data[pos:pos+extract_size])
                
                extracted += 1
                signatures_found.append("OggS")
            except Exception:
                continue
        
        # 3. If nothing found, try raw extraction at fixed intervals
        if extracted == 0:
            file_size = len(data)
            chunk_size = min(2000000, file_size // 10)
            
            for i in range(0, 10):
                start_pos = (file_size * i) // 10
                end_pos = min(start_pos + chunk_size, file_size)
                
                if end_pos - start_pos < 1000:  # Skip tiny chunks
                    continue
                
                wem_file = os.path.join(output_dir, f"{wsb_prefix}_chunk_{i:08d}.wem")
                with open(wem_file, 'wb') as wf:
                    wf.write(data[start_pos:end_pos])
                
                extracted += 1
                signatures_found.append("CHUNK")
        
        if extracted == 0:
            return 0, "No audio signatures found"
        
        return extracted, None
    except Exception as e:
        return 0, f"Direct extraction failed: {str(e)}"

def extract_raw_audio(wsb_file, output_dir, wsb_prefix):
    """
    Extract audio by looking for raw audio data patterns without relying on headers.
    
    Args:
        wsb_file: Path to the WSB file to extract
        output_dir: Directory where extracted files will be saved
        wsb_prefix: Prefix to add to extracted filenames
        
    Returns:
        Tuple of (number of successfully extracted files, error message if any)
    """
    try:
        with open(wsb_file, 'rb') as f:
            data = f.read()
            
        file_size = len(data)
        if file_size < 1024:
            return 0, "File too small for raw extraction"
            
        extracted = 0
        
        # Divide file into chunks and analyze each for audio-like characteristics
        chunk_size = 512 * 1024  # 512KB chunks
        num_chunks = file_size // chunk_size + (1 if file_size % chunk_size else 0)
        
        for i in range(num_chunks):
            start_pos = i * chunk_size
            end_pos = min(start_pos + chunk_size, file_size)
            
            # Skip chunks that are too small
            if end_pos - start_pos < 10240:  # At least 10KB
                continue
                
            chunk = data[start_pos:end_pos]
            
            # Calculate some simple statistics to identify audio-like data
            entropy = 0
            byte_counts = {}
            for b in chunk:
                byte_counts[b] = byte_counts.get(b, 0) + 1
            
            for count in byte_counts.values():
                probability = count / len(chunk)
                try:
                    entropy -= probability * (math.log(probability) / math.log(2))
                except:
                    continue
            
            # Audio typically has moderately high entropy
            if 4.5 <= entropy <= 7.0 and len(byte_counts) >= 40:
                output_file = os.path.join(output_dir, f"{wsb_prefix}_raw_{i}.wem")
                with open(output_file, 'wb') as out_f:
                    out_f.write(chunk)
                    
                extracted += 1
        
        # Last resort - extract the entire file
        if extracted == 0:
            output_file = os.path.join(output_dir, f"{wsb_prefix}_full.wem")
            with open(output_file, 'wb') as out_f:
                out_f.write(data)
                
            extracted += 1
        
        if extracted == 0:
            return 0, "No audio patterns identified"
            
        return extracted, None
        
    except Exception as e:
        return 0, f"Raw extraction failed: {str(e)}"

def extract_wsb_worker(args):
    """
    Worker function for parallel extraction of WSB files.
    
    Args:
        args: Tuple containing (wsb_file, output_dir, quickbms_path, bms_script_path, prefix, force_raw)
        
    Returns:
        Tuple containing (filename, success_count, error_message)
    """
    if len(args) == 6:
        wsb_file, output_dir, quickbms_path, bms_script_path, prefix, force_raw = args
    else:
        wsb_file, output_dir, quickbms_path, bms_script_path, prefix = args
        force_raw = False
    
    try:
        # Create a temporary directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            # Generate a unique identifier for this extraction
            wsb_name = Path(wsb_file).stem
            file_prefix = f"{prefix}_{wsb_name}_" if prefix else f"{wsb_name}_"
            
            # If force_raw is True, skip QuickBMS and go straight to raw extraction
            if not force_raw:
                # First try QuickBMS
                try:
                    result = subprocess.run(
                        [quickbms_path, "-o", bms_script_path, wsb_file, temp_dir],
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE,
                        text=False  # Use binary mode to avoid encoding issues
                    )
                    
                    # Check for extracted files
                    wem_files = list(Path(temp_dir).glob("*.wem"))
                    
                    if wem_files:
                        # Move the files to the output directory
                        file_count = 0
                        for wem_file in wem_files:
                            output_file = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                            shutil.move(str(wem_file), str(output_file))
                            file_count += 1
                        
                        return wsb_file.name, file_count, None
                except Exception as e:
                    # Continue to next method if QuickBMS fails
                    pass
                
                # Next try direct extraction
                try:
                    extracted_count, direct_error = extract_wsb_direct(wsb_file, temp_dir, file_prefix)
                    
                    if extracted_count > 0:
                        # Move the extracted files to the output directory
                        wem_files = list(Path(temp_dir).glob("*.wem"))
                        for wem_file in wem_files:
                            output_file = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                            shutil.move(str(wem_file), str(output_file))
                        
                        return wsb_file.name, extracted_count, None
                except Exception as e:
                    # Continue to next method if direct extraction fails
                    pass
            
            # Finally try raw audio extraction
            try:
                extracted_count, raw_error = extract_raw_audio(wsb_file, temp_dir, file_prefix)
                
                if extracted_count > 0:
                    # Move the extracted files to the output directory
                    wem_files = list(Path(temp_dir).glob("*.wem"))
                    for wem_file in wem_files:
                        output_file = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                        shutil.move(str(wem_file), str(output_file))
                    
                    return wsb_file.name, extracted_count, None
                else:
                    if raw_error:
                        return wsb_file.name, 0, raw_error
            except Exception as e:
                return wsb_file.name, 0, f"All extraction methods failed: {str(e)}"
            
            # If we get here, all methods failed
            return wsb_file.name, 0, "All extraction methods failed"
    except Exception as e:
        return wsb_file.name, 0, f"Extraction process failed: {str(e)}"

def convert_wem_to_wav_worker(args):
    """
    Worker function for parallel conversion of WEM files to WAV format.
    
    Args:
        args: Tuple containing (wem_file, vgmstream_cmd, keep_wem)
        
    Returns:
        Tuple containing (filename, success, error_message)
    """
    wem_file, vgmstream_cmd, keep_wem = args
    
    try:
        output_file = wem_file.with_suffix(".wav")
        
        # Convert WEM to WAV using vgmstream
        result = subprocess.run(
            [vgmstream_cmd, "-o", str(output_file), str(wem_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False  # Use binary mode to avoid encoding issues
        )
        
        # Safely decode stderr with error handling
        stderr_text = result.stderr.decode('utf-8', errors='replace') if result.stderr else ""
        
        if result.returncode != 0:
            return wem_file.name, False, f"vgmstream error: {stderr_text}"
        
        # Delete WEM file if conversion was successful and not keeping WEMs
        if output_file.exists() and not keep_wem:
            try:
                wem_file.unlink()
            except Exception:
                pass  # Ignore errors when deleting WEM files
            
        return wem_file.name, True, None
    except Exception as e:
        return wem_file.name, False, f"Conversion failed: {str(e)}"

def main():
    """
    Main function that parses arguments and orchestrates the extraction process.
    """
    parser = argparse.ArgumentParser(description="Extract audio from WSB files and convert to WAV")
    parser.add_argument("--input", "-i", default="input", help="Input folder containing WSB files (default: 'input')")
    parser.add_argument("--output", "-o", default="output", help="Output folder for extracted audio (default: 'output')")
    parser.add_argument("--quickbms", "-q", default="quickbms.exe", help="Path to QuickBMS executable (default: 'quickbms.exe')")
    parser.add_argument("--vgmstream", "-v", default="vgmstream-cli", help="Path to vgmstream-cli for converting WEM files (default: 'vgmstream-cli')")
    parser.add_argument("--keep-wem", "-k", action="store_true", help="Keep WEM files after WAV conversion")
    parser.add_argument("--prefix", "-p", default="", help="Add a prefix to all output files")
    parser.add_argument("--workers", "-w", type=int, default=0, help="Number of worker processes (default: number of CPU cores)")
    parser.add_argument("--raw", "-r", action="store_true", help="Force raw audio extraction mode")
    parser.add_argument("--error-log", "-e", default="errors.log", help="Path to error log file (default: 'errors.log')")
    
    args = parser.parse_args()
    
    # Set up error logging to file
    error_handler = logging.FileHandler(args.error_log, mode='w')
    error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    error_logger = logging.getLogger("error_logger")
    error_logger.setLevel(logging.ERROR)
    error_logger.addHandler(error_handler)
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    quickbms_path = args.quickbms
    
    # Determine optimal number of worker processes
    num_workers = args.workers if args.workers > 0 else max(1, multiprocessing.cpu_count() - 1)
    
    # Validate input path
    if not input_path.exists():
        logger.error(f"Input path {input_path} does not exist")
        sys.exit(1)
    
    # Ensure output directory exists
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Begin extraction process
    print(f"Starting extraction process with {num_workers} workers")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create BMS script with combined extraction methods
        bms_script_path = create_wwise_bms_script(Path(temp_dir) / "wwise.bms")
        
        # Find all WSB files recursively
        wsb_files = list(input_path.glob("**/*.wsb"))
        
        if not wsb_files:
            logger.error(f"No WSB files found in {input_path}")
            sys.exit(1)
        
        print(f"Found {len(wsb_files)} WSB files")
        
        # Prepare tasks for parallel extraction
        extract_tasks = [
            (wsb_file, output_path, quickbms_path, bms_script_path, args.prefix, args.raw)
            for wsb_file in wsb_files
        ]
        
        # Execute extraction tasks in parallel with progress bar
        extract_success = 0
        extract_fail = 0
        extract_errors = {}
        extracted_file_count = 0
        
        print("Extracting audio from WSB files...")
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(extract_wsb_worker, task) for task in extract_tasks]
            
            for future in tqdm(as_completed(futures), total=len(futures), 
                               desc="WSB Extraction", unit="file"):
                try:
                    filename, count, error = future.result()
                    if error:
                        extract_fail += 1
                        extract_errors[filename] = error
                        error_logger.error(f"Extraction error for {filename}: {error}")
                    else:
                        extract_success += 1
                        extracted_file_count += count
                except Exception as e:
                    extract_fail += 1
                    extract_errors[f"unknown_{extract_fail}"] = str(e)
                    error_logger.error(f"Unexpected error during extraction: {str(e)}")
        
        # Output extraction statistics
        print(f"\nExtraction completed: {extract_success} successful, {extract_fail} failed")
        print(f"Extracted {extracted_file_count} audio files")
        
        if extract_fail > 0:
            print(f"Error details saved to {args.error_log}")
        
        # Check if vgmstream is available for WEM to WAV conversion
        have_vgmstream = False
        try:
            vgmstream_check = subprocess.run(
                [args.vgmstream, "--help"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=False  # Use binary mode to avoid encoding issues
            )
            have_vgmstream = vgmstream_check.returncode == 0
        except:
            print("vgmstream-cli not found. WEM files will not be converted to WAV.")
        
        # Convert WEM files to WAV format if vgmstream is available
        if have_vgmstream and extracted_file_count > 0:
            wem_files = list(output_path.glob("*.wem"))
            
            if wem_files:
                print(f"\nFound {len(wem_files)} WEM files to convert to WAV")
                
                # Prepare tasks for parallel conversion
                convert_tasks = [
                    (wem_file, args.vgmstream, args.keep_wem)
                    for wem_file in wem_files
                ]
                
                # Execute conversion tasks in parallel with progress bar
                convert_success = 0
                convert_fail = 0
                convert_errors = {}
                
                print("Converting WEM files to WAV...")
                with ProcessPoolExecutor(max_workers=num_workers) as executor:
                    futures = [executor.submit(convert_wem_to_wav_worker, task) for task in convert_tasks]
                    
                    for future in tqdm(as_completed(futures), total=len(futures), 
                                    desc="WEM to WAV", unit="file"):
                        try:
                            filename, success, error = future.result()
                            if not success:
                                convert_fail += 1
                                convert_errors[filename] = error
                                error_logger.error(f"Conversion error for {filename}: {error}")
                            else:
                                convert_success += 1
                        except Exception as e:
                            convert_fail += 1
                            convert_errors[f"unknown_{convert_fail}"] = str(e)
                            error_logger.error(f"Unexpected error during conversion: {str(e)}")
                
                # Output conversion statistics
                print(f"\nConversion completed: {convert_success} successful, {convert_fail} failed")
                
                if convert_fail > 0:
                    print(f"Error details saved to {args.error_log}")
    
    # Final summary
    print("\n--- Processing Summary ---")
    print(f"WSB files processed: {len(wsb_files)}")
    print(f"Extraction: {extract_success} successful, {extract_fail} failed")
    print(f"Audio files extracted: {extracted_file_count}")
    
    if have_vgmstream and 'convert_success' in locals():
        print(f"Conversion: {convert_success} successful, {convert_fail} failed")
    
    print(f"\nOutput saved to: {output_path}")

if __name__ == "__main__":
    main()