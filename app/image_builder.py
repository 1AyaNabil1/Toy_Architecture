"""
Step 3.5 - Commit the Container

Goal: Turn container → image (build an image without Dockerfile)
Reference: https://docker-py.readthedocs.io/en/stable/images.html#docker.models.images.ImageCollection.commit
"""

import docker

def main():
    # Initialize Docker client
    client = docker.from_env()
    
    # Get the most recent container
    containers = client.containers.list(all=True, limit=1)
    
    if not containers:
        print("❌ No container found. Run builder.py first to create one.")
        return
    
    container = containers[0]
    print(f"📦 Using container: {container.id[:12]}")
    
    # Commit the container to create a new image
    image_name = "my-python-model"
    image_tag = "v1.0"
    
    print(f"\n📸 Committing container to image: {image_name}:{image_tag}")
    
    # Create the image from the container
    new_image = container.commit(
        repository=image_name,
        tag=image_tag,
        message="Built without Dockerfile - contains inference.py in /app"
    )
    
    print(f"✅ Image created: {new_image.tags[0] if new_image.tags else new_image.id[:12]}")
    print(f"   Image ID: {new_image.id[:12]}")
    
    # List all images to verify
    print("\n📋 Verifying image exists:")
    images = client.images.list(name=image_name)
    for img in images:
        print(f"   - {img.tags if img.tags else img.id[:12]}")
    
    print("\n🎉 YOU WON AGAIN! Successfully built an image without Dockerfile!")
    print("\n🏆 MILESTONE ACHIEVED:")
    print("   ✓ Container created")
    print("   ✓ Files copied via TAR")
    print("   ✓ Commands executed")
    print("   ✓ Image committed")
    print(f"\n💡 You can now run: docker run {image_name}:{image_tag} python /app/inference.py")

if __name__ == "__main__":
    main()
