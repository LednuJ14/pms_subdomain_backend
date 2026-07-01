"""
Cloudinary upload helpers
"""
import cloudinary
import cloudinary.uploader
from flask import current_app

def upload_to_cloudinary(file, folder="uploads", resource_type="auto"):
    """
    Upload a file to Cloudinary.
    
    Args:
        file: The file object from request.files
        folder: The Cloudinary folder to store the file
        resource_type: The type of resource ('image', 'video', 'raw', 'auto')
        
    Returns:
        tuple: (success, secure_url, error_message)
    """
    try:
        if not file or file.filename == '':
            return False, None, "No file selected"
            
        result = cloudinary.uploader.upload(
            file,
            folder=folder,
            resource_type=resource_type
        )
        
        return True, result.get("secure_url"), None
        
    except Exception as e:
        current_app.logger.error(f'Error uploading to Cloudinary: {str(e)}', exc_info=True)
        return False, None, f"Failed to upload to Cloudinary: {str(e)}"
