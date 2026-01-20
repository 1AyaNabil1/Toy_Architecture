"""
Core Docker image builder module.

Builds Docker images from ZIP files using Docker Engine API
without requiring Dockerfile or Docker Desktop.
"""

import docker
import io
import os
import tarfile
import zipfile
import tempfile
import shutil


def build_image_from_zip(zip_path: str, image_tag: str) -> str:
    """
    Build a Docker image from a ZIP file using Docker Engine API
    without Dockerfile or Docker Desktop.
    
    Args:
        zip_path: Path to the ZIP file containing model files
        image_tag: Tag for the resulting Docker image (e.g., "my-model:v1")
    
    Returns:
        str: Image ID of the created Docker image
    """
    client = docker.from_env()
    container = None
    temp_dir = None
    
    try:
        # Step 1: Unzip to temporary directory
        temp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Step 2: Pull base image and create container
        client.images.pull('python:3.10-slim')
        
        container = client.containers.run(
            'python:3.10-slim',
            command='sleep infinity',
            detach=True
        )
        
        # Step 3: Create /app directory in container
        container.exec_run("mkdir -p /app")
        
        # Step 4: Copy files from ZIP to container
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                # Skip __MACOSX and hidden files
                if '__MACOSX' in root or file.startswith('.'):
                    continue
                
                local_file_path = os.path.join(root, file)
                
                # Read the file
                with open(local_file_path, 'rb') as f:
                    file_data = f.read()
                
                # Create in-memory TAR archive
                tar_stream = io.BytesIO()
                tar = tarfile.open(fileobj=tar_stream, mode='w')
                
                tarinfo = tarfile.TarInfo(name=file)
                tarinfo.size = len(file_data)
                tarinfo.mode = 0o755
                
                tar.addfile(tarinfo, io.BytesIO(file_data))
                tar.close()
                
                tar_stream.seek(0)
                tar_data = tar_stream.read()
                
                # Copy to container
                container.put_archive("/app", tar_data)
        
        # Step 5: Execute commands (install dependencies if requirements.txt exists)
        exit_code, output = container.exec_run("ls /app/requirements.txt")
        if exit_code == 0:
            container.exec_run("pip install -r /app/requirements.txt")
        
        # Step 6: Commit container to image
        repository, tag = image_tag.split(':') if ':' in image_tag else (image_tag, 'latest')
        
        new_image = container.commit(
            repository=repository,
            tag=tag,
            message="Built from ZIP without Dockerfile"
        )
        
        image_id = new_image.id
        
        # Step 7: Cleanup
        container.stop()
        container.remove()
        
        if temp_dir:
            shutil.rmtree(temp_dir)
        
        return image_id
        
    except Exception as e:
        # Cleanup on error
        if container:
            try:
                container.stop()
                container.remove()
            except:
                pass
        
        if temp_dir:
            shutil.rmtree(temp_dir)
        
        raise e
