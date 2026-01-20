"""
CLI entry point for building Docker images from model ZIP files.
"""

import argparse
from app.builder import build_image_from_zip


def main():
    parser = argparse.ArgumentParser(
        description="Build Docker image from model ZIP"
    )

    parser.add_argument(
        "--zip",
        required=True,
        help="Path to model ZIP file"
    )

    parser.add_argument(
        "--tag",
        required=True,
        help="Docker image tag (e.g. my-model:v1)"
    )

    args = parser.parse_args()

    image_id = build_image_from_zip(
        zip_path=args.zip,
        image_tag=args.tag
    )

    print("\n🎉 BUILD COMPLETE")
    print("Image ID:", image_id)
    print("Tag:", args.tag)


if __name__ == "__main__":
    main()
