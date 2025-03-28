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

# Look for major audio signatures
findloc RIFF_OFFSET string "RIFF" 0 ""
findloc RIFX_OFFSET string "RIFX" 0 ""
findloc OGGS_OFFSET string "OggS" 0 ""

# If we found signatures, extract them one by one
if RIFF_OFFSET != ""
    goto RIFF_OFFSET
    log MEMORY_FILE RIFF_OFFSET 0x1000000 # 16MB max size
    string FILENAME p= "riff_%08d.wem" 0
    get MEMORY_SIZE asize MEMORY_FILE
    if MEMORY_SIZE > 100
        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
    endif
endif

if RIFX_OFFSET != ""
    goto RIFX_OFFSET
    log MEMORY_FILE RIFX_OFFSET 0x1000000 # 16MB max size
    string FILENAME p= "rifx_%08d.wem" 0
    get MEMORY_SIZE asize MEMORY_FILE
    if MEMORY_SIZE > 100
        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
    endif
endif

if OGGS_OFFSET != ""
    goto OGGS_OFFSET
    log MEMORY_FILE OGGS_OFFSET 0x1000000 # 16MB max size
    string FILENAME p= "oggs_%08d.wem" 0
    get MEMORY_SIZE asize MEMORY_FILE
    if MEMORY_SIZE > 100
        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
    endif
endif

# Look for HIRC chunk which may contain embedded files
findloc HIRC_OFFSET string "HIRC" 0 ""
if HIRC_OFFSET != ""
    goto HIRC_OFFSET
    get HIRC_ID long
    get HIRC_SIZE long
    
    # If reasonable size, extract whole HIRC chunk
    if HIRC_SIZE > 100 && HIRC_SIZE < 0x10000000
        log MEMORY_FILE HIRC_OFFSET HIRC_SIZE
        string FILENAME p= "hirc_full.wem"
        get MEMORY_SIZE asize MEMORY_FILE
        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
    endif
endif

# Check for DATA chunk and extract whole chunk if found
findloc DATA_START string "DATA" 0 ""
if DATA_START != ""
    goto DATA_START
    get DATA_ID long
    get DATA_SIZE long
    
    # If DATA chunk has reasonable size, extract it
    if DATA_SIZE > 100 && DATA_SIZE < 0x10000000
        log MEMORY_FILE DATA_START DATA_SIZE
        string FILENAME p= "data_full.wem"
        get MEMORY_SIZE asize MEMORY_FILE
        log FILENAME 0 MEMORY_SIZE MEMORY_FILE
    endif
endif

# Last resort - extract the entire file
goto 0
get FILESIZE asize
log MEMORY_FILE 0 FILESIZE
string FILENAME p= "full_file.wem"
get MEMORY_SIZE asize MEMORY_FILE
log FILENAME 0 MEMORY_SIZE MEMORY_FILE
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
        
        # 1. Extract whole file if RIFF header is at the beginning
        if data[:4] == b'RIFF' or data[:4] == b'RIFX':
            wem_file = os.path.join(output_dir, f"{wsb_prefix}_full.wem")
            with open(wem_file, 'wb') as wf:
                wf.write(data)
            extracted += 1
            return extracted, None
            
        # 2. If no RIFF at start, scan for RIFF signatures
        riff_pos = data.find(b'RIFF')
        if riff_pos != -1:
            wem_file = os.path.join(output_dir, f"{wsb_prefix}_riff.wem")
            with open(wem_file, 'wb') as wf:
                wf.write(data[riff_pos:])
            extracted += 1
            return extracted, None
            
        # 3. Try OggS
        ogg_pos = data.find(b'OggS')
        if ogg_pos != -1:
            wem_file = os.path.join(output_dir, f"{wsb_prefix}_ogg.wem")
            with open(wem_file, 'wb') as wf:
                wf.write(data[ogg_pos:])
            extracted += 1
            return extracted, None
            
        # 4. Last resort - extract the entire file
        wem_file = os.path.join(output_dir, f"{wsb_prefix}_full.wem")
        with open(wem_file, 'wb') as wf:
            wf.write(data)
        extracted += 1
        
        return extracted, None
    except Exception as e:
        return 0, f"Direct extraction failed: {str(e)}"

def convert_wem_to_wav(wem_file, vgmstream_path, keep_wem):
    """
    Convert a WEM file to WAV format and optionally delete the WEM file.
    
    Args:
        wem_file: Path object for the WEM file
        vgmstream_path: Path to vgmstream executable
        keep_wem: Whether to keep WEM file after conversion
        
    Returns:
        Tuple of (success, wav_file_path or None, error)
    """
    try:
        # Check file size - if too small, it's likely not a valid audio file
        file_size = wem_file.stat().st_size
        if file_size < 5000:  # Less than 5KB is suspicious
            logger.warning(f"File {wem_file.name} is very small ({file_size} bytes), may not be a valid audio file")
            # We'll try to convert anyway, but with a note
        
        wav_file = wem_file.with_suffix('.wav')
        
        # Run vgmstream to convert WEM to WAV with verbose output
        cmd = [vgmstream_path, "-o", str(wav_file), str(wem_file), "-v"]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        
        # Log the output regardless of success for debugging
        if stdout:
            logger.debug(f"vgmstream stdout for {wem_file.name}: {stdout}")
        if stderr:
            logger.debug(f"vgmstream stderr for {wem_file.name}: {stderr}")
        
        if result.returncode != 0 or not wav_file.exists() or wav_file.stat().st_size < 100:
            # If conversion failed, we'll keep the WEM file for inspection
            return False, None, f"vgmstream conversion failed: {stderr or 'Unknown error'}"
        
        # Delete the WEM file if requested and conversion succeeded
        if not keep_wem:
            try:
                wem_file.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete WEM file {wem_file}: {str(e)}")
            
        return True, wav_file, None
    except Exception as e:
        return False, None, f"Conversion error: {str(e)}"

def extract_wsb_worker(args):
    """
    Worker function for parallel extraction of WSB files.
    
    Args:
        args: Tuple containing (wsb_file, output_dir, quickbms_path, bms_script_path, vgmstream_path, keep_wem, prefix, force_raw)
        
    Returns:
        Tuple containing (filename, success_count, conversion_failures, error_message)
    """
    wsb_file, output_dir, quickbms_path, bms_script_path, vgmstream_path, keep_wem, prefix, force_raw = args
    
    try:
        # Create a temporary directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            
            # Generate a unique identifier for this extraction
            wsb_name = Path(wsb_file).stem
            file_prefix = f"{prefix}_{wsb_name}_" if prefix else f"{wsb_name}_"
            
            extracted_files = 0
            converted_wavs = 0
            conversion_failures = 0
            conversion_errors = []
            
            # Method 1: Try QuickBMS extraction if not forcing raw mode
            if not force_raw:
                try:
                    proc = subprocess.run(
                        [quickbms_path, "-o", bms_script_path, wsb_file, temp_dir],
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE,
                        timeout=60  # Add timeout to prevent hanging
                    )
                    
                    # Log QuickBMS output for debugging
                    stdout = proc.stdout.decode('utf-8', errors='replace')
                    stderr = proc.stderr.decode('utf-8', errors='replace')
                    if stderr:
                        logger.debug(f"QuickBMS stderr for {wsb_file.name}: {stderr}")
                    
                    # Check for extracted files
                    wem_files = list(temp_dir_path.glob("*.wem"))
                    extracted_files = len(wem_files)
                    
                    # If we successfully extracted files with QuickBMS
                    if extracted_files > 0:
                        # Process each extracted WEM file
                        for wem_file in wem_files:
                            # Log file size for debugging
                            file_size = wem_file.stat().st_size
                            logger.debug(f"Extracted {wem_file.name}: {file_size} bytes")
                            
                            # First convert to WAV if we have vgmstream
                            if vgmstream_path:
                                success, wav_file, error = convert_wem_to_wav(wem_file, vgmstream_path, keep_wem)
                                
                                if success:
                                    # Move the WAV file to output directory
                                    output_wav = Path(output_dir) / f"{file_prefix}{wav_file.name}"
                                    shutil.copy2(wav_file, output_wav)
                                    converted_wavs += 1
                                else:
                                    # If conversion failed, record the error and move the WEM
                                    conversion_failures += 1
                                    conversion_errors.append(f"{wem_file.name}: {error}")
                                    logger.warning(f"Failed to convert {wem_file.name}: {error}")
                                    output_wem = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                                    shutil.copy2(wem_file, output_wem)
                                continue
                            
                            # If no vgmstream, just move the WEM
                            output_wem = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                            shutil.copy2(wem_file, output_wem)
                        
                        # Return results including conversion failures
                        return wsb_file.name, converted_wavs + (extracted_files - converted_wavs), conversion_failures, None
                except Exception as e:
                    logger.warning(f"QuickBMS extraction failed for {wsb_file.name}: {str(e)}")
                    # If QuickBMS fails, continue to next method
                    pass
            
            # Method 2: Try direct extraction
            try:
                count, error = extract_wsb_direct(wsb_file, temp_dir, file_prefix)
                
                if count > 0:
                    # Direct extraction succeeded, process the files
                    wem_files = list(temp_dir_path.glob("*.wem"))
                    
                    for wem_file in wem_files:
                        # Log file size for debugging
                        file_size = wem_file.stat().st_size
                        logger.debug(f"Direct extracted {wem_file.name}: {file_size} bytes")
                        
                        # Convert to WAV if we have vgmstream
                        if vgmstream_path:
                            success, wav_file, error = convert_wem_to_wav(wem_file, vgmstream_path, keep_wem)
                            
                            if success:
                                # Move the WAV file to output directory
                                output_wav = Path(output_dir) / f"{file_prefix}{wav_file.name}"
                                shutil.copy2(wav_file, output_wav)
                                converted_wavs += 1
                            else:
                                # If conversion failed, record the error and move the WEM
                                conversion_failures += 1
                                conversion_errors.append(f"{wem_file.name}: {error}")
                                logger.warning(f"Failed to convert {wem_file.name}: {error}")
                                output_wem = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                                shutil.copy2(wem_file, output_wem)
                            continue
                        
                        # If no vgmstream, just move the WEM
                        output_wem = Path(output_dir) / f"{file_prefix}{wem_file.name}"
                        shutil.copy2(wem_file, output_wem)
                    
                    # Return results including conversion failures
                    return wsb_file.name, converted_wavs + (count - converted_wavs), conversion_failures, None
            except Exception as e:
                logger.warning(f"Direct extraction failed for {wsb_file.name}: {str(e)}")
                # Continue to final attempt if direct extraction fails
                pass
            
            # Method 3: Last resort - extract whole file
            try:
                output_wem = temp_dir_path / f"{file_prefix}full_file.wem"
                shutil.copy2(wsb_file, output_wem)
                
                # Log file size for debugging
                file_size = output_wem.stat().st_size
                logger.debug(f"Full file extraction {output_wem.name}: {file_size} bytes")
                
                # Try to convert this whole-file WEM
                if vgmstream_path:
                    success, wav_file, error = convert_wem_to_wav(output_wem, vgmstream_path, keep_wem)
                    
                    if success:
                        # Move the WAV file to output directory
                        output_wav = Path(output_dir) / f"{file_prefix}{wav_file.name}"
                        shutil.copy2(wav_file, output_wav)
                        return wsb_file.name, 1, 0, None
                    else:
                        # If conversion failed, log and move the WEM
                        conversion_failures += 1
                        conversion_errors.append(f"{output_wem.name}: {error}")
                        logger.warning(f"Failed to convert {output_wem.name}: {error}")
                
                # If conversion failed or no vgmstream, move the WEM
                final_output = Path(output_dir) / f"{file_prefix}{output_wem.name}"
                shutil.copy2(output_wem, final_output)
                return wsb_file.name, 1, conversion_failures, None
            except Exception as e:
                error_msg = f"All extraction methods failed: {str(e)}"
                logger.error(error_msg)
                return wsb_file.name, 0, 0, error_msg
    except Exception as e:
        error_msg = f"Extraction process error: {str(e)}"
        logger.error(error_msg)
        return wsb_file.name, 0, 0, error_msg

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
    parser.add_argument("--verbose", "-d", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--try-ffmpeg", "-f", action="store_true", help="Try using FFmpeg if vgmstream fails")
    
    args = parser.parse_args()
    
    # Setup logging based on verbosity
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        print("Debug logging enabled")
    
    # Set up error logging to file
    error_handler = logging.FileHandler(args.error_log, mode='w')
    error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    error_logger = logging.getLogger("error_logger")
    error_logger.setLevel(logging.ERROR)
    error_logger.addHandler(error_handler)
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    # Determine optimal number of worker processes
    num_workers = args.workers if args.workers > 0 else max(1, multiprocessing.cpu_count() - 1)
    
    # Validate input path
    if not input_path.exists():
        logger.error(f"Input path {input_path} does not exist")
        sys.exit(1)
    
    # Ensure output directory exists
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Check if vgmstream is available
    vgmstream_path = None
    try:
        subprocess.run([args.vgmstream, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        vgmstream_path = args.vgmstream
        print(f"vgmstream-cli found at {vgmstream_path}")
    except:
        print("vgmstream-cli not found. WEM files will not be converted to WAV.")
    
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
        
        print(f"Found {len(wsb_files)} WSB files to process")
        
        # Prepare tasks for parallel extraction
        extract_tasks = [
            (wsb_file, output_path, args.quickbms, bms_script_path, vgmstream_path, 
             args.keep_wem, args.prefix, args.raw)
            for wsb_file in wsb_files
        ]
        
        # Execute extraction tasks in parallel with progress bar
        success_count = 0
        fail_count = 0
        total_files_processed = 0
        total_conversion_failures = 0
        file_errors = {}
        
        print("Extracting audio and converting to WAV...")
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(extract_wsb_worker, task) for task in extract_tasks]
            
            for future in tqdm(as_completed(futures), total=len(futures), 
                               desc="Processing", unit="file"):
                try:
                    filename, count, conv_failures, error = future.result()
                    if error:
                        fail_count += 1
                        file_errors[filename] = error
                        error_logger.error(f"Error processing {filename}: {error}")
                    else:
                        success_count += 1
                        total_files_processed += count
                        total_conversion_failures += conv_failures
                except Exception as e:
                    fail_count += 1
                    file_errors[f"unknown_{fail_count}"] = str(e)
                    error_logger.error(f"Exception during processing: {str(e)}")
    
    # Count WEM files in output directory - these are likely conversion failures
    wem_files_count = len(list(output_path.glob("*.wem")))
    wav_files_count = len(list(output_path.glob("*.wav")))
    
    # Final summary
    print("\n--- Processing Summary ---")
    print(f"WSB files processed: {len(wsb_files)}")
    print(f"Files successfully processed: {success_count}")
    print(f"Files failed to process: {fail_count}")
    print(f"Total audio files extracted: {total_files_processed}")
    
    if total_conversion_failures > 0:
        print(f"Failed conversions: {total_conversion_failures} WEM files could not be converted to WAV")
        
    print(f"Output directory contains: {wav_files_count} WAV files, {wem_files_count} WEM files")
    
    if wem_files_count > 0:
        print("\nNote about WEM files:")
        print("- Small WEM files (< 5KB) are likely metadata-only or invalid")
        print("- Try opening these files in a hex editor to inspect their content")
        print("- If you need to convert these files, you might try ReWwise or other specialized tools")
    
    if fail_count > 0 or total_conversion_failures > 0:
        print(f"\nError details saved to {args.error_log}")
    
    print(f"\nOutput saved to: {output_path}")

if __name__ == "__main__":
    main()
