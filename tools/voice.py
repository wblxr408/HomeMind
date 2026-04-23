"""
Voice recognition tools
Currently uses browser-side Web Speech API
Server-side faster-whisper support planned for future
"""
import logging

logger = logging.getLogger(__name__)

class VoiceRecognizer:
    """Voice recognition interface"""
    
    def __init__(self):
        self.mode = "browser"  # browser or server
        logger.info("VoiceRecognizer initialized (browser mode)")
    
    def is_available(self) -> bool:
        """Check if voice recognition is available"""
        # In browser, this is checked client-side via Web Speech API
        # Server-side always returns True for now
        return True
    
    def recognize(self, audio_data: bytes = None) -> str:
        """
        Recognize speech from audio data
        Currently not implemented server-side - browser handles this
        """
        raise NotImplementedError("Server-side voice recognition not yet implemented. Use browser Web Speech API.")