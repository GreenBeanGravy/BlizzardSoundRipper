#!/usr/bin/env python3
"""
Complete Installation Script for BlizzardSoundRipper Dependencies

This script automates the installation of QuickBMS and vgmstream tools,
which are required for extracting and converting audio from Blizzard games.
It detects the operating system, downloads appropriate versions, and sets up
all necessary files in the current directory.

The script will:
1. Fetch or use requirements.txt and install dependencies
2. Download and install QuickBMS from GitHub
3. Download and install vgmstream from GitHub
4. Clean up temporary files

Usage: python install.py
"""

import os
import sys
import platform
import subprocess
import shutil
import zipfile
import tempfile
from pathlib import Path
import requests
from tqdm import tqdm

# Constants
REQUIREMENTS_URL = "https://raw.githubusercontent.com/GreenBeanGravy/BlizzardSoundRipper/main/requirements.txt"
QUICKBMS_REPO = "https://github.com/LittleBigBug/QuickBMS/releases/latest/download"
VGMSTREAM_REPO = "https://github.com/vgmstream/vgmstream/releases/latest/download"


def fetch_requirements_file():
    """
    Downloads requirements.txt from GitHub repository if not found locally.
    
    This ensures that the script can install all necessary Python dependencies
    even if the user hasn't downloaded the full repository.
    
    Returns:
        Path: Path to the requirements file (either local or downloaded)
    """
    local_req = Path("requirements.txt")
    
    # Check if requirements.txt exists locally
    if local_req.exists():
        print("Found local requirements.txt file.")
        return local_req
    
    print("Local requirements.txt not found. Fetching from GitHub repository...")
    try:
        # Download requirements.txt from GitHub
        response = requests.get(REQUIREMENTS_URL)
        response.raise_for_status()  # Raise exception for HTTP errors
        
        # Save the downloaded content to local file
        with open(local_req, 'w') as f:
            f.write(response.text)
        
        print("Downloaded requirements.txt successfully.")
        return local_req
    except Exception as e:
        print(f"Error fetching requirements.txt: {e}")
        print("Continuing without installing requirements.")
        return None


def get_latest_quickbms_url():
    """
    Determines the appropriate QuickBMS download URL based on the operating system.
    
    Detects Windows, macOS, or Linux and selects the corresponding zip file
    from the QuickBMS repository.
    
    Returns:
        tuple: (download_url, filename) for the appropriate QuickBMS release
    """
    os_type = platform.system().lower()
    
    if os_type == "windows":
        filename = "quickbms_win.zip"
    elif os_type == "darwin":
        filename = "quickbms_macosx.zip"
    elif os_type == "linux":
        filename = "quickbms_linux.zip"
    else:
        raise SystemExit(f"Unsupported operating system: {os_type}")
    
    download_url = f"{QUICKBMS_REPO}/{filename}"
    return download_url, filename


def get_latest_vgmstream_url():
    """
    Determines the appropriate vgmstream download URL based on the operating system.
    
    Detects Windows, macOS, or Linux and selects the corresponding zip file
    from the vgmstream repository. For Windows, uses the standard build instead
    of the 64-bit specific version.
    
    Returns:
        tuple: (download_url, filename) for the appropriate vgmstream release
    """
    os_type = platform.system().lower()
    
    if os_type == "windows":
        filename = "vgmstream-win.zip"  # Using regular Windows build, not the 64-bit one
    elif os_type == "darwin":
        filename = "vgmstream-mac-cli.zip"
    elif os_type == "linux":
        filename = "vgmstream-linux-cli.zip"
    else:
        raise SystemExit(f"Unsupported operating system: {os_type}")
    
    download_url = f"{VGMSTREAM_REPO}/{filename}"
    return download_url, filename


def install_requirements(req_file):
    """
    Installs Python dependencies from requirements.txt.
    
    Args:
        req_file: Path to the requirements.txt file
    
    Returns:
        bool: True if installation was successful or skipped, False on error
    """
    print("Installing requirements...")
    
    if not req_file or not Path(req_file).exists():
        print("Requirements file not available. Skipping requirements installation.")
        return True
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
        print("Requirements installed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error installing requirements: {e}")
        print("Continuing with installation despite requirements error.")
        return False


def download_file(url, filename):
    """
    Downloads a file from a URL with a progress bar.
    
    Uses tqdm to display download progress and handles HTTP errors
    appropriately.
    
    Args:
        url: URL to download from
        filename: Local path where the downloaded file will be saved
    
    Returns:
        bool: True if download was successful, False otherwise
    """
    print(f"Downloading {filename}...")
    try:
        # Send HTTP request with stream=True to download in chunks
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Get total file size for progress bar
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024  # 1 Kibibyte
        
        # Download with progress bar
        with open(filename, 'wb') as file, tqdm(
                desc=filename,
                total=total_size,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
        ) as bar:
            for data in response.iter_content(block_size):
                size = file.write(data)
                bar.update(size)
                
        print(f"Download completed: {filename}")
        return True
    except Exception as e:
        print(f"Error downloading file: {e}")
        return False


def extract_zip(zip_path):
    """
    Extracts a ZIP archive to a folder with the same base name.
    
    Args:
        zip_path: Path to the ZIP file to extract
    
    Returns:
        str or None: Path to the extraction directory if successful, None otherwise
    """
    print(f"Extracting {zip_path}...")
    
    # Extract the base filename without extension for the output folder
    folder_name = os.path.splitext(zip_path)[0]
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(folder_name)
        print(f"Extraction completed to {folder_name}/")
        return folder_name
    except Exception as e:
        print(f"Error extracting zip file: {e}")
        return None


def copy_files(source_dir, dest_dir="."):
    """
    Recursively copies all files from source directory to destination directory.
    
    Preserves directory structure and handles path resolution to prevent errors.
    
    Args:
        source_dir: Directory containing files to copy
        dest_dir: Destination directory (defaults to current directory)
    
    Returns:
        bool: True if at least one file was copied successfully, False otherwise
    """
    source_dir = str(source_dir)  # Ensure string path
    dest_dir = str(dest_dir) if dest_dir else "."  # Ensure string path with default
    
    print(f"Copying files from '{source_dir}' to '{dest_dir}'...")
    
    try:
        # Convert to Path objects for better path handling
        source_path = Path(source_dir).resolve()
        dest_path = Path(dest_dir).resolve()
        
        # Validate source directory
        if not source_path.exists():
            print(f"Error: Source directory '{source_path}' does not exist")
            return False
            
        # Create destination directory if needed
        dest_path.mkdir(parents=True, exist_ok=True)
            
        # Debug information
        print(f"Source directory (absolute): {source_path}")
        print(f"Destination directory (absolute): {dest_path}")
        
        # Find and count all files to copy
        files_to_copy = list(source_path.glob('**/*'))
        file_count = len([f for f in files_to_copy if f.is_file()])
        print(f"Found {file_count} files to copy")
        
        if file_count == 0:
            print(f"Warning: No files found in {source_path}")
            # List contents of the directory for debugging
            print(f"Contents of source directory: {list(source_path.iterdir())}")
            return False
        
        # Copy files with proper path handling
        copied_files = []
        for item in files_to_copy:
            if item.is_file():
                # Get relative path from source directory
                try:
                    rel_path = item.relative_to(source_path)
                    dest_file = dest_path / rel_path
                    
                    # Create parent directories
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Copy file with metadata
                    shutil.copy2(str(item), str(dest_file))
                    copied_files.append(str(rel_path))
                    
                except Exception as e:
                    print(f"Error copying file {item}: {e}")
        
        print(f"Successfully copied {len(copied_files)} files.")
        return len(copied_files) > 0
    except Exception as e:
        print(f"Error during file copying: {e}")
        import traceback
        traceback.print_exc()
        return False


def cleanup(zip_path, folder_path):
    """
    Removes temporary files and directories created during installation.
    
    Args:
        zip_path: Path to the ZIP file to remove
        folder_path: Path to the extracted folder to remove
    
    Returns:
        bool: True if cleanup was successful, False otherwise
    """
    print("Cleaning up temporary files...")
    
    try:
        # Delete zip file
        if os.path.exists(zip_path):
            os.remove(zip_path)
            print(f"Deleted: {zip_path}")
        
        # Delete extracted folder
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
            print(f"Deleted: {folder_path}")
            
        return True
    except Exception as e:
        print(f"Error during cleanup: {e}")
        return False


def install_tool(name, url_func, current_dir):
    """
    Generic function to install a tool (QuickBMS or vgmstream).
    
    Handles the download, extraction, copying, and cleanup process for
    a specific tool based on the provided parameters.
    
    Args:
        name: Name of the tool being installed
        url_func: Function to get the download URL for the tool
        current_dir: Current working directory
    
    Returns:
        bool: True if installation was successful, False otherwise
    """
    print(f"\n=== Installing {name} ===")
    
    # Get the appropriate download URL and filename
    download_url, filename = url_func()
    if not download_file(download_url, filename):
        print(f"Failed to download {name}. Installation aborted.")
        return False
    
    # Extract the zip file
    extract_dir = extract_zip(filename)
    if not extract_dir:
        print(f"Failed to extract {name}. Installation aborted.")
        return False
    
    # Copy all files to the current directory
    if not copy_files(extract_dir, current_dir):
        print(f"WARNING: Failed to copy {name} files.")
        success = False
    else:
        success = True
    
    # Clean up temporary files
    cleanup(filename, extract_dir)
    
    if success:
        print(f"{name} installation completed successfully!")
    else:
        print(f"{name} installation completed with warnings.")
    
    return success


def main():
    """
    Main function that orchestrates the entire installation process.
    
    Downloads and installs both QuickBMS and vgmstream based on the
    detected operating system, handles requirements installation,
    and provides user feedback throughout the process.
    """
    print("=== Complete Audio Extraction Tools Installation Script ===")
    print(f"Detected Operating System: {platform.system()} {platform.release()}")
    
    # Get the current directory where files will be installed
    current_dir = os.getcwd()
    print(f"Installing to directory: {current_dir}")
    
    # Step 1: Handle requirements.txt
    req_file = fetch_requirements_file()
    install_requirements(req_file)
    
    # Step 2: Install QuickBMS
    quickbms_success = install_tool("QuickBMS", get_latest_quickbms_url, current_dir)
    
    # Step 3: Install vgmstream
    vgmstream_success = install_tool("vgmstream", get_latest_vgmstream_url, current_dir)
    
    # Final status report
    print("\n=== Installation Summary ===")
    print(f"QuickBMS: {'SUCCESS' if quickbms_success else 'WARNING'}")
    print(f"vgmstream: {'SUCCESS' if vgmstream_success else 'WARNING'}")
    
    if quickbms_success and vgmstream_success:
        print("\n✓ All tools have been successfully installed!")
    else:
        print("\n⚠ Installation completed with warnings. Some tools may not function correctly.")
    
    print("\nYou can now use these tools to extract and convert audio files from games.")


if __name__ == "__main__":
    main()