import logging
from typing import List, Optional
from sqlmodel import Session
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.models.knowledge_base import KnowledgeBaseEntry
from app.services.kb_rag import embed_single, ensure_entry_embedding

logger = logging.getLogger(__name__)

def process_text_content(
    session: Session,
    content: str,
    source_name: str,
    source_type: str = "document",
    category: Optional[str] = None,
    storage_path: Optional[str] = None,
    chunk_size: int = 500,
    chunk_overlap: int = 100
) -> List[KnowledgeBaseEntry]:
    """
    Chunks text content, generates embeddings, and saves to the knowledge base.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        is_separator_regex=False,
    )
    
    chunks = text_splitter.split_text(content)
    logger.info(f"Ingestion: Split {source_name} into {len(chunks)} chunks.")
    
    entries = []
    for i, chunk in enumerate(chunks):
        entry = KnowledgeBaseEntry(
            question=f"Excerpt from {source_name} (Part {i+1})",
            answer=chunk,
            category=category,
            source_type=source_type,
            source_name=source_name,
            storage_path=storage_path,
            chunk_index=i
        )
        session.add(entry)
        entries.append(entry)
    
    # Commit all chunks once for performance
    try:
        session.commit()
        for e in entries:
            session.refresh(e)
    except Exception as e:
        logger.error(f"Ingestion Database Error while committing chunks: {e}")
        session.rollback()
        raise ValueError(f"Failed to save chunks to database: {e}")

    # Generate embeddings (one by one for individual error handling)
    for entry in entries:
        try:
            ensure_entry_embedding(session, entry)
        except Exception as e:
            logger.error(f"Ingestion Embedding Error: Failed for chunk {entry.chunk_index} of {source_name}: {e}")
            
    return entries

def extract_text_from_pdf(file_path: str) -> str:
    """Extracts raw text from a PDF file."""
    text = ""
    try:
        import PyPDF2

        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except ModuleNotFoundError as e:
        logger.error(f"PDF Extraction Error: missing dependency: {e}")
        raise ValueError(
            "PDF support is unavailable because PyPDF2 is not installed."
        ) from e
    except Exception as e:
        logger.error(f"PDF Extraction Error: {e}")
        raise ValueError(f"Could not extract text from PDF: {e}")
    return text
