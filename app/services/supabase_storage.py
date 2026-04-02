import logging
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)

def get_supabase_client() -> Client:
    """Initializes the Supabase client using service role key for full access."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

def upload_to_supabase(file_content: bytes, destination_path: str, content_type: str = "application/pdf") -> str:
    """
    Uploads bytes to the configured Supabase bucket.
    Returns the storage path (e.g., 'user_id/timestamp-file.pdf').
    """
    supabase = get_supabase_client()
    bucket = settings.SUPABASE_KB_BUCKET or "MyBucket"
    
    try:
        # Note: Supabase's python client 'upload' method handles bytes
        res = supabase.storage.from_(bucket).upload(
            path=destination_path,
            file=file_content,
            file_options={"content-type": content_type, "upsert": "true"}
        )
        logger.info(f"Storage: Successfully uploaded to {bucket}/{destination_path}")
        return destination_path
    except Exception as e:
        logger.error(f"Storage Error: Failed to upload to {bucket}/{destination_path}: {e}")
        raise ValueError(f"Failed to upload file to storage: {e}")
def delete_from_supabase(storage_path: str):
    """Deletes a file from the configured Supabase bucket."""
    supabase = get_supabase_client()
    bucket = settings.SUPABASE_KB_BUCKET or "MyBucket"
    
    try:
        supabase.storage.from_(bucket).remove([storage_path])
        logger.info(f"Storage: Successfully deleted {bucket}/{storage_path}")
    except Exception as e:
        logger.error(f"Storage Error: Failed to delete {bucket}/{storage_path}: {e}")
        # We don't raise error here to avoid blocking DB cleanup if storage is already gone
