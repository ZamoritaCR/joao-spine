"""Learning tools — joao_learn_youtube/pdf/excel/url/docx.

All tools feed extracted content to JOAO's brain (session log + Claude analysis).
For file-based tools (pdf, excel, docx), accepts a local file path on the server.
"""

from __future__ import annotations

from pathlib import Path

from services import content_intelligence


async def joao_learn_youtube(url: str) -> str:
    """Extract a YouTube video transcript, analyze it with Claude, feed to JOAO's brain.

    Fetches the auto-transcript (falls back to Whisper if none available).
    Claude extracts every insight and maps it to active TAOP/JOAO projects.
    Result is appended to JOAO_SESSION_LOG.md.

    Args:
        url: Full YouTube URL (youtube.com or youtu.be)
    """
    try:
        result = await content_intelligence.handle_youtube(url)
        analysis = result.get("analysis", "")
        video_id = result.get("video_id", "?")
        return (
            f"YouTube learned: {video_id}\n"
            f"Transcript saved: {result.get('transcript_path', '?')}\n\n"
            f"Analysis:\n{analysis}"
        )
    except Exception as e:
        return f"Error processing YouTube URL: {e}"


async def joao_learn_pdf(file_path: str) -> str:
    """Extract a PDF, generate an HTML intelligence report, feed insights to JOAO's brain.

    Reads the PDF from the local filesystem, extracts all text, generates a
    beautiful HTML report, and appends learning summary to JOAO_SESSION_LOG.md.

    Args:
        file_path: Absolute path to the PDF file on the server
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found at {file_path}"
    if path.suffix.lower() != ".pdf":
        return f"Error: expected a .pdf file, got {path.suffix}"

    try:
        data = path.read_bytes()
        result = await content_intelligence.handle_pdf(path.name, data)
        return (
            f"PDF learned: {result.get('filename')}\n"
            f"HTML report: {result.get('html_url')}\n"
            f"Raw text: {result.get('raw_path')}\n\n"
            f"Learning summary:\n{result.get('learning_summary', '')}"
        )
    except Exception as e:
        return f"Error processing PDF: {e}"


async def joao_learn_excel(file_path: str) -> str:
    """Parse an Excel or CSV file, run full Dr. Data BI analysis, generate HTML dashboard.

    Reads the file from the local filesystem. Dr. Data analyzes patterns, anomalies,
    and business insights. Generates a Fortune 500-quality HTML dashboard.
    Insights appended to JOAO_SESSION_LOG.md.

    Args:
        file_path: Absolute path to the .xlsx, .xls, or .csv file on the server
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found at {file_path}"
    if path.suffix.lower() not in (".xlsx", ".xls", ".csv"):
        return f"Error: expected .xlsx, .xls, or .csv — got {path.suffix}"

    try:
        data = path.read_bytes()
        result = await content_intelligence.handle_spreadsheet(path.name, data)
        return (
            f"Spreadsheet analyzed: {result.get('filename')}\n"
            f"HTML dashboard: {result.get('html_url')}\n"
            f"Raw profile: {result.get('raw_path')}\n\n"
            f"Key insights:\n{result.get('insights', '')}"
        )
    except Exception as e:
        return f"Error processing spreadsheet: {e}"


async def joao_learn_url(url: str) -> str:
    """Extract content from any URL, analyze it with Claude, feed insights to JOAO's brain.

    Fetches the page, strips navigation/ads, extracts clean text via BeautifulSoup
    (falls back to trafilatura). Claude maps every insight to TAOP/JOAO projects.
    Result appended to JOAO_SESSION_LOG.md.

    Args:
        url: Full URL to fetch and analyze (http or https)
    """
    try:
        result = await content_intelligence.handle_web_link(url)
        analysis = result.get("analysis", "")
        return (
            f"URL learned: {result.get('url')}\n"
            f"Raw saved: {result.get('raw_path')}\n\n"
            f"Analysis:\n{analysis}"
        )
    except Exception as e:
        return f"Error processing URL: {e}"


async def joao_learn_docx(file_path: str) -> str:
    """Extract a Word document, generate a beautiful HTML version, feed insights to JOAO's brain.

    Reads the .docx from the local filesystem, preserves headings and tables,
    generates a visually stunning HTML version. Insights appended to JOAO_SESSION_LOG.md.

    Args:
        file_path: Absolute path to the .docx file on the server
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found at {file_path}"
    if path.suffix.lower() != ".docx":
        return f"Error: expected a .docx file, got {path.suffix}"

    try:
        data = path.read_bytes()
        result = await content_intelligence.handle_docx(path.name, data)
        return (
            f"DOCX learned: {result.get('filename')}\n"
            f"HTML version: {result.get('html_url')}\n"
            f"Raw text: {result.get('raw_path')}\n\n"
            f"Key insights:\n{result.get('insights', '')}"
        )
    except Exception as e:
        return f"Error processing DOCX: {e}"
