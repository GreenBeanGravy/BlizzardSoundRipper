#!/usr/bin/env python3
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

def get_latest_quickbms_url():
    """Get the download URL for the latest QuickBMS release based on OS."""
    os_type = platform.system().lower()
    
    if os_type == "windows":
        filename = "quickbms_win.zip"
    elif os_type == "darwin":
        filename = "quickbms_macosx.zip"
    elif os_type == "linux":
        filename = "quickbms_linux.zip"
    else:
        raise SystemExit(f"Unsupported operating system: {os_type}")
    
    download_url = f"https://github.com/LittleBigBug/QuickBMS/releases/latest/download/{filename}"
    return download_url, filename

def get_latest_vgmstream_url():
    """Get the download URL for the latest vgmstream release based on OS."""
    os_type = platform.system().lower()
    
    if os_type == "windows":
        filename = "vgmstream-win.zip"  # Using regular Windows build, not the 64-bit one
    elif os_type == "darwin":
        filename = "vgmstream-mac-cli.zip"
    elif os_type == "linux":
        filename = "vgmstream-linux-cli.zip"
    else:
        raise SystemExit(f"Unsupported operating system: {os_type}")
    
    download_url = f"https://github.com/vgmstream/vgmstream/releases/latest/download/{filename}"
    return download_url, filename

def install_requirements():
    """Install requirements from requirements.txt file."""
    print("Installing requirements...")
    
    req_file = Path("requirements.txt")
    if not req_file.exists():
        print("Warning: requirements.txt not found. Skipping requirements installation.")
        return
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("Requirements installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error installing requirements: {e}")
        sys.exit(1)

def download_file(url, filename):
    """Download file with progress bar."""
    print(f"Downloading {filename}...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024  # 1 Kibibyte
        
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
    """Extract zip file and return the extraction folder path."""
    print(f"Extracting {zip_path}...")
    
    # Extract the base filename without extension
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
    """Copy all files from source directory to destination directory."""
    print(f"Copying files from {source_dir} to {dest_dir}...")
    
    source_path = Path(source_dir)
    dest_path = Path(dest_dir)
    
    try:
        # Create a list to store all copied files for reporting
        copied_files = []
        
        for item in source_path.glob('**/*'):
            if item.is_file():
                # Get the relative path from source_dir
                rel_path = item.relative_to(source_path)
                # Create the destination path
                dest_file = dest_path / rel_path
                
                # Create parent directories if they don't exist
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                
                # Copy the file
                shutil.copy2(item, dest_file)
                copied_files.append(str(rel_path))
        
        print(f"Successfully copied {len(copied_files)} files.")
        return True
    except Exception as e:
        print(f"Error copying files: {e}")
        return False

def cleanup(zip_path, folder_path):
    """Delete the zip file and extracted folder."""
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

def main():
    print("=== QuickBMS and vgmstream Installation Script ===")
    
    # Step 1: Install requirements
    install_requirements()
    
    # ===== QuickBMS Installation =====
    print("\n=== Installing QuickBMS ===")
    
    # Step 2: Download the appropriate QuickBMS zip file
    download_url, filename = get_latest_quickbms_url()
    if not download_file(download_url, filename):
        sys.exit(1)
    
    # Step 3: Extract the zip file
    extract_dir = extract_zip(filename)
    if not extract_dir:
        sys.exit(1)
    
    # Step 4: Copy all files to the current directory
    if not copy_files(extract_dir):
        sys.exit(1)
    
    # Step 5: Cleanup
    cleanup(filename, extract_dir)
    
    print("QuickBMS has been successfully installed!")
    
    # ===== vgmstream Installation =====
    print("\n=== Installing vgmstream ===")
    
    # Step 6: Download the appropriate vgmstream zip file
    download_url, filename = get_latest_vgmstream_url()
    if not download_file(download_url, filename):
        sys.exit(1)
    
    # Step 7: Extract the zip file
    extract_dir = extract_zip(filename)
    if not extract_dir:
        sys.exit(1)
    
    # Step 8: Copy all files to the current directory
    if not copy_files(extract_dir):
        sys.exit(1)
    
    # Step 9: Cleanup
    cleanup(filename, extract_dir)
    
    print("vgmstream has been successfully installed!")
    print("\n=== Installation Complete ===")
    print("QuickBMS and vgmstream have been successfully installed!")

if __name__ == "__main__":
    main()
